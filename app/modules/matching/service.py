"""F2 匹配服务 — 编排 scorers + 写 DB + 审计."""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit.logger import log_event
from app.modules.matching.hashing import compute_competency_hash, compute_weights_hash
from app.modules.matching.weights import get_effective_weights
from app.modules.matching.models import MatchingResult
from app.modules.matching.schemas import EvidenceItem, MatchingResultResponse
from app.modules.matching.scorers.aggregator import aggregate, derive_tags
from app.modules.matching.scorers.education import score_education
from app.modules.matching.scorers.evidence import (
    build_deterministic_evidence, enhance_evidence_with_llm,
)
from app.modules.matching.scorers.experience import score_experience
from app.modules.matching.scorers.industry import score_industry
from app.modules.matching.scorers.seniority import score_seniority
from app.modules.matching.scorers.skill import score_skill
from app.modules.resume.models import Resume
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)


class MatchingService:
    def __init__(self, db: Session):
        self.db = db

    async def score_pair(
        self, resume_id: int, job_id: int, *, triggered_by: str = "T4"
    ) -> MatchingResultResponse:
        resume = self.db.query(Resume).filter_by(id=resume_id).first()
        if not resume:
            raise ValueError(f"resume {resume_id} not found")
        job = self.db.query(Job).filter_by(id=job_id).first()
        if not job:
            raise ValueError(f"job {job_id} not found")
        if not job.competency_model:
            raise ValueError(f"job {job_id} has no competency_model (not approved yet)")

        cm = job.competency_model
        weights = get_effective_weights(job)

        # 分维度打分
        skill_score, missing_must = score_skill(
            cm.get("hard_skills", []),
            resume.skills or "",
            db_session=self.db,
        )
        experience_score = score_experience(
            resume.work_years or 0,
            cm.get("experience") or {},
        )
        seniority_score = score_seniority(
            resume.seniority or "",
            cm.get("job_level", "") or "",
        )
        education_score = score_education(
            resume.education or "",
            cm.get("education") or {},
        )
        industries = (cm.get("experience") or {}).get("industries") or []
        industry_score = score_industry(
            resume.work_experience or "", industries, db_session=self.db,
        )

        # 聚合
        agg = aggregate(
            dim_scores={
                "skill": skill_score, "experience": experience_score,
                "seniority": seniority_score, "education": education_score,
                "industry": industry_score,
            },
            missing_must_haves=missing_must,
            weights=weights,
        )
        tags = derive_tags(
            total_score=agg["total_score"],
            hard_gate_passed=agg["hard_gate_passed"],
            missing=missing_must,
            education_score=education_score,
            experience_score=experience_score,
        )

        # 证据
        matched_skills = self._compute_matched_skills(cm.get("hard_skills", []), resume, missing_must)
        matched_industries = self._compute_matched_industries(industries, resume.work_experience or "")
        base_ev = build_deterministic_evidence(
            resume=resume,
            matched_skills=matched_skills,
            experience_range=(
                (cm.get("experience") or {}).get("years_min", 0),
                (cm.get("experience") or {}).get("years_max") or ((cm.get("experience") or {}).get("years_min", 0) + 10),
            ),
            matched_industries=matched_industries,
        )
        dim_scores_dict = {
            "skill": skill_score, "experience": experience_score,
            "seniority": seniority_score, "education": education_score,
            "industry": industry_score,
        }
        evidence = await enhance_evidence_with_llm(base_ev, resume, dim_scores_dict)

        # UPSERT
        competency_hash = compute_competency_hash(cm)
        weights_hash = compute_weights_hash(weights)
        now = datetime.now(timezone.utc)

        existing = self.db.query(MatchingResult).filter_by(
            resume_id=resume_id, job_id=job_id
        ).first()
        if existing:
            existing.total_score = agg["total_score"]
            existing.skill_score = skill_score
            existing.experience_score = experience_score
            existing.seniority_score = seniority_score
            existing.education_score = education_score
            existing.industry_score = industry_score
            existing.hard_gate_passed = 1 if agg["hard_gate_passed"] else 0
            existing.missing_must_haves = json.dumps(missing_must, ensure_ascii=False)
            existing.evidence = json.dumps(evidence, ensure_ascii=False)
            existing.tags = json.dumps(tags, ensure_ascii=False)
            existing.competency_hash = competency_hash
            existing.weights_hash = weights_hash
            existing.scored_at = now
            row = existing
        else:
            row = MatchingResult(
                resume_id=resume_id, job_id=job_id,
                total_score=agg["total_score"],
                skill_score=skill_score, experience_score=experience_score,
                seniority_score=seniority_score, education_score=education_score,
                industry_score=industry_score,
                hard_gate_passed=1 if agg["hard_gate_passed"] else 0,
                missing_must_haves=json.dumps(missing_must, ensure_ascii=False),
                evidence=json.dumps(evidence, ensure_ascii=False),
                tags=json.dumps(tags, ensure_ascii=False),
                competency_hash=competency_hash, weights_hash=weights_hash,
                scored_at=now,
            )
            self.db.add(row)

        self.db.commit()
        self.db.refresh(row)

        # 审计
        try:
            log_event(
                f_stage="F2",
                action="score",
                entity_type="matching_result",
                entity_id=row.id,
                input_payload={
                    "resume_id": resume_id, "job_id": job_id,
                    "trigger": triggered_by,
                    "competency_hash": competency_hash,
                    "weights_hash": weights_hash,
                },
                output_payload={
                    "total_score": agg["total_score"],
                    "dim_scores": dim_scores_dict,
                    "tags": tags,
                    "hard_gate_passed": agg["hard_gate_passed"],
                    "missing_must_haves": missing_must,
                },
            )
        except Exception as e:
            logger.warning(f"audit log failed (non-fatal): {e}")

        response = self._to_response(row, resume, job, competency_hash, weights_hash)
        # spec 0429-D cleanup (P2-b): 单 row 响应也以决策表为真值, 与 list_results 一致;
        # 防止旧 row.job_action 字段未同步导致前端显示陈旧决策。
        # BUG-106: 用 job.user_id 作为 owner 真值 (job 已在 router 验过 user 归属);
        # 不再隐式信任 resume.user_id (历史脏数据可能跨用户)。
        try:
            from app.modules.im_intake.candidate_model import IntakeCandidate
            from app.modules.matching.decision_service import get_decision
            owner_id = job.user_id
            cand = self.db.query(IntakeCandidate).filter_by(
                promoted_resume_id=resume.id, user_id=owner_id,
            ).first()
            if cand:
                d = get_decision(self.db, owner_id, job.id, cand.id)
                response.job_action = d.action if d else None
                response.candidate_id = cand.id
        except Exception as _e:
            logger.warning(f"decision lookup failed (non-fatal): {_e}")
        return response

    @staticmethod
    def _compute_matched_skills(hard_skills: list[dict], resume: Resume, missing: list[str]) -> list[str]:
        """匹配到的技能名 = hard_skills - missing."""
        missing_set = set(missing)
        return [hs["name"] for hs in hard_skills if hs.get("name") not in missing_set]

    @staticmethod
    def _compute_matched_industries(industries: list[str], work_experience: str) -> list[str]:
        text = (work_experience or "").lower()
        return [ind for ind in industries if ind and ind.lower() in text]

    @staticmethod
    def _to_response(
        row: MatchingResult, resume: Resume, job: Job,
        current_competency_hash: str, current_weights_hash: str,
    ) -> MatchingResultResponse:
        evidence_dict = json.loads(row.evidence or "{}")
        return MatchingResultResponse(
            id=row.id, resume_id=row.resume_id, resume_name=resume.name,
            job_id=row.job_id, job_title=job.title,
            total_score=row.total_score, skill_score=row.skill_score,
            experience_score=row.experience_score, seniority_score=row.seniority_score,
            education_score=row.education_score, industry_score=row.industry_score,
            hard_gate_passed=bool(row.hard_gate_passed),
            missing_must_haves=json.loads(row.missing_must_haves or "[]"),
            evidence={k: [EvidenceItem(**e) for e in v] for k, v in evidence_dict.items()},
            tags=json.loads(row.tags or "[]"),
            job_action=row.job_action,
            stale=(row.competency_hash != current_competency_hash
                   or row.weights_hash != current_weights_hash),
            scored_at=row.scored_at,
        )


