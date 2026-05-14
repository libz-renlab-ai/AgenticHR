"""TDD: 陈成功 重复发问 bug.

Root cause (confirmed via DB id=334 + audit trail): the extension sent a
hard-slot question but `intake_typeAndSendChatMessage` returned ok:false
(slow-network false-failure), so `ack-sent` was never called and the
backend recorded nothing — asked_at=None, ask_count=0. On the next scan
`decide_next_action` sees asked_at=None → `_cooled()` True → re-sends the
SAME question. The BUG-B1 anti-loop guard that should catch this is
bypassed because it gates on `ask_count > 0`.

Fix: the chat history is the source of truth for "did we ask". A "self"
message carrying the pack_hard marker proves we already asked, even when
ack-sent never landed. analyze_chat must self-heal from that evidence
instead of trusting only the ack callback.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

import pytest

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.service import IntakeService
from app.modules.im_intake.templates import HARD_SLOT_KEYS

# The exact marker every QuestionGenerator.pack_hard() output contains
# (both the with-job and without-job branches).
HARD_Q_MARKER = "想跟您先确认几个信息"


class _NullFiller:
    """SlotFiller stub that always extracts nothing — extraction blind spot."""
    def __init__(self, *a, **kw):
        pass

    async def parse_conversation(self, messages, boss_id, pending_keys):
        return {}


def _mk_unrecorded_candidate(db_session, boss_id):
    """Candidate whose hard slots are blank with asked_at=None / ask_count=0 —
    i.e. a question WAS put on the wire but ack-sent never recorded it."""
    c = IntakeCandidate(
        boss_id=boss_id, name="陈成功", intake_status="collecting",
        source="plugin", user_id=1,
    )
    db_session.add(c)
    db_session.flush()
    for k in HARD_SLOT_KEYS:
        db_session.add(IntakeSlot(
            candidate_id=c.id, slot_key=k, slot_category="hard",
            ask_count=0, asked_at=None,
        ))
    db_session.add(IntakeSlot(candidate_id=c.id, slot_key="pdf", slot_category="pdf"))
    db_session.commit()
    return c


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _hard_question_msg():
    """A 'self' message in the shape pack_hard() produces."""
    return {"sender_id": "self",
            "content": f"您好陈成功~{HARD_Q_MARKER}：1. 请问您最快什么时候可以到岗呢？"}


def test_unrecorded_ask_plus_two_replies_does_not_resend(db_session):
    """陈成功 core case: an (unrecorded) hard question sits in the chat, the
    candidate has replied twice, extraction is blind → must NOT send_hard
    again; route to pending_human for HR review instead."""
    c = _mk_unrecorded_candidate(db_session, boss_id="bxChen2")
    svc = IntakeService(db=db_session, llm=None, user_id=1, ask_cooldown_hours=6)
    svc.filler = _NullFiller()
    msgs = [
        _hard_question_msg(),
        {"sender_id": "bxChen2", "content": "您好，请问还招25届的吗？"},
        {"sender_id": "bxChen2", "content": "1.面试通过就入职 3.最少3个月"},
    ]
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    assert action.type != "send_hard", "must not re-send an already-asked question"
    assert action.type == "mark_pending_human"
    db_session.expire_all()
    c2 = db_session.query(IntakeCandidate).filter_by(id=c.id).one()
    assert c2.intake_status == "pending_human"


def test_unrecorded_ask_plus_one_reply_waits_not_resend(db_session):
    """Only one reply so far: not enough to give up to a human, but the
    backend still must not re-send the question it just asked — wait_reply."""
    c = _mk_unrecorded_candidate(db_session, boss_id="bxChen1")
    svc = IntakeService(db=db_session, llm=None, user_id=1, ask_cooldown_hours=6)
    svc.filler = _NullFiller()
    msgs = [
        _hard_question_msg(),
        {"sender_id": "bxChen1", "content": "1.面试通过就入职"},
    ]
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    assert action.type != "send_hard", "must not re-send within cooldown of an unrecorded ask"
    assert action.type == "wait_reply"


def test_no_hard_question_in_chat_still_sends_first_ask(db_session):
    """Regression guard: a candidate genuinely never asked (no pack_hard
    marker anywhere in the chat) must still get their first send_hard."""
    c = _mk_unrecorded_candidate(db_session, boss_id="bxFresh")
    svc = IntakeService(db=db_session, llm=None, user_id=1, ask_cooldown_hours=6)
    svc.filler = _NullFiller()
    msgs = [{"sender_id": "bxFresh", "content": "您好，看到招聘信息，很感兴趣"}]
    action = _run(svc.analyze_chat(c, messages=msgs, job=None))
    assert action.type == "send_hard", "first ask must not be suppressed by the self-heal"
