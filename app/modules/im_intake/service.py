import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.config import settings
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot

# Terminal states must match outbox_service.TERMINAL_CANDIDATE_STATES.
# Duplicated here to avoid circular import (outbox_service imports IntakeService).
TERMINAL_CANDIDATE_STATES = ("complete", "abandoned", "pending_human", "timed_out")
from app.modules.im_intake.slot_filler import SlotFiller
from app.modules.im_intake.question_generator import QuestionGenerator
from app.modules.im_intake.pdf_collector import PdfCollector
from app.modules.im_intake.job_matcher import match_job_title
from app.modules.im_intake.decision import decide_next_action, NextAction
from app.modules.im_intake.promote import promote_to_resume
from app.modules.im_intake.staleness import (
    STALE_DAYS, last_message_dt, is_stale,
)
from app.modules.im_intake.templates import HARD_SLOT_KEYS
from app.modules.screening.models import Job
from app.core.audit.logger import log_event

logger = logging.getLogger(__name__)


def _audit_safe(f_stage: str, action: str, entity_id: int, payload: dict | None = None,
                reviewer_id: int | None = None) -> None:
    """Write an F4 audit event; swallow exceptions so audit never breaks intake flow."""
    try:
        log_event(
            f_stage=f_stage, action=action, entity_type="intake_candidate",
            entity_id=entity_id, input_payload=payload, reviewer_id=reviewer_id,
        )
    except Exception as e:
        logger.warning("audit log_event %s failed: %s", f_stage, e)


