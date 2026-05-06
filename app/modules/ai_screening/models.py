"""AI 智能筛选 ORM."""
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.database import Base


class ScreeningJob(Base):
    __tablename__ = "screening_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id = Column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    mode = Column(String(10), nullable=False)  # 'count' / 'ratio'
    threshold = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    total = Column(Integer, nullable=False, default=0)
    processed = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Integer, nullable=False, default=0)
    error_msg = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        CheckConstraint("mode IN ('count','ratio')", name="ck_sj_mode_enum"),
        CheckConstraint(
            "status IN ('pending','running','done','failed','cancelled')",
            name="ck_sj_status_enum",
        ),
        Index("ix_sj_user_job", "user_id", "job_id", "status"),
        Index("ix_sj_status", "status"),
    )


class ScreeningJobItem(Base):
    __tablename__ = "screening_job_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    screening_job_id = Column(
        Integer,
        ForeignKey("screening_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id = Column(
        Integer,
        ForeignKey("intake_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    pdf_path = Column(String(500), nullable=False, default="")
    score = Column(Integer, nullable=True)  # 0-100
    reason = Column(Text, nullable=True)
    pass_flag = Column(Integer, nullable=False, default=0)  # 0/1
    error = Column(Text, nullable=True)
    batch_no = Column(Integer, nullable=False, default=0)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_sji_job_score", "screening_job_id", "score"),
        Index("ix_sji_job_candidate", "screening_job_id", "candidate_id"),
    )
