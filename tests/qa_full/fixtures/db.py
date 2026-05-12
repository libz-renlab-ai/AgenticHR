"""独立 test DB,每轮起新副本,跑完不删(供失败排查)。

Bootstrap 顺序模仿 app/main.py 启动流程:
  1. SQLAlchemy create_all() 建当前所有表 (M2 baseline)
  2. alembic stamp 0001 标记 baseline
  3. alembic upgrade head 跑 0002+ 的增量迁移
这样既绕过"baseline 不实际建表"的问题,也复用真实迁移路径。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"


@pytest.fixture(scope="session")
def qa_db_path(round_no):
    p = DATA_DIR / f"qa_test_{round_no}.db"
    if p.exists():
        p.unlink()

    db_url = f"sqlite:///{p}"
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    alembic_ini = str(REPO_ROOT / "migrations" / "alembic.ini")

    # 1) SQLAlchemy create_all — import 所有 model 模块,metadata 才完整
    bootstrap_lines = [
        "import app.core.audit.models, app.core.competency.models, app.core.hitl.models",
        "import app.modules.auth.models, app.modules.resume.models",
        "import app.modules.screening.models, app.modules.scheduling.models",
        "import app.modules.notification.models, app.modules.ai_screening.models",
        "import app.modules.interview_eval.models",
        "import app.modules.im_intake.models",
        "import app.modules.im_intake.candidate_model",
        "import app.modules.im_intake.outbox_model",
        "import app.modules.im_intake.settings_model",
        "import app.modules.matching.models, app.modules.matching.decision_model",
        "from app.database import create_tables; create_tables()",
    ]
    bootstrap = ";".join(bootstrap_lines)
    subprocess.run([sys.executable, "-c", bootstrap], cwd=str(REPO_ROOT), env=env, check=True)

    # 2) stamp head — create_all 已建当前所有表;不需要再跑增量迁移
    # (这模仿真实开发流: 老 DB 用 create_all 起家,alembic 只管之后的演化)
    subprocess.run(
        ["alembic", "-c", alembic_ini, "stamp", "head"],
        cwd=str(REPO_ROOT), env=env, check=True
    )
    return p


@pytest.fixture(scope="session")
def qa_db_url(qa_db_path):
    return f"sqlite:///{qa_db_path}"


@pytest.fixture(scope="session", autouse=True)
def qa_engine_bound(qa_db_path):
    """把 app.database.engine 重绑到 qa_test_N.db, 让 in-process service 调用走对库.

    根因: app/database.py 在 import time 就根据 settings.database_url(默认 recruitment.db)
    创建 engine; 测试函数若直接 `from app.database import engine/SessionLocal` 走的是
    默认库,导致跨库污染、外键残留、purge/clear-all 等用例失败.
    本 fixture 在 session 启动时把 engine + SessionLocal 重绑到 qa_test_N.db,
    teardown 还原.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    import app.database as _appdb

    new_url = f"sqlite:///{qa_db_path}"
    new_engine = create_engine(new_url, connect_args={"check_same_thread": False})

    @event.listens_for(new_engine, "connect")
    def _pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    old_engine = _appdb.engine
    old_session = _appdb.SessionLocal
    _appdb.engine = new_engine
    _appdb.SessionLocal = sessionmaker(bind=new_engine, autocommit=False, autoflush=False)
    try:
        yield
    finally:
        _appdb.engine = old_engine
        _appdb.SessionLocal = old_session
        new_engine.dispose()
