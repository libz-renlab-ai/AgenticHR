"""F-interview-eval reconcile: 僵尸任务自愈.

非终态 status (pending/downloading/transcribing/scoring) 但 last_heartbeat 陈旧
或为 NULL → 标记 failed("服务中断")。

调用点:
- app/main.py startup: 一次性扫描，恢复服务重启后的残留
- 定时 cron (interview_eval_reconcile_period_seconds): 兜底 worker 进程内异常死亡
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.database import SessionLocal
from app.modules.interview_eval.audit import record as audit_record
from app.modules.interview_eval.models import InterviewEvalJob

logger = logging.getLogger(__name__)

NON_TERMINAL = ("pending", "downloading", "transcribing", "scoring")
_ERROR_MSG = "服务中断：任务超过阈值无心跳，已自动标记失败，请重跑"


def sweep_stale_jobs(threshold_seconds: int) -> int:
    """扫所有非终态 + heartbeat 陈旧的 job，标 failed。返回处理数。

    陈旧判定：last_heartbeat IS NULL 或 last_heartbeat < now - threshold
    IE-020: 跳过 cancel_requested=1 的 job，让 worker 自己处理为 cancelled
            （否则用户主动 cancel 会被误标"服务中断"，UX 误导）
    IE-018: 防御性最低 threshold；调用方应该已经被 settings ge=10 校验，但留个底
    """
    if threshold_seconds <= 0:
        logger.warning("reconcile: threshold_seconds=%d invalid, skip", threshold_seconds)
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=threshold_seconds)
    db = SessionLocal()
    swept = 0
    swept_jobs: list[tuple[int, str, str]] = []  # (job_id, old_status, hb_str)
    try:
        rows = (
            db.query(InterviewEvalJob)
            .filter(InterviewEvalJob.status.in_(NON_TERMINAL))
            .filter(InterviewEvalJob.cancel_requested == 0)  # IE-020
            .filter(
                or_(
                    InterviewEvalJob.last_heartbeat.is_(None),
                    InterviewEvalJob.last_heartbeat < cutoff,
                )
            )
            .all()
        )
        for job in rows:
            swept_jobs.append((job.id, job.status, str(job.last_heartbeat)))
            job.status = "failed"
            job.error_msg = _ERROR_MSG
            swept += 1
        if swept:
            db.commit()  # IE-024: 先持久化状态，再 best-effort 写 audit
            logger.warning("reconcile: swept %d stale jobs", swept)
        # IE-024: audit 不在事务内，单个 audit 失败不回滚 status 修改
        for job_id, old_status, hb_str in swept_jobs:
            try:
                audit_record(
                    "reconcile_stale",
                    entity_id=job_id,
                    old_status=old_status,
                    last_heartbeat=hb_str,
                )
            except Exception as e:
                logger.warning("reconcile audit failed for job %d: %s", job_id, e)
        return swept
    finally:
        db.close()
