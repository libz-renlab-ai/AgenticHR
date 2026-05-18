from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal, Any
from app.modules.im_intake.templates import HARD_SLOT_KEYS
from app.modules.im_intake.question_generator import QuestionGenerator

ActionType = Literal[
    "send_hard", "request_pdf", "wait_pdf", "wait_reply",
    "send_soft", "complete", "mark_pending_human", "abandon",
    # 2026-05-18 新增: 7 天无消息自动归档 / 入库前 stale 拦截
    "archived_stale", "skipped_stale_new",
]


@dataclass
class NextAction:
    type: ActionType
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _slots_by_key(slots):
    return {s.slot_key: s for s in slots}


def decide_next_action(
    candidate, slots, job,
    hard_max: int = 3, pdf_timeout_h: int = 72, ask_cooldown_h: int = 6,
) -> NextAction:
    by = _slots_by_key(slots)
    # BUG-049: empty slots list ⇒ ``all([]) == True`` would falsely report
    # all hard slots filled and short-circuit to ``complete``. A candidate
    # without any slot rows must be treated as fully unanswered: pending_human
    # so an HR reviews before promoting a zero-info row.
    if not by:
        return NextAction(type="mark_pending_human")
    pdf = by.get("pdf")
    now = datetime.now(timezone.utc)

    def _asked_at(s):
        if s.asked_at is None:
            return None
        return s.asked_at if s.asked_at.tzinfo else s.asked_at.replace(tzinfo=timezone.utc)

    if pdf and not pdf.value and pdf.asked_at:
        pdf_asked = _asked_at(pdf)
        if now - pdf_asked > timedelta(hours=pdf_timeout_h):
            return NextAction(type="abandon")

    hard_unfilled = [k for k in HARD_SLOT_KEYS
                     if k in by and not by[k].value and by[k].ask_count < hard_max]

    def _cooled(k):
        a = _asked_at(by[k])
        return a is None or (now - a) >= timedelta(hours=ask_cooldown_h)

    pending = [k for k in hard_unfilled if _cooled(k)]
    if pending:
        qg = QuestionGenerator(llm=None)
        missing = [(k, by[k].ask_count) for k in pending]
        text = qg.pack_hard(
            candidate_name=getattr(candidate, "name", ""),
            job_title=getattr(job, "title", "") if job else "",
            missing=missing,
        )
        return NextAction(type="send_hard", text=text, meta={"slot_keys": pending})

    # 有待填槽位但都在冷却期内 — 等对方回复，先别打扰
    if hard_unfilled:
        return NextAction(type="wait_reply")

    if pdf and not pdf.value:
        if pdf.ask_count == 0:
            return NextAction(type="request_pdf")
        return NextAction(type="wait_pdf")

    hard_filled = all(by[k].value for k in HARD_SLOT_KEYS if k in by)
    if not hard_filled:
        return NextAction(type="mark_pending_human")

    soft_sent = any(s.slot_category == "soft" for s in slots)
    dims = (getattr(job, "competency_model", None) or {}).get("assessment_dimensions") if job else None
    if dims and not soft_sent:
        return NextAction(type="send_soft", meta={"dimensions": dims})

    return NextAction(type="complete")
