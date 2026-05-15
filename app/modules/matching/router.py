"""F2 匹配 REST API."""
import json
import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.matching.hashing import compute_competency_hash, compute_weights_hash
from app.modules.matching.weights import get_effective_weights
from app.modules.matching.models import MatchingResult
from app.modules.matching.schemas import (
    EvidenceItem,
    MatchingResultResponse, MatchingResultListResponse,
    ScoreRequest, RecomputeRequest, RecomputeStatus,
)
from app.modules.matching.service import MatchingService
from app.modules.resume.models import Resume
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/matching", tags=["matching"])


def _require_matching_enabled():
    if not getattr(settings, "matching_enabled", True):
        raise HTTPException(status_code=503, detail="matching feature disabled")


class _NormalizeError(Exception):
    """promote 失败的内部错误，区分 404（不存在）和 500（服务端错误）。"""
    pass


def _normalize_resume_id(db: Session, input_id: int, user_id: int) -> int | None:
    """翻译 candidate.id → Resume.id（按需 promote）；不存在返 None；
    BUG-072 修复：promote 抛异常时抛 _NormalizeError，调用方区分 500 vs 404。"""
    from app.modules.im_intake.candidate_model import IntakeCandidate
    cand = db.query(IntakeCandidate).filter_by(id=input_id, user_id=user_id).first()
    if cand:
        if cand.promoted_resume_id:
            existing = db.query(Resume).filter_by(id=cand.promoted_resume_id).first()
            if existing and existing.user_id == user_id:
                return existing.id
        try:
            from app.modules.im_intake.promote import promote_to_resume
            r = promote_to_resume(db, cand, user_id=user_id)
            db.commit()
            return r.id
        except Exception as _e:
            db.rollback()
            raise _NormalizeError(str(_e))
    resume = db.query(Resume).filter_by(id=input_id, user_id=user_id).first()
    if resume:
        return resume.id
    return None


from app.modules.matching.hard_filter import hard_filter_resume_ids as _hard_filter_resume_ids


def _purge_outside_hard_filter(db: Session, job_id: int, keep_resume_ids: set[int]) -> int:
    """删本 job 下 resume_id 不在 keep_resume_ids 的所有 matching_results.
    返回删除行数, 供调用方记日志。
    keep_resume_ids 为空集合时 → 删本 job 全部行 (硬筛 0 通过场景)。
    """
    q = db.query(MatchingResult).filter(MatchingResult.job_id == job_id)
    if keep_resume_ids:
        q = q.filter(~MatchingResult.resume_id.in_(keep_resume_ids))
    deleted = q.delete(synchronize_session=False)
    db.commit()
    return deleted


def _resolve_or_404(db: Session, input_id: int, user_id: int) -> int:
    """统一鉴权 + 翻译。BUG-056 修复：他人资源与不存在均返 404。"""
    try:
        rid = _normalize_resume_id(db, input_id, user_id)
    except _NormalizeError as e:
        raise HTTPException(status_code=500, detail="无法落库简历，请稍后重试")
    if rid is None:
        raise HTTPException(status_code=404, detail="简历不存在")
    return rid


@router.post("/score", response_model=MatchingResultResponse)
async def score_pair(req: ScoreRequest, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    _require_matching_enabled()
    real_resume_id = _resolve_or_404(db, req.resume_id, user_id)
    job = db.query(Job).filter_by(id=req.job_id).first()
    if not job or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="岗位不存在")
    service = MatchingService(db)
    try:
        return await service.score_pair(real_resume_id, req.job_id, triggered_by="T4")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/results", response_model=MatchingResultListResponse)
