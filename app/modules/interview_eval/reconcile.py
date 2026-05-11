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
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=threshold_seconds)
    db = SessionLocal()
    swept = 0
    try:
        rows = (
            db.query(InterviewEvalJob)
            .filter(InterviewEvalJob.status.in_(NON_TERMINAL))
            .filter(
                or_(
                    InterviewEvalJob.last_heartbeat.is_(None),
                    InterviewEvalJob.last_heartbeat < cutoff,
                )
            )
            .all()
        )
        for job in rows:
            old_status = job.status
            job.status = "failed"
            job.error_msg = _ERROR_MSG
            audit_record(
                "reconcile_stale",
                entity_id=job.id,
                old_status=old_status,
                last_heartbeat=str(job.last_heartbeat),
            )
            swept += 1
        if swept:
            db.commit()
            logger.warning("reconcile: swept %d stale jobs", swept)
        return swept
    finally:
        db.close()
