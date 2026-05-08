"""InterviewEvalJob / InterviewEvalScorecard ORM 字段 + 约束."""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.exc import IntegrityError

from app.database import Base, engine, SessionLocal


@pytest.fixture(autouse=True)
def setup_tables():
    """建表 + 准备 FK 链所需虚拟行（resumes/interviewers/interviews）.

    注：plan 原始 fixture 只 merge Interview，但 Interview.resume_id /
    interviewer_id 是 NOT NULL FK，sqlite PRAGMA foreign_keys=ON 下需要
    上游行存在。这里统一在 setup 里 merge 三表的 id=99001 占位行，多个测试共享。
    """
    Base.metadata.create_all(bind=engine)
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interview, Interviewer
    db = SessionLocal()
    try:
        db.merge(Resume(id=99001, name="dummy_resume"))
        db.merge(Interviewer(id=99001, name="dummy_interviewer"))
        db.merge(Interview(
            id=99001, resume_id=99001, interviewer_id=99001,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()
    finally:
        db.close()
    yield
    # 不清表：与其他测试同 db，由 fixture 隔离 user_id


def test_job_default_values():
    from app.modules.interview_eval.models import InterviewEvalJob
    db = SessionLocal()
    try:

        job = InterviewEvalJob(
            interview_id=99001, user_id=1,
            retention_until=datetime.now(timezone.utc) + timedelta(days=180),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        assert job.status == "pending"
        assert job.cancel_requested == 0
        assert job.recording_path == ""
        assert job.duration_sec == 0
        assert job.created_at is not None
    finally:
        db.close()


def test_job_status_check_constraint():
    """status 必须是允许枚举之一."""
    from app.modules.interview_eval.models import InterviewEvalJob
    db = SessionLocal()
    try:
        job = InterviewEvalJob(
            interview_id=99001, user_id=1, status="bogus_status",
            retention_until=datetime.now(timezone.utc) + timedelta(days=180),
        )
        db.add(job)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_scorecard_required_fields():
    from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard
    db = SessionLocal()
    try:
        job = InterviewEvalJob(
            interview_id=99001, user_id=1,
            retention_until=datetime.now(timezone.utc) + timedelta(days=180),
        )
        db.add(job); db.commit(); db.refresh(job)

        sc = InterviewEvalScorecard(
            job_id=job.id, interview_id=99001,
            transcript_path="data/transcripts/x.json",
            dimensions_json=[],
            hire_recommendation="hire",
            strengths=[], risks=[], followups=[],
            llm_model="glm-4-flash", prompt_version="interview_eval_v1",
        )
        db.add(sc); db.commit()
        assert sc.id is not None
    finally:
        db.close()
