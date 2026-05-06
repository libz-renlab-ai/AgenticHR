"""AI 智能筛选 service。

业务流:
  preview → 候选池预览 (硬筛通过, 排除已 reject)
  start   → 建 screening_job + lock items + 启动 worker
  cancel  → cancel_requested=1, worker 检查后中断
  current → 取最新一次 (running 优先, 否则最近 finished)
  items   → 跑完取结果列表
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.ai_screening.models import ScreeningJob, ScreeningJobItem
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.matching.decision_model import JobCandidateDecision
from app.modules.matching.models import MatchingResult
from app.modules.resume.models import Resume
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)


class ScreeningError(Exception):
    """业务异常: code 字段供 router 转 HTTP 状态。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _check_job_owner(db: Session, job_id: int, user_id: int) -> Job:
    job = db.query(Job).filter_by(id=job_id, user_id=user_id).first()
    if not job:
        raise ScreeningError("job_not_found")
    return job


def _eligible_candidate_query(db: Session, user_id: int, job_id: int):
    """硬筛通过 + 未 reject + 有 pdf_path 的 IntakeCandidate 查询。

    返回 (candidate, pdf_path) 元组列表。
    """
    rejected_subq = (
        select(JobCandidateDecision.candidate_id)
        .where(
            and_(
                JobCandidateDecision.job_id == job_id,
                JobCandidateDecision.action == "rejected",
            )
        )
        .scalar_subquery()
    )

    rows = (
        db.query(IntakeCandidate)
        .join(Resume, Resume.id == IntakeCandidate.promoted_resume_id)
        .join(MatchingResult, MatchingResult.resume_id == Resume.id)
        .filter(IntakeCandidate.user_id == user_id)
        .filter(MatchingResult.job_id == job_id)
        .filter(MatchingResult.hard_gate_passed == 1)
        .filter(IntakeCandidate.pdf_path.isnot(None))
        .filter(IntakeCandidate.pdf_path != "")
        # BUG-100: pdf_path 仅空白也应排除 (防 LLM 编造评分)
        .filter(func.trim(IntakeCandidate.pdf_path) != "")
        .filter(~IntakeCandidate.id.in_(rejected_subq))
        .all()
    )
    return rows


def get_running_job(
    db: Session, user_id: int, job_id: int
) -> Optional[ScreeningJob]:
    return (
        db.query(ScreeningJob)
        .filter_by(user_id=user_id, job_id=job_id, status="running")
        .order_by(desc(ScreeningJob.id))
        .first()
    )


def preview(db: Session, user_id: int, job_id: int) -> dict:
    _check_job_owner(db, job_id, user_id)
    cands = _eligible_candidate_query(db, user_id, job_id)
    running = get_running_job(db, user_id, job_id)
    return {
        "eligible_count": len(cands),
        "has_running": running is not None,
    }


def start(
    db: Session,
    user_id: int,
    job_id: int,
    mode: str,
    threshold: int,
    cli_path: Optional[str] = None,
) -> ScreeningJob:
    """创建 screening_job + lock 候选池 items.

    cli_path: BUG-102, router 解析出的 claude 二进制绝对路径, 锁定到 ScreeningJob.cli_path,
    worker 读取时不再 resolve, 避免环境变更不一致。
    raise ScreeningError:
      - job_not_found
      - already_running
      - empty_pool
      - invalid_threshold
    """
    _check_job_owner(db, job_id, user_id)

    if mode not in ("count", "ratio"):
        raise ScreeningError("invalid_mode")
    if threshold < 1 or (mode == "ratio" and threshold > 100):
        raise ScreeningError("invalid_threshold")

    if get_running_job(db, user_id, job_id):
        raise ScreeningError("already_running")

    cands = _eligible_candidate_query(db, user_id, job_id)
    if not cands:
        raise ScreeningError("empty_pool")
    if mode == "count" and threshold > len(cands):
        raise ScreeningError("invalid_threshold")

    sj = ScreeningJob(
        user_id=user_id,
        job_id=job_id,
        mode=mode,
        threshold=threshold,
        status="running",
        total=len(cands),
        processed=0,
        started_at=datetime.now(timezone.utc),
        cli_path=cli_path,
    )
    db.add(sj)
    try:
        db.flush()
    except IntegrityError:
        # BUG-088: partial unique on (user_id, job_id) WHERE status='running'
        # 并发 start 两次时第二次 INSERT 触发 IntegrityError → 转成 already_running
        db.rollback()
        raise ScreeningError("already_running")

    for c in cands:
        item = ScreeningJobItem(
            screening_job_id=sj.id,
            candidate_id=c.id,
            pdf_path=c.pdf_path or "",
            score=None,
            reason=None,
            pass_flag=0,
            batch_no=0,
        )
        db.add(item)
    db.commit()
    db.refresh(sj)
    return sj


