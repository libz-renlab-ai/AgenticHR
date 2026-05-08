"""F-interview-eval ORM: interview_eval_jobs + interview_eval_scorecards."""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer,
    String, Text,
)

from app.database import Base


class InterviewEvalJob(Base):
    __tablename__ = "interview_eval_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    interview_id = Column(
        Integer, ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False,
    )
    user_id = Column(Integer, nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending")
    recording_path = Column(String(500), nullable=False, default="")
    recording_size = Column(Integer, nullable=False, default=0)
    duration_sec = Column(Integer, nullable=False, default=0)
    meeting_account = Column(String(50), nullable=False, default="")
    asr_request_id = Column(String(100), nullable=False, default="")
    llm_model = Column(String(100), nullable=False, default="")
    prompt_version = Column(String(50), nullable=False, default="")
    error_msg = Column(Text, nullable=False, default="")
    cancel_requested = Column(Integer, nullable=False, default=0)
    retention_until = Column(DateTime, nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False,
    )
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','downloading','transcribing','scoring','done','failed','cancelled')",
            name="ck_ieval_job_status",
        ),
        Index("ix_ieval_jobs_interview", "interview_id"),
        Index("ix_ieval_jobs_status", "status"),
        Index("ix_ieval_jobs_retention", "retention_until"),
    )


class InterviewEvalScorecard(Base):
    __tablename__ = "interview_eval_scorecards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(
        Integer, ForeignKey("interview_eval_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    interview_id = Column(Integer, nullable=False, index=True)
    transcript_path = Column(String(500), nullable=False)
    dimensions_json = Column(JSON, nullable=False)
    hire_recommendation = Column(String(20), nullable=False)
    strengths = Column(JSON, nullable=False)
    risks = Column(JSON, nullable=False)
    followups = Column(JSON, nullable=False)
    llm_model = Column(String(100), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "hire_recommendation IN ('strong_hire','hire','hold','no_hire')",
            name="ck_ieval_sc_recommendation",
        ),
        Index("ix_ieval_sc_job", "job_id"),
        Index("ix_ieval_sc_interview", "interview_id"),
    )
