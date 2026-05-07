"""ai_screening startup reaper 单测 — BUG-089 + BUG-141.

直接复用 main.py lifespan 的清理逻辑, 验证多 worker 部署下不会误杀活跃 sj。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.modules.auth.models  # noqa: F401
import app.modules.ai_screening.models  # noqa: F401

from app.modules.ai_screening.models import ScreeningJob
from app.modules.screening.models import Job


@pytest.fixture
def engine_session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    s.execute(text(
        "INSERT INTO users (id, username, password_hash, display_name, is_active, daily_cap) "
        "VALUES (1,'u','x','U',1,1000)"
    ))
    s.commit()
    yield s
    s.close()


def _run_reaper(db):
    """复用 main.py 中的 lifespan reaper 行为, 不依赖完整 lifespan."""
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(minutes=10)
    stuck = (
        db.query(ScreeningJob)
        .filter(ScreeningJob.status == "running")
        .filter(
            (ScreeningJob.started_at.is_(None))
            | (ScreeningJob.started_at < stale_threshold)
        )
        .all()
    )
    for sj in stuck:
        sj.status = "failed"
        sj.error_msg = "server restart while running"
        if sj.finished_at is None:
            sj.finished_at = now
    db.commit()
    return len(stuck)


def _make_sj(db, status="running", started_offset_min=None) -> ScreeningJob:
    j = Job(user_id=1, title="t", jd_text="x")
    db.add(j)
    db.commit()
    started_at = None
    if started_offset_min is not None:
        started_at = datetime.now(timezone.utc) - timedelta(minutes=started_offset_min)
    sj = ScreeningJob(
        user_id=1, job_id=j.id, mode="count", threshold=1,
        status=status, total=0, processed=0, started_at=started_at,
    )
    db.add(sj)
    db.commit()
    db.refresh(sj)
    return sj


class TestStartupReaperBug089:
    """BUG-089: 服务器重启时 status='running' 残留必须被标 failed,
    否则用户永久 already_running 阻塞。"""

    def test_old_running_marked_failed(self, engine_session):
        sj = _make_sj(engine_session, status="running", started_offset_min=60)
        n = _run_reaper(engine_session)
        engine_session.refresh(sj)
        assert n == 1
        assert sj.status == "failed"
        assert sj.error_msg == "server restart while running"
        assert sj.finished_at is not None

    def test_null_started_at_treated_as_stale(self, engine_session):
        sj = _make_sj(engine_session, status="running", started_offset_min=None)
        _run_reaper(engine_session)
        engine_session.refresh(sj)
        assert sj.status == "failed"


class TestMultiWorkerSafetyBug141:
    """BUG-141: multi-worker 部署下, reaper 不应误杀其他 worker 正在跑的 sj。
    started_at 在 10 min 以内的活跃 sj 必须保留。"""

    def test_recently_started_sj_not_killed(self, engine_session):
        """场景: 进程 A 刚 start sj (started_at = 30s ago), 进程 B 此时启动
        触发 reaper, A 的 sj 不应被标 failed。"""
        sj = _make_sj(engine_session, status="running", started_offset_min=0.5)
        n = _run_reaper(engine_session)
        engine_session.refresh(sj)
        assert n == 0
        assert sj.status == "running"
        assert sj.error_msg is None

    def test_running_for_5min_still_safe(self, engine_session):
        """5 min batch timeout 内的活跃 sj 不应被误杀。"""
        sj = _make_sj(engine_session, status="running", started_offset_min=4)
        n = _run_reaper(engine_session)
        engine_session.refresh(sj)
        assert n == 0
        assert sj.status == "running"

    def test_done_failed_cancelled_not_touched(self, engine_session):
        """非 running 的终态行不应被 reaper 改动。"""
        d = _make_sj(engine_session, status="done", started_offset_min=120)
        f = _make_sj(engine_session, status="failed", started_offset_min=120)
        c = _make_sj(engine_session, status="cancelled", started_offset_min=120)
        n = _run_reaper(engine_session)
        for sj in (d, f, c):
            engine_session.refresh(sj)
        assert n == 0
        assert d.status == "done"
        assert f.status == "failed"
        assert c.status == "cancelled"
