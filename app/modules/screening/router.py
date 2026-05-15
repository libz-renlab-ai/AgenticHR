"""岗位管理与筛选 API 路由"""
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.screening.service import ScreeningService
from app.modules.screening.schemas import (
    JobCreate, JobUpdate, JobResponse, JobListResponse, ScreeningResponse,
)
from app.modules.scheduling.models import Interview
from app.core.competency.extractor import extract_competency, ExtractionFailedError
from app.core.hitl.service import HitlService
from app.modules.screening.competency_service import apply_competency_to_job
from app.core.llm.parsing import extract_json
from app.core.llm.provider import LLMProvider, LLMError

router = APIRouter()


def get_llm_provider() -> LLMProvider:
    """创建 LLMProvider 实例（在 router 模块中定义以便测试 mock）."""
    return LLMProvider()


class _ParseJdBody(BaseModel):
    jd_text: str

    @validator('jd_text')
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError('jd_text must not be blank')
        return v


_PARSE_JD_SYSTEM = """你是 HR 专家，从 JD 文本提取招聘基本字段，严格输出 JSON，不要 markdown 包装，不要多余字段。

schema:
{
  "title": "岗位名称（字符串）",
  "department": "部门（字符串，JD 未提及则空字符串）",
  "education_min": "最低学历：大专|本科|硕士|博士（JD 未提及则空字符串）",
  "work_years_min": 最少年限（整数，JD 未提及则 0）,
  "work_years_max": 最多年限（整数，JD 未提及则 99），
  "salary_min": 最低月薪（整数元，JD 未提及则 0），
  "salary_max": 最高月薪（整数元，JD 未提及则 0），
  "required_skills": "必备技能，逗号分隔（字符串）",
  "soft_requirements": "软性要求自然语言（字符串）"
}

规则：
1. salary 统一转为月薪（元）；年薪则除以 12
2. 薪资范围如"20-40k"→ salary_min=20000, salary_max=40000
3. 只提取 JD 里明确写出的信息，不编造"""


@router.post("/jobs/parse-jd")
async def parse_jd_fields(body: _ParseJdBody):
    """从 JD 原文用 LLM 提取基本岗位字段，供前端表单预填。"""
    fallback = {
        "title": "", "department": "", "education_min": "",
        "work_years_min": 0, "work_years_max": 99,
        "salary_min": 0, "salary_max": 0,
        "required_skills": "", "soft_requirements": "",
        "jd_text": body.jd_text,
    }
    try:
        llm = get_llm_provider()
        raw = await llm.complete(
            messages=[
                {"role": "system", "content": _PARSE_JD_SYSTEM},
                {"role": "user", "content": body.jd_text},
            ],
            temperature=0.1,
        )
        parsed = extract_json(raw)
        # 合并 fallback（防止 LLM 少输出字段）
        result = {**fallback, **parsed, "jd_text": body.jd_text, "parse_success": True}
        # 确保数值类型正确
        for k in ("work_years_min", "work_years_max", "salary_min", "salary_max"):
            try:
                result[k] = int(result.get(k) or fallback[k])
            except (TypeError, ValueError):
                result[k] = fallback[k]
        return result
    except Exception as e:
        _log.warning("parse_jd_fields failed: %s", e, exc_info=True)
        return {**fallback, "parse_success": False}


def get_screening_service(db: Session = Depends(get_db)) -> ScreeningService:
    return ScreeningService(db)


@router.post("/jobs", response_model=JobResponse, status_code=201)
def create_job(
    data: JobCreate,
    service: ScreeningService = Depends(get_screening_service),
    user_id: int = Depends(get_current_user_id),
):
    job = service.create_job(data)
    job.user_id = user_id
    service.db.commit()
    service.db.refresh(job)
    return job


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    active_only: bool = False,
    service: ScreeningService = Depends(get_screening_service),
    user_id: int = Depends(get_current_user_id),
):
    return service.list_jobs(active_only=active_only, user_id=user_id)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    service: ScreeningService = Depends(get_screening_service),
    user_id: int = Depends(get_current_user_id),
):
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该岗位")
    return job