import uuid
from datetime import timedelta

# 全局任务状态表（in-memory，进程重启丢；足够 V1 用）
_RECOMPUTE_TASKS: dict[str, dict] = {}


def _new_task(total: int) -> str:
    task_id = str(uuid.uuid4())
    _RECOMPUTE_TASKS[task_id] = {
        "task_id": task_id, "total": total, "completed": 0, "failed": 0,
        "running": True, "current": "",
        "started_at": datetime.now(timezone.utc),
    }
    return task_id


def _get_task(task_id: str) -> dict | None:
    return _RECOMPUTE_TASKS.get(task_id)


def _prune_stale_tasks(hours: int = 24) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stale = [k for k, v in _RECOMPUTE_TASKS.items() if v["started_at"] < cutoff]
    for k in stale:
        _RECOMPUTE_TASKS.pop(k, None)


async def recompute_job(
    db: Session,
    job_id: int,
    task_id: str,
    user_id: int = 0,
    *,
    pre_filter_resume_ids: set[int] | None = None,
) -> None:
    """后台任务：对 job 的所有 ai_parsed='yes' 简历打分.

    pre_filter_resume_ids: 仅对集合内 Resume.id 打分（用于 "硬筛 → F2" 串联）。
                           None = 旧行为（全量 ai_parsed=yes）。空集合 = 跳过全部。
    """
    task = _RECOMPUTE_TASKS[task_id]
    try:
        q = db.query(Resume).filter(
            Resume.ai_parsed == "yes",
            Resume.user_id == user_id,
        )
        if pre_filter_resume_ids is not None:
            if not pre_filter_resume_ids:
                resume_ids = []
            else:
                q = q.filter(Resume.id.in_(pre_filter_resume_ids))
                resume_ids = [r.id for r in q.all()]
        else:
            resume_ids = [r.id for r in q.all()]
        # endpoint 通常已显式设好 total (硬筛通过数), 后台仅在未设时回填,
        # 避免后台 session 看不到主测试 session 的数据导致 total 被错误覆盖。
        if not task["total"]:
            task["total"] = len(resume_ids)
        service = MatchingService(db)
        for rid in resume_ids:
            task["current"] = f"Resume#{rid} × Job#{job_id}"
            try:
                await service.score_pair(rid, job_id, triggered_by="T3")
                task["completed"] += 1
            except Exception as e:
                logger.warning(f"recompute failed for resume {rid}: {e}")
                task["failed"] += 1
    finally:
        task["running"] = False
        task["current"] = ""
        try:
            log_event(
                f_stage="F2", action="recompute_job_done",
                entity_type="matching", entity_id=job_id,
                output_payload={
                    "task_id": task_id,
                    "completed": task["completed"],
                    "failed": task["failed"],
                },
            )
        except Exception as e:
            logger.warning(f"audit log failed (non-fatal): {e}")


