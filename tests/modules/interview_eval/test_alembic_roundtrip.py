"""验证 0027 / 0028 migration 可以 upgrade/downgrade 干净往返.

修复（chaos round 12）：
1. 改用临时 sqlite 文件而非 dev db，避免污染共享 data/recruitment.db
2. baseline 0001 是 no-op、后续 alter migration 假设 schema 已存在 → 走
   create_all + stamp 0026 模式，与 test_migration_idempotent 一致
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
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
    )


def _bootstrap_baseline_schema(db_url: str):
    """模拟正常项目：用 SQLAlchemy create_all 建 schema + stamp 到 0026."""
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
    from sqlalchemy import create_engine
    from app.database import Base

    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    # 共享 pytest 进程下 IE models 可能已注册到 Base，create_all 顺带建表；
    # 本测试要验证 0027 升降，对 IE 表自管，先 drop 让 0027 走 create_table 分支
    from sqlalchemy import text
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


def test_upgrade_to_0027_then_downgrade_back(temp_db_url):
    _bootstrap_baseline_schema(temp_db_url)

    # 升到 0027
    r1 = _alembic(temp_db_url, "upgrade", "0027")
    assert r1.returncode == 0, f"upgrade failed: {r1.stderr}"

    from sqlalchemy import create_engine, inspect
    engine = create_engine(temp_db_url)
    insp = inspect(engine)
    assert "interview_eval_jobs" in insp.get_table_names()
    assert "interview_eval_scorecards" in insp.get_table_names()
    engine.dispose()

    # 降回 0026
    r2 = _alembic(temp_db_url, "downgrade", "0026")
    assert r2.returncode == 0, f"downgrade failed: {r2.stderr}"
    engine = create_engine(temp_db_url)
    insp = inspect(engine)
    assert "interview_eval_jobs" not in insp.get_table_names()
    engine.dispose()

    # 升到 head（0028）
    r3 = _alembic(temp_db_url, "upgrade", "head")
    assert r3.returncode == 0, f"upgrade head failed: {r3.stderr}"
    engine = create_engine(temp_db_url)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("interview_eval_jobs")}
    assert "last_heartbeat" in cols  # 0028 加的列必须存在
    engine.dispose()
