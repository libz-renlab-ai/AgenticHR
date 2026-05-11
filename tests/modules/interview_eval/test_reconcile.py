"""F-interview-eval reconcile: 服务重启 / worker 异常死亡后僵尸任务自愈."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.modules.interview_eval.models import InterviewEvalJob


@pytest.fixture
def db_session(monkeypatch):
    """In-memory SQLite + 替换 SessionLocal."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[InterviewEvalJob.__table__])
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    from app.modules.interview_eval import reconcile as reconcile_module
    monkeypatch.setattr(reconcile_module, "SessionLocal", TestSession)
    session = TestSession()
    yield session
    session.close()


def _make_job(session, status: str, heartbeat_ago_sec: int | None) -> InterviewEvalJob:
    now = datetime.now(timezone.utc)
    hb = None if heartbeat_ago_sec is None else now - timedelta(seconds=heartbeat_ago_sec)
    job = InterviewEvalJob(
        interview_id=1, user_id=1, status=status,
        retention_until=now + timedelta(days=180),
        last_heartbeat=hb,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_sweep_marks_stale_pending_as_failed(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "pending", heartbeat_ago_sec=1000)
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 1
    db_session.refresh(job)
    assert job.status == "failed"


def test_sweep_marks_stale_scoring_as_failed(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "scoring", heartbeat_ago_sec=500)
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 1
    db_session.refresh(job)
    assert job.status == "failed"


def test_sweep_skips_fresh_job(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "scoring", heartbeat_ago_sec=30)
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 0
    db_session.refresh(job)
    assert job.status == "scoring"


def test_sweep_skips_terminal_done_cancelled_failed(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    j_done = _make_job(db_session, "done", heartbeat_ago_sec=1000)
    j_can = _make_job(db_session, "cancelled", heartbeat_ago_sec=1000)
    j_fail = _make_job(db_session, "failed", heartbeat_ago_sec=1000)
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 0
    for j in (j_done, j_can, j_fail):
        db_session.refresh(j)
    assert j_done.status == "done"
    assert j_can.status == "cancelled"
    assert j_fail.status == "failed"


def test_sweep_null_heartbeat_treated_as_stale(db_session):
    """历史行（migration 之前残留的 pending）last_heartbeat=NULL → 视为陈旧."""
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "pending", heartbeat_ago_sec=None)
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 1
    db_session.refresh(job)
    assert job.status == "failed"


def test_sweep_sets_error_msg(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "downloading", heartbeat_ago_sec=400)
    sweep_stale_jobs(threshold_seconds=180)
    db_session.refresh(job)
    assert "服务中断" in job.error_msg or "service interrupted" in job.error_msg.lower()


def test_sweep_returns_count(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    _make_job(db_session, "pending", heartbeat_ago_sec=1000)
    _make_job(db_session, "scoring", heartbeat_ago_sec=400)
    _make_job(db_session, "transcribing", heartbeat_ago_sec=30)  # fresh
    _make_job(db_session, "done", heartbeat_ago_sec=1000)         # terminal
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 2


# IE-020: 跳过 cancel_requested=1 让 worker 自己处理成 cancelled
def test_sweep_skips_cancel_requested_job(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "scoring", heartbeat_ago_sec=1000)
    job.cancel_requested = 1
    db_session.commit()
    n = sweep_stale_jobs(threshold_seconds=180)
    assert n == 0
    db_session.refresh(job)
    assert job.status == "scoring"  # 未被改 failed
    assert job.error_msg == ""       # 未写入"服务中断"


# IE-018: 防御性最低 threshold
def test_sweep_rejects_zero_or_negative_threshold(db_session):
    from app.modules.interview_eval.reconcile import sweep_stale_jobs
    job = _make_job(db_session, "scoring", heartbeat_ago_sec=1000)
    assert sweep_stale_jobs(threshold_seconds=0) == 0
    assert sweep_stale_jobs(threshold_seconds=-1) == 0
    db_session.refresh(job)
    assert job.status == "scoring"  # 没被误杀


# IE-024: audit_record 失败不影响 status 修改持久化
def test_sweep_status_committed_before_audit(db_session, monkeypatch):
    from app.modules.interview_eval import reconcile as reconcile_module
    job = _make_job(db_session, "scoring", heartbeat_ago_sec=1000)

    def _failing_audit(*args, **kwargs):
        raise RuntimeError("audit log broken")
    monkeypatch.setattr(reconcile_module, "audit_record", _failing_audit)

    n = reconcile_module.sweep_stale_jobs(threshold_seconds=180)
    assert n == 1
    db_session.refresh(job)
    # audit 失败但 status 已 commit
    assert job.status == "failed"
    assert "服务中断" in job.error_msg
