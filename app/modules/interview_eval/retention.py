"""F-interview-eval 数据保留 cron：180 天清理 mp4 + transcript."""
import logging
import os
from datetime import datetime, timezone

from app.database import SessionLocal
from app.modules.interview_eval.audit import record as audit_record
from app.modules.interview_eval.models import InterviewEvalJob

logger = logging.getLogger(__name__)

RECORDING_DIR = "data/recordings"
TRANSCRIPT_DIR = "data/transcripts"


def purge_expired() -> int:
    """删到期的 mp4 + transcript；soft-delete job 行；返回处理数。"""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    deleted = 0
    try:
        rows = (
            db.query(InterviewEvalJob)
            .filter(
                InterviewEvalJob.retention_until < now,
                InterviewEvalJob.deleted_at.is_(None),
            )
            .all()
        )
        for job in rows:
            # IE-013: 优先用 job.recording_path 字段（RECORDING_DIR 配置变化时仍能命中）
            mp4_paths = [os.path.join(RECORDING_DIR, f"{job.id}.mp4")]
            if job.recording_path and job.recording_path not in mp4_paths:
                mp4_paths.insert(0, job.recording_path)
            ts = os.path.join(TRANSCRIPT_DIR, f"{job.id}.json")
            removed = 0
            for p in mp4_paths + [ts]:
                if os.path.exists(p):
                    try:
                        os.remove(p); removed += 1
                    except OSError as e:
                        logger.warning("retention: cannot remove %s: %s", p, e)
            job.recording_path = ""
            job.deleted_at = now
            audit_record("retention_purge", entity_id=job.id, files_removed=removed)
            deleted += 1
        if deleted:
            db.commit()
            logger.info("retention purged %d expired jobs", deleted)
        return deleted
    finally:
        db.close()
