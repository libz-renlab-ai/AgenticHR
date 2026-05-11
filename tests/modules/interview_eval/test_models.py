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
    import app.modules.screening.models  # noqa: F401 — Interview.job_id FK 需要 jobs 表元数据
    import app.modules.interview_eval.models  # noqa: F401 — register IE tables in Base
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interview, Interviewer
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # SQLite PRAGMA foreign_keys=ON: 必须先 commit 上游再 INSERT 下游，
        # ORM merge 单次 flush 不保证按 FK 拓扑排序
        db.merge(Resume(id=99001, name="dummy_resume"))
        db.merge(Interviewer(id=99001, name="dummy_interviewer"))
        db.commit()
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
