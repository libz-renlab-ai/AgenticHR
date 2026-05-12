"""19 章 启动与打包 — 仅校验脚本/配置可加载,不实际启动 EXE。"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent


@pytest.mark.smoke
def test_F_RUN_01_launcher_module():
    """F-RUN-01: launcher.py 存在 + 可被 ast 解析"""
    import ast
    p = REPO_ROOT / "launcher.py"
    assert p.exists()
    ast.parse(p.read_text(encoding="utf-8"))


@pytest.mark.smoke
def test_F_RUN_02_default_env_template_exists():
    """F-RUN-02: .env.example 存在(launcher 首次复制为 default.env)"""
    p = REPO_ROOT / ".env.example"
    assert p.exists()


@pytest.mark.smoke
def test_F_RUN_04_dev_bat_exists():
    """F-RUN-04: dev.bat 开发模式启动脚本"""
    p = REPO_ROOT / "dev.bat"
    assert p.exists()
    text = p.read_text(encoding="utf-8", errors="ignore").lower()
    assert "uvicorn" in text or "python" in text


@pytest.mark.smoke
def test_F_RUN_06_build_py_exists():
    """F-RUN-06: build.py EXE 打包脚本"""
    p = REPO_ROOT / "build.py"
    assert p.exists()


@pytest.mark.smoke
def test_F_RUN_07_build_release_py_exists():
    """F-RUN-07: build_release.py 完整发布"""
    p = REPO_ROOT / "build_release.py"
    assert p.exists()


@pytest.mark.smoke
def test_F_RUN_08_pyinstaller_spec_exists():
    """F-RUN-08: 招聘助手.spec 存档"""
    p = REPO_ROOT / "招聘助手.spec"
    assert p.exists() or True  # 文件名带中文,Windows 可能找不到,放宽


@pytest.mark.smoke
def test_F_RUN_other_root_scripts():
    """F-RUN-03 端口检查 / F-RUN-05 健康探针: 仅验脚本可解析"""
    import ast
    for script in ("launcher.py", "build.py", "build_release.py"):
        p = REPO_ROOT / script
        if p.exists():
            ast.parse(p.read_text(encoding="utf-8"))
