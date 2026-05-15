"""Job classifier 测试: exact match + LLM 兜底 + 跨用户拒绝 + 无岗位短路."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.job_classifier import classify_candidate_to_job
from app.modules.screening.models import Job


def _make_user(db, uid):
    from sqlalchemy import text
    db.execute(text(
        "INSERT OR IGNORE INTO users (id, username, password_hash, display_name, is_active, daily_cap) "
        "VALUES (:id, :u, 'x', 'Tester', 1, 1000)"
    ), {"id": uid, "u": f"u{uid}"})
    db.commit()


def _make_job(db, uid, jid, title, cm=None):
    j = Job(
        id=jid, user_id=uid, title=title, is_active=True,
        required_skills="", competency_model=cm or {"hard_skills": [], "experience": {"years_min": 0}},
        competency_model_status="approved",
    )
    db.add(j); db.commit()
    return j


def _make_cand(db, uid, name, intention="", skills="", we=""):
    c = IntakeCandidate(
        user_id=uid, boss_id=f"b_{name}", name=name,
        job_intention=intention, skills=skills, work_experience=we,
        intake_status="complete", status="passed",
    )
    db.add(c); db.commit()
    return c


@pytest.mark.asyncio
async def test_exact_match_writes_job_id(db_session):
    _make_user(db_session, 11)
    _make_job(db_session, 11, 7001, "全栈工程师")
    c = _make_cand(db_session, 11, "alice", intention="全栈工程师")

    jid, reason = await classify_candidate_to_job(db_session, c, user_id=11)
    db_session.commit()

    assert jid == 7001
    assert reason == "exact_match"
    assert c.job_id == 7001


@pytest.mark.asyncio
async def test_exact_match_skipped_when_intent_empty(db_session):
    _make_user(db_session, 12)
    _make_job(db_session, 12, 7002, "全栈工程师")
    c = _make_cand(db_session, 12, "bob", intention="")

    mock_llm = AsyncMock(return_value=(7002, "llm_high: 技能匹配"))
    with patch("app.modules.im_intake.job_classifier._llm_classify", mock_llm):
        jid, reason = await classify_candidate_to_job(db_session, c, user_id=12)
    assert mock_llm.called, "intent 为空必须进 LLM"
    assert jid == 7002


@pytest.mark.asyncio
async def test_llm_path_picks_best_job(db_session):
    _make_user(db_session, 13)
    _make_job(db_session, 13, 7003, "AI 工程师")
    c = _make_cand(db_session, 13, "carol", intention="AI Agent 开发", skills="LangChain")

    mock_resp = {"choices": [{"message": {"content": '{"job_id": 7003, "confidence": "high", "reason": "AI 方向"}'}}]}
    async def _post(*a, **kw):
        class R:
            def raise_for_status(self): pass
            def json(self_inner): return mock_resp
        return R()
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = _post
        with patch("app.modules.im_intake.job_classifier.AIProvider") as mock_p:
            mock_p.return_value.is_configured.return_value = True
            mock_p.return_value.base_url = "http://x"
            mock_p.return_value.api_key = "x"
            mock_p.return_value.model = "glm-4-flash"
            jid, reason = await classify_candidate_to_job(db_session, c, user_id=13)
            db_session.commit()
    assert jid == 7003
    assert "llm_high" in reason
    assert c.job_id == 7003


@pytest.mark.asyncio
async def test_llm_rejects_cross_user_job(db_session):
    _make_user(db_session, 14)
    _make_user(db_session, 15)
    _make_job(db_session, 14, 7004, "前端")
    _make_job(db_session, 15, 7005, "后端")  # 不属于 user 14
    c = _make_cand(db_session, 14, "dave", intention="后端开发")

    mock_resp = {"choices": [{"message": {"content": '{"job_id": 7005, "confidence": "high", "reason": "x"}'}}]}
    async def _post(*a, **kw):
        class R:
            def raise_for_status(self): pass
            def json(self_inner): return mock_resp
        return R()
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = _post
        with patch("app.modules.im_intake.job_classifier.AIProvider") as mock_p:
            mock_p.return_value.is_configured.return_value = True
            mock_p.return_value.base_url = "http://x"
            mock_p.return_value.api_key = "x"
            mock_p.return_value.model = "glm-4-flash"
            jid, reason = await classify_candidate_to_job(db_session, c, user_id=14)

    assert jid is None
    assert "cross_user" in reason
    assert c.job_id is None


@pytest.mark.asyncio
async def test_classify_no_active_jobs_returns_none(db_session):
    _make_user(db_session, 16)
    c = _make_cand(db_session, 16, "eve")

    jid, reason = await classify_candidate_to_job(db_session, c, user_id=16)
    assert jid is None
    assert reason == "no_active_jobs"
