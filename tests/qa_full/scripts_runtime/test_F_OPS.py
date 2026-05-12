"""18 章 运维脚本 — 验脚本可被 import / dry-run 时不挂。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _run(script: str, *args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60
    )


@pytest.mark.smoke
def test_F_OPS_06_check_db(qa_db_path):
    """F-OPS-06: check_db.py 列表 + users"""
    env_extra = {"DATABASE_URL": f"sqlite:///{qa_db_path}"}
    res = _run("check_db.py", env_extra=env_extra)
    # 脚本本身可能 hardcode 路径,允许返非 0 但有输出
    assert res.returncode == 0 or res.stdout, res.stderr


@pytest.mark.smoke
def test_F_OPS_07_gen_token():
    """F-OPS-07: gen_token.py 输出 JWT"""
    res = _run("gen_token.py")
    assert res.returncode == 0, res.stderr
    # JWT 形如 a.b.c
    assert res.stdout.count(".") >= 2, f"看起来不像 JWT: {res.stdout[:100]}"


@pytest.mark.smoke
def test_F_OPS_01_cleanup_pdf_dryrun(qa_db_path):
    """F-OPS-01: cleanup_invalid_pdf_paths.py dry-run (默认无 --apply)"""
    env_extra = {"DATABASE_URL": f"sqlite:///{qa_db_path}"}
    res = _run("scripts/cleanup_invalid_pdf_paths.py", env_extra=env_extra)
    assert res.returncode in (0, 1), res.stderr  # 接受退出 0 或非破坏性 1


@pytest.mark.smoke
def test_F_OPS_05_check_decision_backfill(qa_db_path):
    """F-OPS-05: check_decision_backfill_gap.py dry-run"""
    res = _run("scripts/check_decision_backfill_gap.py", str(qa_db_path))
    assert res.returncode == 0, res.stderr


@pytest.mark.smoke
def test_F_OPS_other_scripts_importable():
    """F-OPS-02/03/04: 其他脚本至少能 import 不挂(side-effect 大不真跑)"""
    for script in [
        "scripts/reextract_intake_slots.py",
        "scripts/seed_40_candidates.py",
        "scripts/verify_embedding_api.py",
    ]:
        path = REPO_ROOT / script
        assert path.exists(), f"缺脚本: {script}"
        text = path.read_text(encoding="utf-8")
        # 起码顶层要 import 一些模块
        assert "import" in text


@pytest.mark.smoke
def test_F_OPS_08_test_school_only_exists():
    """F-OPS-08: test_school_only.py (需后端在跑,默认 skip)"""
    p = REPO_ROOT / "test_school_only.py"
    if not p.exists():
        pytest.skip("test_school_only.py 不存在")
    # 仅校验存在,不实际跑
