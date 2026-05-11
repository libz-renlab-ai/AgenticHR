"""Migration 0028: 给 interview_eval_jobs 表加 last_heartbeat 列.

支持 worker 心跳 + reconcile 模块识别僵尸任务.
baseline 0001 是 no-op + 后续 migration 假设 schema 已存在 →
走 create_all + stamp 0026 模式跟 test_migration_idempotent / test_alembic_roundtrip 一致.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = str(_REPO_ROOT / "migrations" / "alembic.ini")


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["ALEMBIC_DB_URL"] = db_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", _ALEMBIC_INI, *args],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), env=env,
    )


def _bootstrap_baseline_schema(db_url: str):
    import app.modules.auth.models  # noqa: F401
    import app.modules.resume.models  # noqa: F401
    import app.modules.screening.models  # noqa: F401
    import app.modules.scheduling.models  # noqa: F401
    import app.modules.notification.models  # noqa: F401
    import app.modules.matching.models  # noqa: F401
    import app.modules.matching.decision_model  # noqa: F401
    import app.modules.ai_screening.models  # noqa: F401
    import app.core.audit.models  # noqa: F401
    import app.modules.im_intake.models  # noqa: F401
    import app.modules.im_intake.candidate_model  # noqa: F401
    import app.modules.im_intake.settings_model  # noqa: F401
    import app.modules.im_intake.outbox_model  # noqa: F401
    from sqlalchemy import create_engine, text
    from app.database import Base

    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    # IE 表由 0027 migration 自管，先 drop 让 0027 走 create_table 分支
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS interview_eval_scorecards"))
        conn.execute(text("DROP TABLE IF EXISTS interview_eval_jobs"))
    engine.dispose()

    r = _alembic(db_url, "stamp", "0026")
    assert r.returncode == 0, f"stamp 0026 failed: {r.stderr}"


@pytest.fixture
def temp_db_url():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    yield url
    try:
        os.remove(path)
    except OSError:
        pass


def test_upgrade_adds_last_heartbeat_column(temp_db_url):
    _bootstrap_baseline_schema(temp_db_url)
    r = _alembic(temp_db_url, "upgrade", "0028")
    assert r.returncode == 0, f"upgrade failed: {r.stderr}"
    from sqlalchemy import create_engine, inspect
    engine = create_engine(temp_db_url)
    cols = {c["name"] for c in inspect(engine).get_columns("interview_eval_jobs")}
    assert "last_heartbeat" in cols
    engine.dispose()
