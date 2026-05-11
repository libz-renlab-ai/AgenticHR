"""BUG-IE-012: 0027 migration 在表已存在但缺列的 legacy db 上必须补齐缺失列。

场景：legacy 库有人手工 patch 过 interview_eval_jobs，缺少 cancel_requested 等列。
原行为：if 表存在 → skip create_table；alembic_version 跳到 0027；运行时崩溃。
期望：检测到表已存在时，对比 canonical 列清单，缺什么补什么（best-effort）。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text


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


@pytest.fixture
def temp_db_url():
    """独立的临时 sqlite 文件，避免污染 data/recruitment.db。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    yield url
    try:
        os.remove(path)
    except OSError:
        pass


def _bootstrap_baseline_schema(db_url: str):
    """模拟正常项目：用 SQLAlchemy create_all 建 schema + stamp 到 0026。

    迁移链 0001..0026 假设 schema 已由 ORM Base.metadata 创建（baseline 0001 是 no-op，
    后续 alter 也以 schema 已存在为前提）。
    """
    # 注册所有 model 到 Base.metadata
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
    from app.database import Base

    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    # stamp 到 0026（不真跑 upgrade）
    r = _alembic(db_url, "stamp", "0026")
    assert r.returncode == 0, f"stamp 0026 failed: {r.stderr}"


def test_0027_partial_table_adds_missing_columns(temp_db_url):
    """legacy 库：interview_eval_jobs 已存在但缺 cancel_requested 等列。

    upgrade 0027 应该补齐缺失列，而不是 silent skip。
    """
    # 1) 用 ORM 建 baseline 并 stamp 到 0026
    _bootstrap_baseline_schema(temp_db_url)

    # 2) 手工创建一个不完整的 interview_eval_jobs（模拟 legacy 手工 patch）
    # 注意：上一步 create_all 如果在 sys.modules 已 import 过 interview_eval.models
    # 的进程里跑（共享 pytest 进程），Base.metadata 会包含 IE 表 → create_all 已建。
    # 显式 drop 后再用 legacy 不完整 schema 建。
    engine = create_engine(temp_db_url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS interview_eval_jobs"))
        conn.execute(text("DROP TABLE IF EXISTS interview_eval_scorecards"))
        conn.execute(text(
            "CREATE TABLE interview_eval_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "interview_id INTEGER NOT NULL"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE interview_eval_scorecards ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "job_id INTEGER NOT NULL"
            ")"
        ))

    # 3) 升到 0027
    r1 = _alembic(temp_db_url, "upgrade", "0027")
    assert r1.returncode == 0, f"upgrade to 0027 failed: {r1.stderr}"

    # 4) 验证 cancel_requested 等关键列已被补齐
    insp = inspect(engine)
    job_cols = {c["name"] for c in insp.get_columns("interview_eval_jobs")}
    expected_job_cols = {
        "id", "interview_id", "user_id", "status",
        "recording_path", "recording_size", "duration_sec",
        "meeting_account", "asr_request_id", "llm_model",
        "prompt_version", "error_msg", "cancel_requested",
        "retention_until", "deleted_at", "created_at", "updated_at",
    }
    missing = expected_job_cols - job_cols
    assert not missing, f"interview_eval_jobs 缺失列未补齐: {missing}"

    sc_cols = {c["name"] for c in insp.get_columns("interview_eval_scorecards")}
    expected_sc_cols = {
        "id", "job_id", "interview_id", "transcript_path",
        "dimensions_json", "hire_recommendation", "strengths",
        "risks", "followups", "llm_model", "prompt_version", "created_at",
    }
    missing_sc = expected_sc_cols - sc_cols
    assert not missing_sc, f"interview_eval_scorecards 缺失列未补齐: {missing_sc}"
    engine.dispose()


def test_0027_idempotent_on_complete_table(temp_db_url):
    """完整表上重复 upgrade 不应报错（idempotency）。"""
    _bootstrap_baseline_schema(temp_db_url)
    r0 = _alembic(temp_db_url, "upgrade", "0027")
    assert r0.returncode == 0, f"first upgrade failed: {r0.stderr}"

    # 模拟已 stamp 但又被 downgrade alembic_version 后重跑 upgrade 的诡异场景
    engine = create_engine(temp_db_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num='0026'"))
    engine.dispose()

    r1 = _alembic(temp_db_url, "upgrade", "0027")
    assert r1.returncode == 0, f"re-upgrade failed: {r1.stderr}"
