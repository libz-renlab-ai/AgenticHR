"""staleness 纯函数 — 时间起点解析 + 陈旧判定."""
from datetime import datetime, timezone, timedelta

from app.modules.im_intake.staleness import (
    STALE_DAYS, last_message_dt, is_stale,
)


class TestLastMessageDt:
    def test_returns_last_valid_sent_at(self):
        snap = {"messages": [
            {"sender_id": "self", "content": "你好", "sent_at": "2026-05-10T08:00:00+00:00"},
            {"sender_id": "boss", "content": "您好", "sent_at": "2026-05-11T09:00:00+00:00"},
        ]}
        r = last_message_dt(snap)
        assert r == datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc)

    def test_falls_back_when_all_missing(self):
        snap = {"messages": [
            {"sender_id": "self", "content": "你好"},
            {"sender_id": "boss", "content": "您好", "sent_at": None},
        ]}
        fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert last_message_dt(snap, fallback=fallback) == fallback

    def test_empty_snapshot_uses_fallback(self):
        fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert last_message_dt({"messages": []}, fallback=fallback) == fallback
        assert last_message_dt(None, fallback=fallback) == fallback
        assert last_message_dt({}, fallback=fallback) == fallback

    def test_parses_z_suffix(self):
        snap = {"messages": [
            {"sender_id": "boss", "content": "hi", "sent_at": "2026-05-10T08:00:00Z"},
        ]}
        r = last_message_dt(snap)
        assert r is not None
        assert r.tzinfo is not None
        assert r == datetime(2026, 5, 10, 8, 0, tzinfo=timezone.utc)

    def test_skips_invalid_then_uses_prior(self):
        """最新一条 sent_at 格式坏 → 跳过, 用倒数第二条."""
        snap = {"messages": [
            {"sender_id": "self", "content": "1", "sent_at": "2026-05-09T08:00:00Z"},
            {"sender_id": "boss", "content": "2", "sent_at": "not-a-date"},
        ]}
        r = last_message_dt(snap)
        assert r == datetime(2026, 5, 9, 8, 0, tzinfo=timezone.utc)

    def test_messages_key_missing_uses_fallback(self):
        fallback = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert last_message_dt({"foo": "bar"}, fallback=fallback) == fallback

    def test_fallback_newer_than_chat_wins(self):
        """反归档场景: chat 有真实老消息 + fallback 是 now → 应返回 fallback.
        这是给反归档候选人 7 天宽限期的关键 — 否则反归档后立即又被归档。"""
        snap = {"messages": [
            {"sender_id": "boss", "content": "ok", "sent_at": "2026-04-01T08:00:00Z"},
        ]}
        # fallback (反归档时被重置的 intake_started_at) 比 chat 新
        fallback_now = datetime(2026, 5, 18, tzinfo=timezone.utc)
        r = last_message_dt(snap, fallback=fallback_now)
        assert r == fallback_now

    def test_chat_newer_than_fallback_wins(self):
        """正常活跃候选人: chat 新消息 + intake_started_at 是 30 天前
        → 应返回 chat 时间 (业务行为不变)."""
        snap = {"messages": [
            {"sender_id": "boss", "content": "我刚回复", "sent_at": "2026-05-17T08:00:00Z"},
        ]}
        old_fallback = datetime(2026, 4, 1, tzinfo=timezone.utc)
        r = last_message_dt(snap, fallback=old_fallback)
        assert r == datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc)


class TestIsStale:
    def _now(self):
        return datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    def test_recent_dt_not_stale(self):
        now = self._now()
        assert is_stale(now - timedelta(days=3), now=now) is False
        assert is_stale(now - timedelta(days=6, hours=23), now=now) is False

    def test_old_dt_stale(self):
        now = self._now()
        assert is_stale(now - timedelta(days=8), now=now) is True
        assert is_stale(now - timedelta(days=30), now=now) is True

    def test_exactly_at_threshold_not_stale(self):
        """边界: 恰好 7 天前不算陈旧 (> 而非 >=)."""
        now = self._now()
        assert is_stale(now - timedelta(days=7), now=now) is False

    def test_just_past_threshold_stale(self):
        now = self._now()
        assert is_stale(now - timedelta(days=7, seconds=1), now=now) is True

    def test_none_returns_false(self):
        """信息不足倾向放过."""
        assert is_stale(None) is False
        assert is_stale(None, days=99999) is False

    def test_handles_naive_datetime_as_utc(self):
        """SQLAlchemy DB 返回的 naive datetime 不应导致比较异常."""
        now = self._now()
        naive_old = datetime(2026, 5, 1, 0, 0)  # 18 days ago, naive
        assert is_stale(naive_old, now=now) is True

    def test_default_now_used_when_not_provided(self):
        """不传 now 时用 datetime.now(utc) — 烟雾测试不抛异常."""
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert is_stale(old) is True

    def test_stale_days_constant(self):
        assert STALE_DAYS == 7