@router.patch("/jobs/{job_id}", response_model=JobResponse)
def update_job(
    job_id: int,
    data: JobUpdate,
    service: ScreeningService = Depends(get_screening_service),
    user_id: int = Depends(get_current_user_id),
):
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权修改该岗位")

    # 防御：JD 文本变了 → 旧能力模型已过时，重置 status 强制重抽
    # BUG-011: 在同一 db session 中完成重置，不开新 SessionLocal（避免两 session 竞态覆盖）
    new_jd = getattr(data, "jd_text", None)
    if new_jd is not None and new_jd.strip() and (job.jd_text or "").strip() != new_jd.strip():
        if job.competency_model_status in ("draft", "approved"):
            job.competency_model_status = "none"
            job.competency_model = None
            service.db.flush()

    updated = service.update_job(job_id, data)
    return updated


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    service: ScreeningService = Depends(get_screening_service),
    user_id: int = Depends(get_current_user_id),
):
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权删除该岗位")
    linked = db.query(Interview).filter(
        Interview.job_id == job_id,
        Interview.status != "cancelled",
    ).count()
    if linked > 0:
        raise HTTPException(
            status_code=409,
            detail=f"该岗位下有 {linked} 场待面试，请先处理后再删除"
        )
    # 级联清 cancelled Interview + 其 NotificationLog,
    # 否则 Interview.job_id FK (无 ondelete) 让 db.delete(job) 挂 IntegrityError
    from app.modules.notification.models import NotificationLog
    cancelled_iv_ids = [
        i for (i,) in db.query(Interview.id).filter(
            Interview.job_id == job_id,
            Interview.status == "cancelled",
        ).all()
    ]
    if cancelled_iv_ids:
        db.query(NotificationLog).filter(
            NotificationLog.interview_id.in_(cancelled_iv_ids)
        ).delete(synchronize_session=False)
        db.query(Interview).filter(
            Interview.id.in_(cancelled_iv_ids)
        ).delete(synchronize_session=False)
    # 级联清 F2 匹配结果（无 FK，需手动）
    try:
        from app.modules.matching.models import MatchingResult
        db.query(MatchingResult).filter(
            MatchingResult.job_id == job_id
        ).delete(synchronize_session=False)
    except Exception:
        pass
    # spec 0429-D: 级联清 job × candidate 决策
    try:
        from app.modules.matching.decision_model import JobCandidateDecision
        db.query(JobCandidateDecision).filter(
            JobCandidateDecision.job_id == job_id
        ).delete(synchronize_session=False)
    except Exception:
        pass
    db.commit()
    service.delete_job(job_id)


@router.post("/jobs/{job_id}/screen", response_model=ScreeningResponse)
def screen_resumes(
    job_id: int,
    resume_ids: list[int] | None = None,
    service: ScreeningService = Depends(get_screening_service),
    user_id: int = Depends(get_current_user_id),
):
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权操作该岗位")
    return service.screen_resumes(job_id, resume_ids, user_id=user_id)


class _ManualBody(BaseModel):
    flat_fields: dict


class _ExtractBody(BaseModel):
    jd_text: str | None = None


@router.post("/jobs/{job_id}/competency/extract")
async def extract_job_competency(job_id: int, body: _ExtractBody = _ExtractBody(), user_id: int = Depends(get_current_user_id)):
    """触发 LLM 抽取能力模型. 成功 → draft + HITL; 失败 → 降级扁平表单."""
    from app.database import SessionLocal
    from app.modules.screening.models import Job

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该岗位")
        # 若前端传入 jd_text，更新到 DB 覆盖旧值
        if body.jd_text and body.jd_text.strip():
            job.jd_text = body.jd_text.strip()
            db.commit()
        jd_text = job.jd_text or ""
    finally:
        db.close()

    if not jd_text.strip():
        raise HTTPException(status_code=400, detail="jd_text 为空, 请先填 JD 原文")

    try:
        model = await extract_competency(jd_text=jd_text, job_id=job_id)
    except ExtractionFailedError:
        return {"status": "failed", "fallback": "flat_form"}

    db = SessionLocal()
    try:
        from app.core.hitl.models import HitlTask
        from datetime import datetime, timezone
        job = db.query(Job).filter(Job.id == job_id).first()
        job.competency_model = model.model_dump(mode="json")
        job.competency_model_status = "draft"
        # 关掉同一岗位之前所有 HITL 任务（包括 pending + approved），避免审核队列堆孤儿
        # 也保证审计血脉清晰：只有最新一条任务是当前生效版本
        stale = (
            db.query(HitlTask)
            .filter(
                HitlTask.entity_type == "job",
                HitlTask.entity_id == job_id,
                HitlTask.f_stage == "F1_competency_review",
                HitlTask.status.in_(["pending", "approved"]),
            )
            .all()
        )
        for t in stale:
            t.status = "superseded"
            t.reviewed_at = datetime.now(timezone.utc)
            t.note = "superseded by new extraction"
        db.commit()
    finally:
        db.close()

    hitl_id = HitlService().create(
        f_stage="F1_competency_review",
        entity_type="job",
        entity_id=job_id,
        payload=model.model_dump(mode="json"),
    )
    return {"status": "draft", "hitl_task_id": hitl_id}


