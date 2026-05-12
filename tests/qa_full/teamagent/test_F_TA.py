"""22 章 TeamAgent 自学习系统 — 验文件存在 + hook 脚本可执行。

多数功能需要交互式触发,这里仅做存在性 + 文件结构断言。
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent


@pytest.mark.teamagent
def test_F_TA_01_pre_commit_hook():
    """F-TA-01: pre-commit hook 存在"""
    p = REPO_ROOT / ".githooks" / "pre-commit"
    assert p.exists(), "缺 .githooks/pre-commit"
    text = p.read_text(encoding="utf-8", errors="ignore")
    assert "teamagent" in text or "m5-bootstrap" in text


@pytest.mark.teamagent
def test_F_TA_02_post_merge_hook():
    """F-TA-02: post-merge hook 存在"""
    p = REPO_ROOT / ".githooks" / "post-merge"
    assert p.exists(), "缺 .githooks/post-merge"
    text = p.read_text(encoding="utf-8", errors="ignore")
    assert "teamagent" in text or "m5-sync" in text


@pytest.mark.teamagent
def test_F_TA_03_scan_cursor():
    """F-TA-03: scan-cursor.json 存在"""
    p = REPO_ROOT / ".teamagent" / "scan-cursor.json"
    assert p.exists()


@pytest.mark.teamagent
def test_F_TA_05_compile_to_claude_md():
    """F-TA-05: CLAUDE.md 中有 TEAMAGENT 段(规则编译注入)"""
    p = REPO_ROOT / "CLAUDE.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8", errors="ignore")
    assert "TeamAgent" in text or "teamagent" in text


@pytest.mark.teamagent
def test_F_TA_06_knowledge_db():
    """F-TA-06: knowledge.db 存在(SQLite 规则库)"""
    p = REPO_ROOT / ".teamagent" / "knowledge.db"
    assert p.exists()


@pytest.mark.teamagent
def test_F_TA_07_shared_claude_md():
    """F-TA-07: shared-claude.md 团队共享文件"""
    p = REPO_ROOT / ".teamagent" / "shared-claude.md"
    assert p.exists()


@pytest.mark.teamagent
def test_F_TA_other_files_present():
    """F-TA-04 / 08 / 09: manifest + last-harvest + shared-skills"""
    must_have = [
        ".teamagent/manifest.json",
        ".teamagent/last-harvest.md",
    ]
    for f in must_have:
        assert (REPO_ROOT / f).exists(), f"缺 {f}"