def list_results(
    job_id: Optional[int] = None,
    resume_id: Optional[int] = None,
    tag: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    _require_matching_enabled()
    if not job_id and not resume_id:
        raise HTTPException(status_code=400, detail="need job_id or resume_id")

    # BUG-097: live/dead filter 推下到 SQL EXISTS, 不再全表 q.all() 后内存过滤;
    # tag filter 仍在 in-memory 因 SQLite JSON 字段难下推, 但只在 tag 非空时才走 fallback。
    from app.modules.im_intake.candidate_model import IntakeCandidate
    q = db.query(MatchingResult)
    if job_id:
        job = db.query(Job).filter_by(id=job_id).first()
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="岗位不存在")
        q = q.filter(MatchingResult.job_id == job_id)
    if resume_id:
        resume_id = _resolve_or_404(db, resume_id, user_id)
        q = q.filter(MatchingResult.resume_id == resume_id)

    # SQL EXISTS: 仅保留指向 status!=rejected Resume 的 matching_results 行
    live_resume_subq = (
        db.query(Resume.id).filter(
            Resume.id == MatchingResult.resume_id,
            Resume.status != "rejected",
        ).exists()
    )
    q = q.filter(live_resume_subq)

    # SQL NOT EXISTS: 排除指向 abandoned/timed_out candidate 的 Resume
    dead_cand_subq = (
        db.query(IntakeCandidate.id).filter(
            IntakeCandidate.promoted_resume_id == MatchingResult.resume_id,
            IntakeCandidate.intake_status.in_(["abandoned", "timed_out"]),
        ).exists()
    )
    q = q.filter(~dead_cand_subq)

    # SQL EXISTS: Job 仍存在
    live_job_subq = (
        db.query(Job.id).filter(Job.id == MatchingResult.job_id).exists()
    )
    q = q.filter(live_job_subq)

    q = q.order_by(MatchingResult.total_score.desc())

    if tag:
        # tag 走 in-memory fallback (JSON 字段下推到 SQL 复杂);
        # 单 job 内通常 ≤ 几百行, 影响有限。
        all_rows = [r for r in q.all() if tag in json.loads(r.tags or "[]")]
        total = len(all_rows)
        start = (page - 1) * page_size
        rows = all_rows[start: start + page_size]
    else:
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()

    # 批量预取 resume/job 信息 + 当前 hash
    resume_ids = {r.resume_id for r in rows}
    job_ids = {r.job_id for r in rows}
    resumes = {r.id: r for r in db.query(Resume).filter(Resume.id.in_(resume_ids)).all()}
    jobs = {j.id: j for j in db.query(Job).filter(Job.id.in_(job_ids)).all()}

    # spec 0429-D: 反查 resume_id -> candidate_id (用 promoted_resume_id)
    # P0-b: 死候选人 (abandoned/timed_out) 不参与反查, 防 list 注入"诈尸" candidate_id
    candidate_id_by_resume: dict[int, int] = {}
    if resume_ids:
        from app.modules.im_intake.candidate_model import IntakeCandidate
        for cid, rid in db.query(
            IntakeCandidate.id, IntakeCandidate.promoted_resume_id,
        ).filter(
            IntakeCandidate.promoted_resume_id.in_(resume_ids),
            IntakeCandidate.user_id == user_id,
            ~IntakeCandidate.intake_status.in_(["abandoned", "timed_out"]),
        ).all():
            candidate_id_by_resume[rid] = cid

    # spec 0429-D: 注入决策表 job_action (覆盖 matching_results.job_action 旧字段)
    from app.modules.matching.decision_service import get_decisions_map_for_job
    decisions_by_job: dict[int, dict[int, str]] = {}
    for jid in job_ids:
        decisions_by_job[jid] = get_decisions_map_for_job(db, user_id, jid)

    # 按 job 分组算 current hash — 每个 job 用自己的 effective weights
    current_hashes = {}   # job_id → (competency_hash, weights_hash)
    for jid, j in jobs.items():
        current_hashes[jid] = (
            compute_competency_hash(j.competency_model or {}),
            compute_weights_hash(get_effective_weights(j)),
        )

    items = []
    for r in rows:
        resume = resumes.get(r.resume_id)
        job = jobs.get(r.job_id)
        current_c, current_w = current_hashes.get(r.job_id, (r.competency_hash, r.weights_hash))
        evidence_dict = json.loads(r.evidence or "{}")
        cand_id = candidate_id_by_resume.get(r.resume_id)
        decision_action = decisions_by_job.get(r.job_id, {}).get(cand_id) if cand_id else None
        items.append(MatchingResultResponse(
            id=r.id, resume_id=r.resume_id,
            resume_name=resume.name if resume else "",
            job_id=r.job_id, job_title=job.title if job else "",
            total_score=r.total_score, skill_score=r.skill_score,
            experience_score=r.experience_score, seniority_score=r.seniority_score,
            education_score=r.education_score, industry_score=r.industry_score,
            hard_gate_passed=bool(r.hard_gate_passed),
            missing_must_haves=json.loads(r.missing_must_haves or "[]"),
            evidence={k: [EvidenceItem(**e) for e in v] for k, v in evidence_dict.items()},
            tags=json.loads(r.tags or "[]"),
            job_action=decision_action if decision_action is not None else r.job_action,
            candidate_id=cand_id,
            stale=(r.competency_hash != current_c or r.weights_hash != current_w),
            scored_at=r.scored_at,
        ))
    return MatchingResultListResponse(
        total=total, page=page, page_size=page_size, items=items,
    )


from fastapi import BackgroundTasks
from pydantic import BaseModel as _PydanticBaseModel
from app.modules.matching.service import (
    _new_task, _get_task, _prune_stale_tasks,
    recompute_job_with_fresh_session, recompute_resume_with_fresh_session,
)


class _ActionBody(_PydanticBaseModel):
    action: str | None = None  # 'passed' / 'rejected' / null


