"""_ai_parse_core 单测 — 统一的简历解析核心 (单条端点 + 批量 worker 共用)。

根因背景: /resumes 页面列表来自 IntakeCandidate 表 (intake_view_service.list_resume_library),
但旧的批量 worker `_do_parse_all` 只查 Resume 表 → 点"手动启动内容解析"对页面零效果。
这些测试钉死: 批量解析的待办查询必须覆盖 IntakeCandidate, 解析核心对 candidate 与
Resume 两种 target 行为一致。
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.resume.models import Resume


def _candidate(db, **overrides):
    fields = dict(
        user_id=1, boss_id="b-default", name="张三",
        raw_text="张三的简历原文，五年后端经验。", ai_parsed="no",
        intake_status="complete", source="plugin",
        intake_started_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    c = IntakeCandidate(**fields)
    db.add(c)
    db.commit()
    return c


# ---------- 根因: 批量待办查询必须覆盖 IntakeCandidate ----------

def test_query_pending_includes_unparsed_intake_candidate(db_session):
    """简历库页面显示的是 IntakeCandidate; 批量解析必须能查到它们。"""
    from app.modules.resume._ai_parse_core import query_pending_targets

    c = _candidate(db_session, boss_id="b1")

    targets = query_pending_targets(db_session, user_id=1)

    assert any(isinstance(t, IntakeCandidate) and t.id == c.id for t in targets), \
        "ai_parsed='no' 的 IntakeCandidate 应进入批量解析待办"


def test_query_pending_excludes_already_parsed_candidate(db_session):
    """ai_parsed='yes' 的候选人不应被重复排队。"""
    from app.modules.resume._ai_parse_core import query_pending_targets

    done = _candidate(db_session, boss_id="b-done", ai_parsed="yes")

    targets = query_pending_targets(db_session, user_id=1)

    assert all(t.id != done.id for t in targets if isinstance(t, IntakeCandidate))


def test_query_pending_skips_candidate_without_parseable_input(db_session):
    """既无 pdf_path 也无 raw_text 的候选人无可解析输入, 不应排队。"""
    from app.modules.resume._ai_parse_core import query_pending_targets

    blank = _candidate(db_session, boss_id="b-blank", raw_text="", pdf_path=None)

    targets = query_pending_targets(db_session, user_id=1)

    assert all(t.id != blank.id for t in targets if isinstance(t, IntakeCandidate))


def test_query_pending_is_user_scoped(db_session):
    """worker 由某用户触发, 只应解析该用户自己的候选人。"""
    from app.modules.resume._ai_parse_core import query_pending_targets

    mine = _candidate(db_session, boss_id="b-mine", user_id=1)
    other = _candidate(db_session, boss_id="b-other", user_id=2)

    targets = query_pending_targets(db_session, user_id=1)

    ids = {t.id for t in targets if isinstance(t, IntakeCandidate)}
    assert mine.id in ids
    assert other.id not in ids


def test_query_pending_includes_orphan_resume(db_session):
    """无 owning IntakeCandidate 的遗留 Resume 行 (intake_candidate_id 为空) 仍要兜底解析。"""
    from app.modules.resume._ai_parse_core import query_pending_targets

    orphan = Resume(user_id=1, name="老王", raw_text="老王的简历原文", ai_parsed="no")
    db_session.add(orphan)
    db_session.commit()

    targets = query_pending_targets(db_session, user_id=1)

    assert any(isinstance(t, Resume) and t.id == orphan.id for t in targets)


# ---------- 统一解析核心: candidate 入口 ----------

def test_ai_parse_target_parses_candidate_and_promotes(db_session):
    """对 IntakeCandidate 调 ai_parse_target: 标 ai_parsed='yes', 字段落库,
    并 promote 出 Resume (matching 以 Resume 为 FK 锚点)。"""
    from app.modules.resume._ai_parse_core import ai_parse_target

    c = _candidate(db_session, boss_id="b-parse", name="未知")

    fake_parsed = {
        "name": "李雷", "skills": "Python, FastAPI",
        "work_experience": "字节跳动 后端", "education": "本科", "work_years": "5",
    }

    async def _fake_parse(raw_text, ai_provider):
        return fake_parsed

    with patch("app.modules.resume.pdf_parser.ai_parse_resume", _fake_parse):
        status, score_resume_id = asyncio.run(
            ai_parse_target(c, ai=object(), db=db_session)
        )

    db_session.refresh(c)
    assert status == "yes"
    assert c.ai_parsed == "yes"
    assert c.skills == "Python, FastAPI"
    # promote 出的 Resume
    assert c.promoted_resume_id is not None
    assert score_resume_id == c.promoted_resume_id
    resume = db_session.query(Resume).filter_by(id=c.promoted_resume_id).first()
    assert resume is not None
    assert resume.ai_parsed == "yes"
    assert resume.skills == "Python, FastAPI"


def test_ai_parse_target_marks_failed_when_llm_returns_empty(db_session):
    """LLM 返空 dict → target 标 ai_parsed='failed', 不抛异常 (worker 要继续下一条)。"""
    from app.modules.resume._ai_parse_core import ai_parse_target

    c = _candidate(db_session, boss_id="b-fail")

    async def _fake_parse_empty(raw_text, ai_provider):
        return {}

    with patch("app.modules.resume.pdf_parser.ai_parse_resume", _fake_parse_empty):
        status, score_resume_id = asyncio.run(
            ai_parse_target(c, ai=object(), db=db_session)
        )

    db_session.refresh(c)
    assert status == "failed"
    assert c.ai_parsed == "failed"