async def recompute_resume(db: Session, resume_id: int, task_id: str) -> None:
    """后台任务：对 resume 的所有 is_active + approved 岗位打分."""
    task = _RECOMPUTE_TASKS[task_id]
    try:
        job_ids = [j.id for j in db.query(Job).filter(
            Job.is_active == True,
            Job.competency_model_status == "approved",
        ).all()]
        # total is pre-set by the endpoint via _new_task; only update if not yet set
        if not task["total"]:
            task["total"] = len(job_ids)
        service = MatchingService(db)
        for jid in job_ids:
            task["current"] = f"Resume#{resume_id} × Job#{jid}"
            try:
                await service.score_pair(resume_id, jid, triggered_by="T3")
                task["completed"] += 1
            except Exception as e:
                logger.warning(f"recompute failed for job {jid}: {e}")
                task["failed"] += 1
    finally:
        task["running"] = False
        task["current"] = ""
        try:
            log_event(
                f_stage="F2", action="recompute_resume_done",
                entity_type="matching", entity_id=resume_id,
                output_payload={
                    "task_id": task_id,
                    "completed": task["completed"],
                    "failed": task["failed"],
                },
            )
        except Exception as e:
            logger.warning(f"audit log failed (non-fatal): {e}")


async def recompute_job_with_fresh_session(
    job_id: int,
    task_id: str,
    user_id: int = 0,
    *,
    pre_filter_resume_ids: set[int] | None = None,
) -> None:
    """Wrapper for recompute_job that opens its own DB session so it outlives the HTTP response."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        await recompute_job(
            db, job_id, task_id, user_id=user_id,
            pre_filter_resume_ids=pre_filter_resume_ids,
        )
    finally:
        db.close()


async def recompute_resume_with_fresh_session(resume_id: int, task_id: str) -> None:
    """Wrapper for recompute_resume that opens its own DB session so it outlives the HTTP response."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        await recompute_resume(db, resume_id, task_id)
    finally:
        db.close()