class IntakeService:
    def __init__(self, db: Session, adapter=None, llm=None,
                 storage_dir: str = "", hard_max_asks: int = 3, pdf_timeout_hours: int = 72,
                 ask_cooldown_hours: int = 6, soft_max_n: int = 3, user_id: int = 0):
        self.db = db
        self.adapter = adapter
        self.llm = llm
        self.filler = SlotFiller(llm=llm)
        self.qg = QuestionGenerator(llm=llm)
        self.pdf = PdfCollector(adapter=adapter, storage_dir=storage_dir, timeout_hours=pdf_timeout_hours) if adapter else None
        self.hard_max_asks = hard_max_asks
        self.pdf_timeout_hours = pdf_timeout_hours
        self.ask_cooldown_hours = ask_cooldown_hours
        self.soft_max_n = soft_max_n
        self.user_id = user_id

    def ensure_candidate(self, boss_id: str, name: str = "",
                         job_intention: str | None = None) -> IntakeCandidate:
        c = (self.db.query(IntakeCandidate)
             .filter_by(user_id=self.user_id, boss_id=boss_id)
             .first())
        if c is None:
            job_id = None
            if job_intention:
                # 多租户隔离: 模糊匹配只能在自家 Job 列表里搜, 否则会把 user A 的
                # 候选人挂到 user B 的 Job (update 分支已正确隔离, 这里补齐 create)。
                jobs = self.db.query(Job).filter_by(user_id=self.user_id).all()
                job_id = match_job_title(
                    job_intention, [{"id": j.id, "title": j.title} for j in jobs], threshold=0.7,
                )
            now = datetime.now(timezone.utc)
            expires_days = getattr(settings, "f4_expires_days", 14)
            c = IntakeCandidate(
                user_id=self.user_id,
                boss_id=boss_id, name=name or "", job_intention=job_intention, job_id=job_id,
                intake_status="collecting", source="plugin",
                intake_started_at=now,
                expires_at=now + timedelta(days=expires_days),
            )
            self.db.add(c); self.db.commit()
            _audit_safe("f4_candidate_enter", "create", c.id,
                        {"boss_id": boss_id, "job_id": job_id, "name": name},
                        reviewer_id=self.user_id or None)
        else:
            # spec 2026-05-15: 命中已存在行时
            #   - 非空 name 回填 (历史行为)
            #   - 新增: job_id 为 NULL 且 job_intention 给了, 用 fuzzy match 兜底回填
            #     (覆盖 F3 老路径建的 NULL 行;非 NULL 不动 — first-write wins)
            dirty = False
            if name and not c.name:
                c.name = name
                dirty = True
            if c.job_id is None and job_intention:
                jobs = self.db.query(Job).filter_by(user_id=self.user_id).all()
                matched = match_job_title(
                    job_intention,
                    [{"id": j.id, "title": j.title} for j in jobs],
                    threshold=0.7,
                )
                if matched:
                    c.job_id = matched
                    dirty = True
                    _audit_safe(
                        "f4_job_id_backfill", "fuzzy_match",
                        c.id,
                        {"boss_id": boss_id, "job_id": matched,
                         "job_intention": job_intention},
                        reviewer_id=self.user_id or None,
                    )
            if dirty:
                self.db.commit()
        return c

    def ensure_slot_rows(self, candidate_id: int) -> dict[str, IntakeSlot]:
        existing = {s.slot_key: s for s in
                    self.db.query(IntakeSlot).filter_by(candidate_id=candidate_id).all()}
        for k in HARD_SLOT_KEYS:
            if k not in existing:
                s = IntakeSlot(candidate_id=candidate_id, slot_key=k, slot_category="hard")
                self.db.add(s); existing[k] = s
        if "pdf" not in existing:
            s = IntakeSlot(candidate_id=candidate_id, slot_key="pdf", slot_category="pdf")
            self.db.add(s); existing["pdf"] = s
        self.db.commit()
        return existing

    async def analyze_chat(self, candidate: IntakeCandidate,
                           messages: list[dict], job: Job | None) -> NextAction:
        slots_by_key = self.ensure_slot_rows(candidate.id)

        # Merge any cached snapshot messages with the freshly-scraped batch
        # so a buggy / truncated DOM scrape doesn't make us regress on what
        # we already knew. Dedupe by (sender_id, content) — Boss messages
        # don't carry stable IDs, but content is enough for our purposes
        # since the candidate doesn't paste identical sentences twice in a
        # short window.
        snapshot_msgs = []
        if candidate.chat_snapshot and isinstance(candidate.chat_snapshot, dict):
            snapshot_msgs = candidate.chat_snapshot.get("messages") or []
        merged_messages = list(messages or [])
        if snapshot_msgs:
            seen = {(m.get("sender_id"), (m.get("content") or "").strip())
                    for m in merged_messages}
            for m in snapshot_msgs:
                key = (m.get("sender_id"), (m.get("content") or "").strip())
                if key not in seen:
                    merged_messages.append(m)
                    seen.add(key)

        # ── 2026-05-18: 7 天无新消息自动归档 ───────────────────────────────
        # 用合并后的 messages 找最近 sent_at, 失败回退到 intake_started_at
        # (反归档时被重置, 给 7 天宽限期)。已经是终态的候选人由后续
        # decide_next_action 内的 terminal guard 处理, 这里不重复判。
        if candidate.intake_status not in TERMINAL_CANDIDATE_STATES:
            now_archive_chk = datetime.now(timezone.utc)
            last_dt = last_message_dt(
                {"messages": merged_messages},
                fallback=candidate.intake_started_at,
            )
            if is_stale(last_dt, now=now_archive_chk):
                candidate.intake_status = "timed_out"
                candidate.intake_completed_at = now_archive_chk
                candidate.reject_reason = f"auto_archive_{STALE_DAYS}d_no_reply"
                self.db.commit()
                # outbox 关掉 pending 问题, 防止 stale 候选人留着 outbox 行
                try:
                    from app.modules.im_intake.outbox_service import (
                        expire_pending_for_candidate as _expire,
                    )
                    _expire(self.db, candidate.id, reason="auto_archive_stale")
                except Exception:
                    pass
                _audit_safe(
                    "f4_auto_archive", "stale_no_reply", candidate.id,
                    {
                        "last_message_dt": last_dt.isoformat() if last_dt else None,
                        "stale_days_threshold": STALE_DAYS,
                    },
                    reviewer_id=self.user_id or None,
                )
                return NextAction(
                    type="archived_stale",
                    text="",
                    meta={"reason": f"超过 {STALE_DAYS} 天无新消息, 自动归档"},
                )

        pending_hard = [k for k in HARD_SLOT_KEYS if not slots_by_key[k].value]
        if merged_messages and pending_hard:
            latest_candidate_msg_at = None
            for m in merged_messages:
                if m.get("sender_id") == candidate.boss_id and m.get("sent_at"):
                    try:
                        ts = datetime.fromisoformat(str(m["sent_at"]).replace("Z", "+00:00"))
                        if latest_candidate_msg_at is None or ts > latest_candidate_msg_at:
                            latest_candidate_msg_at = ts
                    except (ValueError, TypeError):
                        pass

            candidate_msgs = [
                (m.get("sent_at"), (m.get("content") or "").strip())
                for m in merged_messages
                if m.get("sender_id") == candidate.boss_id
            ]

            parsed = await self.filler.parse_conversation(
                merged_messages, candidate.boss_id, pending_hard,
            )
            now = datetime.now(timezone.utc)
            for key, (val, source) in parsed.items():
                s = slots_by_key[key]
                val_str = val if isinstance(val, str) else str(val)
                s.value = val_str
                s.source = source
                s.answered_at = now
                if latest_candidate_msg_at and not s.msg_sent_at:
                    s.msg_sent_at = latest_candidate_msg_at

                phrases = [p.strip() for p in val_str.split(" | ") if p.strip()]
                phrase_ts = []
                for phrase in phrases:
                    matched_at = None
                    for sent_at, content in candidate_msgs:
                        if phrase in content or content in phrase:
                            matched_at = sent_at
                            break
                    phrase_ts.append({"text": phrase, "sent_at": matched_at})
                s.phrase_timestamps = phrase_ts
            self.db.commit()
            _audit_safe("f4_extract_history", "slot_fill", candidate.id,
                        {"filled": list(parsed.keys()), "msg_count": len(messages)},
                        reviewer_id=self.user_id or None)

        # Don't clobber existing chat_snapshot with an empty-messages call —
        # the extension's collect-chat may legitimately pass [] (e.g. just
        # opened the panel before history loaded). Also don't let a SHORTER
        # scrape evict a longer one we already had on file: that's how a
        # flaky DOM read on the extension side can wipe out a previously-
        # complete conversation snapshot. Refresh only when the new batch
        # strictly grows what's stored, OR when there's no snapshot yet.
        existing_count = len(snapshot_msgs)
        if messages and (candidate.chat_snapshot is None or len(messages) >= existing_count):
            candidate.chat_snapshot = {
                "messages": messages,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            self.db.commit()
        elif candidate.chat_snapshot is None:
            candidate.chat_snapshot = {
                "messages": messages or [],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            self.db.commit()

        # Defense-in-depth: if THIS analyze_chat just filled the last unfilled
        # hard slot, any leftover pending/claimed outbox row is now asking a
        # question whose answer is already in. Expire residuals so the outbox
        # poll cannot dispatch a zombie question 30s later. Local import to
        # avoid circular dependency at module load time.
        slots_after = self.db.query(IntakeSlot).filter_by(candidate_id=candidate.id).all()
        slots_by = {s.slot_key: s for s in slots_after}
        all_hard_filled = all(slots_by.get(k) and slots_by[k].value for k in HARD_SLOT_KEYS)
        if all_hard_filled:
            from app.modules.im_intake.outbox_service import expire_pending_for_candidate
            expire_pending_for_candidate(self.db, candidate.id, reason="hard_slots_filled")

        # ── 自愈：聊天记录才是"是否已发问"的真相源 ──────────────────────────
        # 后端"已发问"的认知本来只靠扩展在确认发送成功后回调 ack-sent。但慢网下
        # 发送确认会假失败、或弹窗中途关闭 → ack-sent 不落地 → asked_at 一直是
        # None → decide_next_action 会立即重发同一条硬槽位问题（陈成功重复发问
        # bug）。而问题本身就躺在聊天里：pack_hard() 的每条输出都带下面这个
        # marker。看到它 = 我们问过了，与 ack 回调是否到达无关。
        hr_asked_in_chat = any(
            m.get("sender_id") == "self"
            and "想跟您先确认几个信息" in (m.get("content") or "")
            for m in merged_messages
        )
        if hr_asked_in_chat and not any(
            slots_by[k].asked_at for k in HARD_SLOT_KEYS if k in slots_by
        ):
            heal_now = datetime.now(timezone.utc)
            for k in HARD_SLOT_KEYS:
                s = slots_by.get(k)
                if s and not s.value:
                    s.asked_at = heal_now
                    s.ask_count = max(s.ask_count, 1)
            self.db.commit()

        # BUG-B1 防循环：候选人已多次回复 + 之前问过 + SlotFiller 仍抽不到
        # → 多半是语义不清或 LLM 抽取盲区，再问也是空。转 pending_human 让 HR 介入。
        # 阈值：candidate_msg_count ≥ 2 且 之前问过（ask_count > 0 或聊天里有
        # pack_hard 问题——后者覆盖 ack-sent 漏调导致 ask_count 没涨的情况）。
        still_unfilled = [k for k in HARD_SLOT_KEYS
                          if slots_by.get(k) and not slots_by[k].value]
        if still_unfilled:
            candidate_msg_count = sum(
                1 for m in merged_messages
                if m.get("sender_id") == candidate.boss_id
            )
            already_asked_some = hr_asked_in_chat or any(
                slots_by[k].ask_count > 0
                for k in HARD_SLOT_KEYS if k in slots_by
            )
            if candidate_msg_count >= 2 and already_asked_some:
                candidate.intake_status = "pending_human"
                candidate.intake_completed_at = datetime.now(timezone.utc)
                self.db.commit()
                from app.modules.im_intake.outbox_service import (
                    expire_pending_for_candidate as _expire,
                )
                _expire(self.db, candidate.id, reason="extract_blind_pending_human")
                _audit_safe(
                    "f4_extract_failed_pending_human", "auto_pending", candidate.id,
                    {"unfilled": still_unfilled,
                     "candidate_msg_count": candidate_msg_count},
                    reviewer_id=self.user_id or None,
                )
                return NextAction(type="mark_pending_human")

        slots = list(slots_by.values())  # use fresh re-query, not stale slots_by_key
        action = decide_next_action(
            candidate, slots, job,
            hard_max=self.hard_max_asks,
            pdf_timeout_h=self.pdf_timeout_hours,
            ask_cooldown_h=self.ask_cooldown_hours,
        )

        if action.type == "send_soft":
            dims = action.meta["dimensions"]
            questions = await self.qg.generate_soft(
                dimensions=[{"id": d.get("name"), "name": d.get("name"),
                             "description": d.get("description", "")} for d in dims],
                resume_summary=candidate.raw_text or "",
                max_n=self.soft_max_n,
            )
            if questions:
                action.text = self.qg.pack_soft(questions)
                action.meta["questions"] = questions
            else:
                action = NextAction(type="complete")

        return action

    def record_asked(self, candidate: IntakeCandidate, action: NextAction) -> None:
        # Terminal-state guard — a candidate that is already complete/abandoned/
        # pending_human must NEVER be regressed to awaiting_reply by a late
        # ack from a stale outbox row. Bail out silently so the outbox row can
        # still be flipped to "sent" by the caller for audit purposes.
        if candidate.intake_status in TERMINAL_CANDIDATE_STATES:
            return
        by = {s.slot_key: s for s in
              self.db.query(IntakeSlot).filter_by(candidate_id=candidate.id).all()}
        now = datetime.now(timezone.utc)
        if action.type == "send_hard":
            for k in action.meta.get("slot_keys", []):
                slot = by.get(k)
                if slot is None:
                    continue
                # Skip slots that were filled between question scheduling and
                # ack — incrementing ask_count for an answered slot would push
                # the candidate toward hard_max abandonment for no reason.
                if slot.value:
                    continue
                slot.ask_count += 1
                slot.asked_at = now
                slot.last_ask_text = action.text
            candidate.intake_status = "awaiting_reply"
            _audit_safe("f4_question_sent", "send_hard", candidate.id,
                        {"slot_keys": action.meta.get("slot_keys", []), "text": action.text},
                        reviewer_id=self.user_id or None)
        elif action.type == "request_pdf":
            by["pdf"].ask_count += 1
            by["pdf"].asked_at = now
            by["pdf"].last_ask_text = "求简历按钮"
            _audit_safe("f4_pdf_requested", "request_pdf", candidate.id,
                        {"ask_count": by["pdf"].ask_count},
                        reviewer_id=self.user_id or None)
        elif action.type == "send_soft":
            for i, q in enumerate(action.meta.get("questions", []), 1):
                sk = f"soft_q_{i}"
                s = IntakeSlot(
                    candidate_id=candidate.id, slot_key=sk, slot_category="soft",
                    ask_count=1, asked_at=now, last_ask_text=q["question"],
                    question_meta={"dimension_id": q.get("dimension_id"),
                                   "dimension_name": q.get("dimension_name")},
                )
                self.db.add(s)
            _audit_safe("f4_question_sent", "send_soft", candidate.id,
                        {"question_count": len(action.meta.get("questions", []))},
                        reviewer_id=self.user_id or None)
        self.db.commit()

    def apply_terminal(self, candidate: IntakeCandidate, action: NextAction, user_id: int = 0):
        # Local import to avoid circular dependency (outbox_service imports IntakeService).
        from app.modules.im_intake.outbox_service import expire_pending_for_candidate
        if action.type == "abandon":
            candidate.intake_status = "abandoned"
            candidate.intake_completed_at = datetime.now(timezone.utc)
            self.db.commit()
            expire_pending_for_candidate(self.db, candidate.id, reason="abandon")
            _audit_safe("f4_abandoned", "auto_abandon", candidate.id,
                        {"reason": "pdf_timeout_or_max_asks"}, reviewer_id=user_id or None)
            return None
        if action.type == "mark_pending_human":
            candidate.intake_status = "pending_human"
            candidate.intake_completed_at = datetime.now(timezone.utc)
            self.db.commit()
            expire_pending_for_candidate(self.db, candidate.id, reason="pending_human")
            _audit_safe("f4_pending_human", "auto_mark", candidate.id,
                        {"reason": "hard_max_asks_exhausted"}, reviewer_id=user_id or None)
            return None
        # BUG-013: 移除 timed_out 死分支 —— decide_next_action 从不产生该动作，此分支永远不可达
        # 真正的超时通过 HTTP endpoint POST /candidates/{id}/mark-timed-out 手动触发
        if action.type == "complete":
            resume = promote_to_resume(self.db, candidate, user_id=user_id)
            self.db.commit()
            expire_pending_for_candidate(self.db, candidate.id, reason="complete")
            _audit_safe("f4_completed", "auto_complete", candidate.id,
                        {"promoted_resume_id": getattr(candidate, "promoted_resume_id", None)},
                        reviewer_id=user_id or None)
            return resume
        return None
