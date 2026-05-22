"""7 天无新消息自动归档 + 入库前拦截 + 反归档 - 集成测试.

spec: docs/superpowers/specs/2026-05-18-7d-stale-archive-design.md
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.service import IntakeService


# ─── helpers ─────────────────────────────────────────────────────────────────

NOW = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)


def _mk_candidate(db, *, boss_id="b1", status="awaiting_reply",
                  intake_started_at=None, chat_snapshot=None, user_id=1):
    c = IntakeCandidate(
        user_id=user_id,
        boss_id=boss_id,
        name="张三",
        intake_status=status,
        intake_started_at=intake_started_at or (NOW - timedelta(days=30)),
        chat_snapshot=chat_snapshot,
        source="plugin",
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _msg(sender, content, days_ago, base_now=NOW):
    return {
        "sender_id": sender,
        "content": content,
        "sent_at": (base_now - timedelta(days=days_ago)).isoformat(),
    }


def _build_svc(db, user_id=1):
    """Minimal IntakeService construction for analyze_chat test calls.

    llm=None 让 SlotFiller / QuestionGenerator 拿不到 LLM 客户端, 但本测试
    仅触达 analyze_chat 顶部的 stale 归档分支(早 return), 不会调到它们。
    """
    return IntakeService(db, adapter=None, llm=None, user_id=user_id)


# ─── Phase 2 测试: analyze_chat 内的归档判定 ─────────────────────────────────


class TestAnalyzeChatStaleArchive:
    """analyze_chat 入口的 stale 归档逻辑.

    通过直接调用 service 层而非 HTTP 路径, 避免依赖整套 SlotFiller / LLM。
    """

    @pytest.mark.asyncio
    async def test_recent_message_normal_flow_no_archive(self, db_session):
        """最后消息 1 天前 → 不归档, 走原 decide_next_action 流程."""
        c = _mk_candidate(db_session)
        # 直接调归档检查相关逻辑 (走 analyze_chat 前半段 + 提前 return)
        # 但 analyze_chat 后续会调 SlotFiller etc, 这里只验证 stale 不触发。
        from app.modules.im_intake.staleness import last_message_dt, is_stale
        snapshot = {"messages": [_msg("self", "你好", 1)]}
        last_dt = last_message_dt(snapshot, fallback=c.intake_started_at)
        assert not is_stale(last_dt, now=NOW)

    @pytest.mark.asyncio
    async def test_stale_message_in_chat_snapshot_archives(self, db_session):
        """已入库候选人, chat_snapshot 最后消息 20 天前 → analyze_chat 归档为 timed_out (阈值 14 天)."""
        c = _mk_candidate(
            db_session,
            chat_snapshot={"messages": [_msg("boss", "我考虑下", 20)]},
        )
        svc = _build_svc(db_session)
        action = await svc.analyze_chat(c, messages=[], job=None)
        db_session.refresh(c)
        assert action.type == "archived_stale"
        assert c.intake_status == "timed_out"
        assert "auto_archive_14d_no_reply" == c.reject_reason
        assert c.intake_completed_at is not None

    @pytest.mark.asyncio
    async def test_terminal_candidate_skipped_by_archive_check(self, db_session):
        """已 complete 候选人不应被归档逻辑覆盖 (终态守卫)."""
        c = _mk_candidate(
            db_session,
            status="complete",
            chat_snapshot={"messages": [_msg("boss", "ok", 30)]},
        )
        svc = _build_svc(db_session)
        action = await svc.analyze_chat(c, messages=[], job=None)
        db_session.refresh(c)
        # 终态守卫: status 不变, 不归档
        assert c.intake_status == "complete"
        assert c.reject_reason == ""
        assert action.type != "archived_stale"

    @pytest.mark.asyncio
    async def test_empty_snapshot_uses_started_at_fallback(self, db_session):
        """chat_snapshot 空 + intake_started_at 在 7 天内 → 不归档."""
        c = _mk_candidate(
            db_session,
            chat_snapshot=None,
            intake_started_at=NOW - timedelta(days=2),
        )
        from app.modules.im_intake.staleness import last_message_dt, is_stale
        last_dt = last_message_dt({"messages": []}, fallback=c.intake_started_at)
        assert not is_stale(last_dt, now=NOW)


# ─── Phase 3 测试: collect-chat 入库前拦截 ──────────────────────────────────


class TestCollectChatStaleIngest:
    """POST /api/intake/collect-chat 入库前 stale 拦截."""

    def test_new_candidate_with_stale_messages_does_not_create(self, client, db_session):
        """新 boss_id, messages 最后一条 20 天前 → 不入库 (阈值 14 天)."""
        # 现实里 sent_at 是 ISO 字符串
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        resp = client.post("/api/intake/collect-chat", json={
            "boss_id": "stale_new_b1",
            "name": "陈陈陈",
            "messages": [
                {"sender_id": "self", "content": "您好", "sent_at": old_ts},
            ],
            "pdf_present": False,
            "skip_outbox": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["next_action"]["type"] == "skipped_stale_new"
        assert body["candidate_id"] is None
        assert body["intake_status"] == "skipped_stale_new"
        # DB 也确实没有创建
        c = (db_session.query(IntakeCandidate)
             .filter_by(user_id=1, boss_id="stale_new_b1").first())
        assert c is None

    def test_new_candidate_with_fresh_messages_creates_normally(self, client, db_session):
        """新 boss_id, 最后消息 1 天前 → 正常入库."""
        fresh_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = client.post("/api/intake/collect-chat", json={
            "boss_id": "fresh_new_b1",
            "name": "新人",
            "messages": [
                {"sender_id": "self", "content": "你好", "sent_at": fresh_ts},
            ],
            "pdf_present": False,
            "skip_outbox": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 不应该是 skipped_stale_new
        assert body["next_action"]["type"] != "skipped_stale_new"
        assert body["candidate_id"] is not None
        c = (db_session.query(IntakeCandidate)
             .filter_by(user_id=1, boss_id="fresh_new_b1").first())
        assert c is not None

    def test_existing_candidate_not_affected_by_pre_ingest_check(self, client, db_session):
        """已有 candidate (即便 messages 旧) 不应被入库前拦截路径处理 ——
        走 analyze_chat 内的归档分支, candidate.id 保留 + status 改为 timed_out。
        """
        # 预置一个已存在 candidate, 但聊天历史是 20 天前 (阈值 14 天)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        c = IntakeCandidate(
            user_id=1, boss_id="existing_old_b1", name="老候选人",
            intake_status="awaiting_reply",
            intake_started_at=datetime.now(timezone.utc) - timedelta(days=15),
            chat_snapshot={"messages": [
                {"sender_id": "boss", "content": "ok", "sent_at": old_ts},
            ]},
            source="plugin",
        )
        db_session.add(c); db_session.commit(); db_session.refresh(c)
        cid = c.id

        resp = client.post("/api/intake/collect-chat", json={
            "boss_id": "existing_old_b1",
            "name": "老候选人",
            "messages": [],  # 现场抓的 messages 为空, 强制走 candidate.chat_snapshot
            "pdf_present": False,
            "skip_outbox": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 不应是 skipped_stale_new (existing candidate 不走入库前拦)
        assert body["next_action"]["type"] != "skipped_stale_new"
        # candidate 仍保留, 但应被归档
        db_session.expire_all()
        c2 = db_session.get(IntakeCandidate, cid)
        assert c2 is not None
        assert c2.intake_status == "timed_out"
        assert c2.reject_reason == "auto_archive_14d_no_reply"


# ─── Phase 4 测试: 反归档端点 ─────────────────────────────────────────────


class TestUnarchive:
    """POST /api/intake/candidates/{id}/unarchive."""

    def test_unarchive_timed_out_to_awaiting_reply(self, client, db_session):
        c = IntakeCandidate(
            user_id=1, boss_id="archived_b1", name="待恢复",
            intake_status="timed_out",
            intake_started_at=datetime.now(timezone.utc) - timedelta(days=30),
            intake_completed_at=datetime.now(timezone.utc) - timedelta(days=1),
            reject_reason="auto_archive_14d_no_reply",
            source="plugin",
        )
        db_session.add(c); db_session.commit(); db_session.refresh(c)
        cid = c.id

        resp = client.post(f"/api/intake/candidates/{cid}/unarchive")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "awaiting_reply"

        db_session.expire_all()
        c2 = db_session.get(IntakeCandidate, cid)
        assert c2.intake_status == "awaiting_reply"
        assert c2.intake_completed_at is None
        assert c2.reject_reason == ""

    def test_unarchive_resets_intake_started_at(self, client, db_session):
        """关键: 反归档要把 intake_started_at 设为 now, 给 7 天宽限."""
        old_start = datetime.now(timezone.utc) - timedelta(days=30)
        c = IntakeCandidate(
            user_id=1, boss_id="reset_b1", name="x",
            intake_status="timed_out",
            intake_started_at=old_start,
            reject_reason="auto_archive_14d_no_reply",
            source="plugin",
        )
        db_session.add(c); db_session.commit(); db_session.refresh(c)
        cid = c.id

        before = datetime.now(timezone.utc)
        resp = client.post(f"/api/intake/candidates/{cid}/unarchive")
        after = datetime.now(timezone.utc)
        assert resp.status_code == 200

        db_session.expire_all()
        c2 = db_session.get(IntakeCandidate, cid)
        new_start = c2.intake_started_at
        if new_start.tzinfo is None:
            new_start = new_start.replace(tzinfo=timezone.utc)
        # 重置后 intake_started_at 应接近 now (大于 before, 小于 after, 允许 1s 偏差)
        assert before - timedelta(seconds=1) <= new_start <= after + timedelta(seconds=1)
        # 远远大于原始 old_start
        assert new_start > old_start + timedelta(days=20)

    def test_unarchive_rejects_non_timed_out_status(self, client, db_session):
        for bad_status in ["complete", "abandoned", "awaiting_reply", "collecting", "pending_human"]:
            c = IntakeCandidate(
                user_id=1, boss_id=f"bad_{bad_status}", name="x",
                intake_status=bad_status, source="plugin",
            )
            db_session.add(c); db_session.commit(); db_session.refresh(c)
            resp = client.post(f"/api/intake/candidates/{c.id}/unarchive")
            assert resp.status_code == 400, f"{bad_status}: {resp.text}"
            assert "timed_out" in resp.json()["detail"]
            db_session.delete(c); db_session.commit()

    def test_unarchive_404_for_other_users_candidate(self, client, db_session):
        c = IntakeCandidate(
            user_id=2,  # 不是测试 client 的 user_id=1
            boss_id="other_user_b1", name="x",
            intake_status="timed_out", source="plugin",
        )
        db_session.add(c); db_session.commit(); db_session.refresh(c)
        resp = client.post(f"/api/intake/candidates/{c.id}/unarchive")
        assert resp.status_code == 404

    def test_unarchive_then_collect_chat_no_immediate_re_archive(self, client, db_session):
        """闭环验证: HTTP 反归档 → 立即 POST collect-chat (chat 仍含老消息)
        → 不会立即归档, 因为 last_message_dt 取 max(chat_ts, intake_started_at)
        而反归档已重置 intake_started_at = now。"""
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        c = IntakeCandidate(
            user_id=1, boss_id="loop_b1", name="x",
            intake_status="timed_out",
            intake_started_at=datetime.now(timezone.utc) - timedelta(days=30),
            chat_snapshot={"messages": [
                {"sender_id": "boss", "content": "ok", "sent_at": ten_days_ago},
            ]},
            reject_reason="auto_archive_14d_no_reply",
            source="plugin",
        )
        db_session.add(c); db_session.commit(); db_session.refresh(c)
        cid = c.id

        # 1. 反归档
        resp = client.post(f"/api/intake/candidates/{cid}/unarchive")
        assert resp.status_code == 200, resp.text

        # 2. 立即 POST collect-chat, body.messages 同样是 10 天前老消息
        resp2 = client.post("/api/intake/collect-chat", json={
            "boss_id": "loop_b1",
            "name": "x",
            "messages": [
                {"sender_id": "boss", "content": "ok", "sent_at": ten_days_ago},
            ],
            "pdf_present": False,
            "skip_outbox": True,
        })
        assert resp2.status_code == 200, resp2.text

        # 3. 验证: 状态不应再次变成 timed_out
        db_session.expire_all()
        c2 = db_session.get(IntakeCandidate, cid)
        assert c2.intake_status != "timed_out", (
            f"反归档后立即 collect-chat 不应再次归档, 实际 status={c2.intake_status}"
        )
