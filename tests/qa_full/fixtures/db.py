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