@router.get("/jobs/{job_id}/competency")
def get_job_competency(job_id: int, user_id: int = Depends(get_current_user_id)):
    from app.database import SessionLocal
    from app.modules.screening.models import Job
    from app.core.hitl.models import HitlTask
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该岗位")
        pending_task = (
            db.query(HitlTask)
            .filter(
                HitlTask.entity_type == "job",
                HitlTask.entity_id == job_id,
                HitlTask.f_stage == "F1_competency_review",
                HitlTask.status == "pending",
            )
            .order_by(HitlTask.id.desc())
            .first()
        )
        cm = job.competency_model
        # Defensive: 若 model 为 None / 空 dict / 缺核心字段, 视为未生成。
        # 防止前端进入 view 模式后访问 model.hard_skills 抛 TypeError。
        cm_valid = bool(cm) and isinstance(cm, dict) and isinstance(cm.get("hard_skills"), list)
        return {
            "competency_model": cm if cm_valid else None,
            "status": job.competency_model_status if cm_valid else "none",
            "pending_hitl_task_id": pending_task.id if pending_task else None,
        }
    finally:
        db.close()


@router.post("/jobs/{job_id}/competency/manual")
def manual_competency(job_id: int, body: _ManualBody, user_id: int = Depends(get_current_user_id)):
    """LLM 失败后 HR 手填扁平字段, 服务端翻译为最简 CompetencyModel, 直接 approved."""
    # Ownership check
    from app.database import SessionLocal as _sl_manual
    from app.modules.screening.models import Job as _Job_manual
    _db_check = _sl_manual()
    try:
        _job_check = _db_check.query(_Job_manual).filter(_Job_manual.id == job_id).first()
        if not _job_check:
            raise HTTPException(status_code=404, detail="job not found")
        if _job_check.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该岗位")
    finally:
        _db_check.close()

    f = body.flat_fields
    skills_csv = f.get("required_skills", "") or ""
    hard_skills = [
        {"name": s.strip(), "weight": 5, "level": "熟练", "must_have": True}
        for s in skills_csv.split(",") if s.strip()
    ]
    model_dict = {
        "schema_version": 1,
        "hard_skills": hard_skills,
        "soft_skills": [],
        "experience": {
            "years_min": int(f.get("work_years_min") or 0),
            "years_max": int(f.get("work_years_max")) if f.get("work_years_max") is not None else None,
            "industries": [],
            "company_scale": None,
        },
        "education": {
            "min_level": f.get("education_min") or "本科",
            "preferred_level": None,
            "prestigious_bonus": False,
        },
        "job_level": "",
        "bonus_items": [],
        "exclusions": [],
        "assessment_dimensions": [],
        "source_jd_hash": "manual_fallback",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    apply_competency_to_job(job_id, model_dict)

    from app.core.audit.logger import log_event
    log_event(
        f_stage="F1_competency_review",
        action="manual_fallback",
        entity_type="job",
        entity_id=job_id,
        input_payload=body.flat_fields,
        output_payload=model_dict,
    )
    return {"status": "approved"}


class _SaveBody(BaseModel):
    competency_model: dict


@router.put("/jobs/{job_id}/competency/save")
def save_competency_draft(job_id: int, body: _SaveBody, user_id: int = Depends(get_current_user_id)):
    """HR 保存草稿：写入 DB，status=draft。若有 pending HITL 任务则同步更新 payload。"""
    from app.database import SessionLocal
    from app.modules.screening.models import Job
    from app.core.hitl.models import HitlTask
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该岗位")
        job.competency_model = body.competency_model
        job.competency_model_status = "draft"
        pending_task = (
            db.query(HitlTask)
            .filter(
                HitlTask.entity_type == "job",
                HitlTask.entity_id == job_id,
                HitlTask.f_stage == "F1_competency_review",
                HitlTask.status == "pending",
            )
            .order_by(HitlTask.id.desc())
            .first()
        )
        if pending_task:
            pending_task.payload = body.competency_model
        db.commit()
    finally:
        db.close()
    return {"status": "draft"}


import logging as _screening_log

# ── Per-job scoring weights endpoints ─────────────────────────────────────────

class _ScoringWeightsBody(BaseModel):
    skill_match: int
    experience: int
    seniority: int
    education: int
    industry: int


@router.get("/jobs/{job_id}/scoring-weights")
def get_job_scoring_weights(
    job_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """返回岗位当前生效权重及来源（custom 或 global）."""
    from app.modules.matching.weights import get_effective_weights
    from app.modules.screening.models import Job
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该岗位")
    custom = bool(job.scoring_weights and isinstance(job.scoring_weights, dict)
                  and job.scoring_weights.get("skill_match") is not None)
    return {"custom": custom, "weights": get_effective_weights(job)}


@router.put("/jobs/{job_id}/scoring-weights")
def set_job_scoring_weights(
    job_id: int,
    body: _ScoringWeightsBody,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """设置岗位自定义评分权重，5 维度之和必须为 100."""
    from app.modules.screening.models import Job
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权修改该岗位")
    total = body.skill_match + body.experience + body.seniority + body.education + body.industry
    if total != 100:
        raise HTTPException(status_code=422, detail=f"各维度权重之和必须为 100，当前为 {total}")
    job.scoring_weights = body.model_dump()
    db.commit()
    db.refresh(job)
    return {"custom": True, "weights": job.scoring_weights}


@router.delete("/jobs/{job_id}/scoring-weights", status_code=204)
def reset_job_scoring_weights(
    job_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """清除岗位自定义权重，恢复使用全局默认."""
    from app.modules.screening.models import Job
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权修改该岗位")
    job.scoring_weights = None
    db.commit()

async def _t2_trigger_with_fresh_session(job_id: int) -> None:
    """Opens its own DB session so it outlives the HTTP response."""
    from app.database import SessionLocal
    from app.modules.matching.triggers import on_competency_approved
    db = SessionLocal()
    try:
        await on_competency_approved(db, job_id)
    except Exception as e:
        _screening_log.getLogger(__name__).warning(f"T2 trigger failed: {e}")
    finally:
        db.close()


async def _recompute_with_purge_for_competency_change(
    job_id: int, user_id: int
) -> None:
    """能力模型变更后全量重算 matching_results.

    相比旧 T2 trigger (on_competency_approved), 本函数:
      1. 先 purge job 下不在硬筛通过集合内的旧行 (避免脏数据残留)
      2. 对硬筛通过的全量简历强制重算 (跑到一半被中断时, 下次进 AI Tab 可由 stale 检测拉起兜底)

    实现复用 /api/matching/recompute 的核心逻辑, 不引入新接口.
    """
    from app.database import SessionLocal
    from app.modules.matching.hard_filter import hard_filter_resume_ids
    from app.modules.matching.router import _purge_outside_hard_filter
    from app.modules.matching.service import (
        _new_task, recompute_job_with_fresh_session,
    )

    db = SessionLocal()
    try:
        allowed = hard_filter_resume_ids(db, user_id, job_id)
        _purge_outside_hard_filter(db, job_id, allowed)
        db.commit()
    finally:
        db.close()

    task_id = _new_task(len(allowed))
    await recompute_job_with_fresh_session(
        job_id, task_id, user_id, pre_filter_resume_ids=allowed,
    )


@router.post("/jobs/{job_id}/competency/approve")
def approve_competency(job_id: int, body: _SaveBody, background_tasks: BackgroundTasks, user_id: int = Depends(get_current_user_id)):
    """HR 通过发布：保存模型 + 状态置为 approved + 回填扁平字段。若有 pending HITL 任务则一并关闭。"""
    from app.database import SessionLocal
    from app.modules.screening.models import Job
    from app.core.hitl.models import HitlTask
    from datetime import datetime, timezone

    # Ownership check
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权访问该岗位")
    finally:
        db.close()

    apply_competency_to_job(job_id, body.competency_model)

    db = SessionLocal()
    try:
        pending_task = (
            db.query(HitlTask)
            .filter(
                HitlTask.entity_type == "job",
                HitlTask.entity_id == job_id,
                HitlTask.f_stage == "F1_competency_review",
                HitlTask.status == "pending",
            )
            .order_by(HitlTask.id.desc())
            .first()
        )
        if pending_task:
            pending_task.status = "approved"
            pending_task.reviewed_at = datetime.now(timezone.utc)
            pending_task.note = "approved via editor"
        db.commit()
    finally:
        db.close()

    from app.core.audit.logger import log_event
    log_event(
        f_stage="F1_competency_review",
        action="hr_approve",
        entity_type="job",
        entity_id=job_id,
        input_payload=body.competency_model,
        output_payload=body.competency_model,
    )

    # F2 强触发: 能力模型变更后全量重算 (清掉旧行避免 stale 残留).
    # 旧的 _t2_trigger_with_fresh_session 保留不删 (其他调用点和测试仍需要).
    background_tasks.add_task(
        _recompute_with_purge_for_competency_change, job_id, user_id,
    )

    return {"status": "approved"}
