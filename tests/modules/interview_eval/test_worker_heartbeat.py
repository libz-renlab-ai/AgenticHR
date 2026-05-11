"""F-interview-eval worker 在每次状态切换时 bump last_heartbeat."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.modules.interview_eval.models import InterviewEvalJob


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[InterviewEvalJob.__table__])
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()
    now = datetime.now(timezone.utc)
    job = InterviewEvalJob(
        interview_id=1, user_id=1, status="pending",
        retention_until=now + timedelta(days=180),
        last_heartbeat=None,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    yield session, job.id
    session.close()


def test_set_status_updates_heartbeat(db_session):
    """_set_status 在切换状态时同步更新 last_heartbeat."""
    from app.modules.interview_eval.worker import _set_status
    session, job_id = db_session
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    _set_status(session, job_id, "downloading")
    job = session.query(InterviewEvalJob).filter_by(id=job_id).first()
    assert job.last_heartbeat is not None
    # SQLite stores naive UTC; compare ignoring tz
    hb = job.last_heartbeat.replace(tzinfo=timezone.utc) if job.last_heartbeat.tzinfo is None else job.last_heartbeat
    assert hb >= before


def test_set_status_explicit_heartbeat_override_kept(db_session):
    """显式传 last_heartbeat 覆盖自动 bump."""
    from app.modules.interview_eval.worker import _set_status
    session, job_id = db_session
    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _set_status(session, job_id, "scoring", last_heartbeat=fixed)
    job = session.query(InterviewEvalJob).filter_by(id=job_id).first()
    hb = job.last_heartbeat.replace(tzinfo=timezone.utc) if job.last_heartbeat.tzinfo is None else job.last_heartbeat
    assert hb == fixed


def test_bump_heartbeat_helper(db_session):
    """专用 _bump_heartbeat 不切状态、只更新心跳，scoring 阶段 LLM 调用前后用."""
    from app.modules.interview_eval.worker import _bump_heartbeat
    session, job_id = db_session
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    _bump_heartbeat(session, job_id)
    job = session.query(InterviewEvalJob).filter_by(id=job_id).first()
    assert job.status == "pending"  # 未改状态
    assert job.last_heartbeat is not None
    hb = job.last_heartbeat.replace(tzinfo=timezone.utc) if job.last_heartbeat.tzinfo is None else job.last_heartbeat
    assert hb >= before
