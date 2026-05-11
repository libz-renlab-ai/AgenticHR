"""Migration 0028: 给 interview_eval_jobs 表加 last_heartbeat 列.

支持 worker 心跳 + reconcile 模块识别僵尸任务.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect


_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = str(_REPO_ROOT / "migrations" / "alembic.ini")


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["ALEMBIC_DB_URL"] = db_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", _ALEMBIC_INI, *args],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), env=env,
    )


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
    r = _alembic(temp_db_url, "upgrade", "0028")
    assert r.returncode == 0, f"upgrade failed: {r.stderr}"
    engine = create_engine(temp_db_url)
    cols = {c["name"] for c in inspect(engine).get_columns("interview_eval_jobs")}
    assert "last_heartbeat" in cols
    engine.dispose()
