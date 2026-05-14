"""TDD for B1: analyze_chat must transition to pending_human when the candidate
has answered repeatedly but SlotFiller still cannot extract values, breaking
the "ask → answer → can't extract → ask again" loop.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.service import IntakeService
from app.modules.im_intake.templates import HARD_SLOT_KEYS


class _NullFiller:
    """SlotFiller stub that always extracts nothing — simulates extraction blind spot."""
    def __init__(self, *a, **kw): pass
    async def parse_conversation(self, messages, boss_id, pending_keys):
        return {}


class _PerfectFiller:
    """SlotFiller stub that extracts every pending slot."""
    def __init__(self, *a, **kw): pass
    async def parse_conversation(self, messages, boss_id, pending_keys):
        return {k: ("有值", "llm") for k in pending_keys}


def _mk_candidate(db_session, name="张三", boss_id="bxLoop", chat_snapshot=None,
                  already_asked=False):
    c = IntakeCandidate(
        boss_id=boss_id,
        name=name,
        intake_status="awaiting_reply" if already_asked else "collecting",
        source="plugin",
        chat_snapshot=chat_snapshot,
        user_id=1,
    )
    db_session.add(c)
    db_session.flush()
    now = datetime.now(timezone.utc)
    for k in HARD_SLOT_KEYS:
        s = IntakeSlot(
            candidate_id=c.id, slot_key=k, slot_category="hard",
            ask_count=1 if already_asked else 0,
            asked_at=now - timedelta(hours=24) if already_asked else None,
        )
        db_session.add(s)
    db_session.add(IntakeSlot(candidate_id=c.id, slot_key="pdf", slot_category="pdf"))
    db_session.commit()
    return c


def _run(coro):
    # asyncio.run() 每次起独立 loop 并收尾, 不依赖"当前 loop"全局状态。
    # 旧写法 asyncio.get_event_loop() 在任何先跑过 asyncio.run() 的会话里会取到
    # None 而抛 RuntimeError —— 让本文件能否通过取决于测试执行顺序。
    return asyncio.run(coro)


def _msgs(boss_id, n_candidate_replies):
    """n_candidate_replies = how many messages the candidate has sent."""
    out = [{"sender_id": "self", "content": "你好，请问几号能到岗？"}]
    for i in range(n_candidate_replies):
        out.append({"sender_id": boss_id, "content": f"我可以下周一到岗 {i}"})
    return out


def test_new_candidate_no_replies_still_send_hard(db_session):
    c = _mk_candidate(db_session, boss_id="bxNew")
    svc = IntakeService(db=db_session, llm=None, user_id=1)
    svc.filler = _NullFiller()
    action = _run(svc.analyze_chat(c, messages=[], job=None))
    assert action.type in ("send_hard", "request_pdf", "wait_pdf", "wait_reply",
                           "mark_pending_human")
    # New candidate (0 replies, never asked) should not be auto-routed to
    # pending_human just because slots are blank — that's normal initial state.
    if action.type == "mark_pending_human":
        # only acceptable if no slot rows existed; here we created them
        pytest.fail("new candidate should not auto-mark_pending_human")


def test_one_reply_already_asked_still_send_hard(db_session):
    c = _mk_candidate(db_session, boss_id="bxOne", already_asked=True)
    svc = IntakeService(db=db_session, llm=None, user_id=1, ask_cooldown_hours=1)
    svc.filler = _NullFiller()
    msgs = _msgs("bxOne", n_candidate_replies=1)
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    # Single reply: extractor may have just missed first attempt; give it room.
    assert action.type != "mark_pending_human"


def test_two_replies_already_asked_extractor_blind_marks_pending_human(db_session):
    c = _mk_candidate(db_session, boss_id="bxLoop2", already_asked=True)
    svc = IntakeService(db=db_session, llm=None, user_id=1, ask_cooldown_hours=1)
    svc.filler = _NullFiller()
    msgs = _msgs("bxLoop2", n_candidate_replies=2)
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    assert action.type == "mark_pending_human"
    db_session.expire_all()
    c2 = db_session.query(IntakeCandidate).filter_by(id=c.id).one()
    assert c2.intake_status == "pending_human"


def test_two_replies_extractor_succeeds_does_not_pending_human(db_session):
    c = _mk_candidate(db_session, boss_id="bxFilled", already_asked=True)
    svc = IntakeService(db=db_session, llm=None, user_id=1, ask_cooldown_hours=1)
    svc.filler = _PerfectFiller()
    msgs = _msgs("bxFilled", n_candidate_replies=2)
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    assert action.type != "mark_pending_human"
    db_session.expire_all()
    c2 = db_session.query(IntakeCandidate).filter_by(id=c.id).one()
    assert c2.intake_status != "pending_human"


def test_two_replies_never_asked_does_not_pending_human(db_session):
    """Edge: chat_snapshot has 2 replies but ask_count=0 (e.g. external imports)."""
    c = _mk_candidate(db_session, boss_id="bxImport", already_asked=False)
    svc = IntakeService(db=db_session, llm=None, user_id=1)
    svc.filler = _NullFiller()
    msgs = _msgs("bxImport", n_candidate_replies=2)
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    # Must give this candidate at least one ask attempt before marking
    # pending_human; otherwise importer floods would all auto-pend.
    assert action.type != "mark_pending_human"