def cancel(db: Session, user_id: int, screening_job_id: int) -> ScreeningJob:
    """BUG-090: 设 cancel_requested 后立即调 terminate_active 杀当前 claude 子进程,
    取消立即生效而非等 batch 自然结束 (5min)。"""
    sj = db.query(ScreeningJob).filter_by(id=screening_job_id).first()
    if not sj:
        raise ScreeningError("not_found")
    if sj.user_id != user_id:
        raise ScreeningError("not_found")
    if sj.status != "running":
        raise ScreeningError("not_running")
    sj.cancel_requested = 1
    db.commit()
    db.refresh(sj)
    try:
        from app.modules.ai_screening.worker import terminate_active as _term
        _term(sj.id)
    except Exception as e:
        logger.warning("terminate_active failed for sj=%s: %s", sj.id, e)
    return sj


def current(
    db: Session, user_id: int, job_id: int
) -> Optional[ScreeningJob]:
    """取最新一次 (running 优先, 否则最近 finished_at 那行, 都没就返 None)。"""
    _check_job_owner(db, job_id, user_id)
    running = get_running_job(db, user_id, job_id)
    if running:
        return running
    return (
        db.query(ScreeningJob)
        .filter_by(user_id=user_id, job_id=job_id)
        .order_by(desc(ScreeningJob.id))
        .first()
    )


def list_items(
    db: Session, user_id: int, screening_job_id: int
) -> tuple[ScreeningJob, list[dict]]:
    """返 (screening_job, items 列表), items 含候选名 + 决策状态。

    BUG-103: status='running' 时拒绝返完整 items, 防止 API 自动化拿到中间状态。
    跑完 (done/failed/cancelled) 才返。
    """
    sj = db.query(ScreeningJob).filter_by(id=screening_job_id).first()
    if not sj or sj.user_id != user_id:
        raise ScreeningError("not_found")
    if sj.status not in ("done", "failed", "cancelled"):
        raise ScreeningError("not_finished")

    items = (
        db.query(ScreeningJobItem)
        .filter_by(screening_job_id=sj.id)
        .order_by(desc(ScreeningJobItem.score), ScreeningJobItem.candidate_id)
        .all()
    )
    cand_ids = [it.candidate_id for it in items]
    cands = {
        c.id: c
        for c in db.query(IntakeCandidate)
        .filter(IntakeCandidate.id.in_(cand_ids))
        .all()
    }
    decisions = {
        d.candidate_id: d.action
        for d in db.query(JobCandidateDecision)
        .filter_by(job_id=sj.job_id)
        .filter(JobCandidateDecision.candidate_id.in_(cand_ids))
        .all()
    }

    out = []
    for it in items:
        c = cands.get(it.candidate_id)
        out.append(
            {
                "id": it.id,
                "candidate_id": it.candidate_id,
                "candidate_name": c.name if c else f"#{it.candidate_id}",
                "score": it.score,
                "reason": it.reason,
                "pass_flag": it.pass_flag,
                "error": it.error,
                "decision_action": decisions.get(it.candidate_id),
            }
        )
    return sj, out