@router.patch(
    "/results/{result_id}/action",
    deprecated=True,
    summary="[DEPRECATED] 旧人工决策端点 — 请改用 PATCH /api/jobs/{job_id}/candidates/{candidate_id}/decision",
)
def set_action(result_id: int, body: _ActionBody, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """spec 0429-D cleanup: 兼容旧前端缓存。新代码走 decision_router。

    将在后续 spec 0429-D-cleanup 删除 matching_results.job_action 列前下线。
    每次命中记 INFO 日志便于评估流量。
    """
    logger.info("legacy set_action endpoint hit: result_id=%s", result_id)
    _require_matching_enabled()
    row = db.query(MatchingResult).filter_by(id=result_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="matching result not found")
    # BUG-056 修复：他人资源与不存在均返 404，不暴露存在性
    owner_resume = db.query(Resume).filter_by(id=row.resume_id).first()
    if not owner_resume or owner_resume.user_id != user_id:
        raise HTTPException(status_code=404, detail="matching result not found")
    if body.action not in (None, "passed", "rejected"):
        raise HTTPException(status_code=400, detail="action must be passed/rejected/null")
    # BUG-098: 决策表写在 row.job_action 之前, 失败时整体不 commit, 避免两表分裂.
    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.matching.decision_service import set_decision, DecisionError
    cand = db.query(IntakeCandidate).filter_by(
        promoted_resume_id=row.resume_id, user_id=user_id,
    ).first()
    if cand:
        try:
            set_decision(
                db, user_id=user_id, job_id=row.job_id,
                candidate_id=cand.id, action=body.action,
            )
        except DecisionError as _e:
            logger.warning(
                "decision sync failed: code=%s job_id=%s candidate_id=%s",
                _e.code, row.job_id, cand.id,
            )
            # 决策表语义不被旧 row.job_action 替代, 但仍写一份兼容旧前端
    row.job_action = body.action
    db.commit()
    return {"id": row.id, "job_action": row.job_action}


@router.get("/passed-resumes/{job_id}")
def list_passed_for_job(
    job_id: int,
    action: Optional[str] = None,
    show_all: bool = False,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """岗位匹配候选人: 四项齐全 ∩ 学历门槛 ∩ 院校等级门槛 (PR4)。

    spec 0429-D: 加 ?action=passed|rejected|undecided 过滤; 缺省返全部 (含 job_action 字段)。
    spec 2026-05-15 Round 2: 默认 strict 模式(只返本岗位绑定的候选人);
      传 ?show_all=true 退回旧行为,返所有过硬筛的(用于跨岗位 cross-fit / 调试)。
    """
    job = db.query(Job).filter_by(id=job_id).first()
    if not job or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if action is not None and action not in ("passed", "rejected", "undecided"):
        raise HTTPException(status_code=400, detail="action 取值非法")
    from app.modules.resume.intake_view_service import list_matched_for_job
    return list_matched_for_job(
        db, user_id=user_id, job_id=job_id, action_filter=action,
        strict=not show_all,
    )


@router.post("/recompute")
async def post_recompute(
    req: RecomputeRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    _require_matching_enabled()
    _prune_stale_tasks()
    if not req.job_id and not req.resume_id:
        raise HTTPException(status_code=400, detail="need job_id or resume_id")

    if req.job_id:
        # BUG-006 / 限定 job 归属
        job = db.query(Job).filter_by(id=req.job_id).first()
        if not job or job.user_id != user_id:
            raise HTTPException(status_code=404, detail="岗位不存在")
        # 硬筛串联 (五维能力筛选通道): 用 list_matched_for_job 的 candidate ID 集合
        # 翻译成 Resume.id, 仅对硬筛通过的人跑 F2, 避免给被硬筛拒掉的人浪费 LLM token.
        pre_filter_resume_ids = _hard_filter_resume_ids(db, user_id, req.job_id)
        # 清理: 本 job 之下、不在硬筛通过集合内的旧 matching_results 行直接删,
        # 保证 "再次分析" 后列表与硬筛通过名单严格一致 (旧人不残留)。
        _purge_outside_hard_filter(db, req.job_id, pre_filter_resume_ids)
        total = len(pre_filter_resume_ids)
        task_id = _new_task(total)
        background.add_task(
            recompute_job_with_fresh_session,
            req.job_id, task_id, user_id,
            pre_filter_resume_ids=pre_filter_resume_ids,
        )
        return {"task_id": task_id, "total": total}

    real_resume_id = _resolve_or_404(db, req.resume_id, user_id)
    total = db.query(Job).filter(
        Job.is_active == True,
        Job.competency_model_status == "approved",
    ).count()
    task_id = _new_task(total)
    background.add_task(recompute_resume_with_fresh_session, real_resume_id, task_id)
    return {"task_id": task_id, "total": total}


@router.get("/recompute/status/{task_id}", response_model=RecomputeStatus)
def get_recompute_status(task_id: str):
    task = _get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return RecomputeStatus(
        task_id=task["task_id"], total=task["total"],
        completed=task["completed"], failed=task["failed"],
        running=task["running"], current=task.get("current", ""),
    )
