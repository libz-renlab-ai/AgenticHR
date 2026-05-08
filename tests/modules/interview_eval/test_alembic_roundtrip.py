"""验证 0027 migration 可以 upgrade/downgrade 干净往返."""
import subprocess
import sys
from pathlib import Path

# alembic.ini 位于 migrations/ 子目录；用 -c 指定，cwd 设为 worktree 根
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = str(_REPO_ROOT / "migrations" / "alembic.ini")


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", _ALEMBIC_INI, *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )


def test_upgrade_to_0027_then_downgrade_back():
    # 升到 0027
    r1 = _alembic("upgrade", "0027")
    assert r1.returncode == 0, f"upgrade failed: {r1.stderr}"

    # 验证表存在
    from sqlalchemy import inspect
    from app.database import engine
    insp = inspect(engine)
    assert "interview_eval_jobs" in insp.get_table_names()
    assert "interview_eval_scorecards" in insp.get_table_names()

    # 降到 0026
    r2 = _alembic("downgrade", "0026")
    assert r2.returncode == 0, f"downgrade failed: {r2.stderr}"
    insp = inspect(engine)
    assert "interview_eval_jobs" not in insp.get_table_names()

    # 升回 head（终态）
    r3 = _alembic("upgrade", "head")
    assert r3.returncode == 0, f"upgrade head failed: {r3.stderr}"
