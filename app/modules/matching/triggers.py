"""F2 触发器 — T1 简历入库 / T2 能力模型发布.

写入 matching_results 前必须过硬筛 (与 list_matched_for_job 一致),
否则 "再次分析" 清掉的人会被 T1/T2 立即写回, DB 永远脏。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.matching.hard_filter import hard_filter_resume_ids
from app.modules.matching.service import MatchingService
from app.modules.resume.models import Resume
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)


def _intake_in_use(db: Session, user_id: int) -> bool:
    """该 user 是否已启用 intake 流程 (有任何 IntakeCandidate 记录).
    未启用 → 跳过硬筛守卫, 走旧式 Resume 直进 F2 流程 (兼容 legacy/测试)。
    """
    from app.modules.im_intake.candidate_model import IntakeCandidate
    return db.query(IntakeCandidate).filter_by(user_id=user_id).first() is not None


async def on_resume_parsed(db: Session, resume_id: int) -> None:
    """T1: 简历 AI 解析完成 → 对所有 is_active + approved 岗位打分.
    硬筛守卫 (启用 intake 时): 仅对 resume 在 job 硬筛通过集合内时才打分。
    """
    if not getattr(settings, "matching_enabled", True):
        return
    resume = db.query(Resume).filter_by(id=resume_id).first()
    if not resume:
        return
    user_id = resume.user_id
    # 多租户隔离: 只对同用户的岗位打分, 否则 user A 简历会被写到 user B 的
    # matching_results, 与 b867297/6ede54e/e115bab 同源 leak。
    jobs = db.query(Job).filter(
        Job.user_id == user_id,
        Job.is_active == True,
        Job.competency_model_status == "approved",
    ).all()
    service = MatchingService(db)
    apply_guard = _intake_in_use(db, user_id)
    for job in jobs:
        try:
            if apply_guard:
                allowed = hard_filter_resume_ids(db, user_id, job.id)
                if resume_id not in allowed:
                    continue  # 硬筛未过, 不写 matching_results
            await service.score_pair(resume_id, job.id, triggered_by="T1")
        except Exception as e:
            logger.warning(f"T1 score failed resume={resume_id} job={job.id}: {e}")


async def on_competency_approved(db: Session, job_id: int) -> None:
    """T2: 能力模型发布 → 对过去 N 天入库的 ai_parsed='yes' 简历打分.
    硬筛守卫 (启用 intake 时): 仅对硬筛通过集合内的简历打分。
    """
    if not getattr(settings, "matching_enabled", True):
        return
    job = db.query(Job).filter_by(id=job_id).first()
    if not job:
        return
    user_id = job.user_id
    days = getattr(settings, "matching_trigger_days_back", 90)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    apply_guard = _intake_in_use(db, user_id)

    # 多租户隔离: 只扫同用户的简历。apply_guard=True 时 hard_filter_resume_ids
    # 已 user-scoped, 但 apply_guard=False 路径 (用户未启用 intake) 会全局扫,
    # 给 user B 简历写 user A 岗位的 matching_results。两条路径都要强加 user_id。
    base_q = db.query(Resume).filter(
        Resume.user_id == user_id,
        Resume.ai_parsed == "yes",
        Resume.created_at >= cutoff,
    )
    if apply_guard:
        allowed = hard_filter_resume_ids(db, user_id, job_id)
        if not allowed:
            return
        base_q = base_q.filter(Resume.id.in_(allowed))

    resumes = base_q.all()
    service = MatchingService(db)
    for r in resumes:
        try:
            await service.score_pair(r.id, job_id, triggered_by="T2")
        except Exception as e:
            logger.warning(f"T2 score failed resume={r.id} job={job_id}: {e}")
