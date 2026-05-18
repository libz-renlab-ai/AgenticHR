"""F4 intake 候选人陈旧度判定 — 纯函数, 无 DB 依赖。

规则 (2026-05-18 HR 反馈):
- 候选人最后一次在 Boss 聊天的时间 > 7 天 → 视为陈旧
- 陈旧候选人: 已入库则自动归档为 timed_out, 未入库则拒绝创建
- 反归档时重置 intake_started_at, 作为 fallback 时间锚 → 给 7 天宽限期
"""
from datetime import datetime, timezone, timedelta

STALE_DAYS = 7


def _normalize_dt(dt: datetime | None) -> datetime | None:
    """Naive datetime 视为 UTC, 避免后续 max / 比较抛 TypeError."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def last_message_dt(
    chat_snapshot: dict | None,
    fallback: datetime | None = None,
) -> datetime | None:
    """从 chat_snapshot 找最新有效 sent_at, 再与 ``fallback`` 取较新者.

    chat_snapshot 结构: ``{"messages": [{"sender_id", "content", "sent_at"}]}``
    反向遍历找最近一条有合法 ISO-8601 ``sent_at`` 的消息; 无 sent_at /
    parse 失败的逐条跳过。

    返回 ``max(chat_last_ts, fallback)`` —— ``fallback`` 不只是 "全空时
    才用", 而是 "时间下界". 这样:

    * 新建候选人 chat_snapshot 为空 → 用 fallback (= intake_started_at) 作为
      时间锚, 给 7 天缓冲;
    * 反归档候选人 chat_snapshot 仍有老消息但 ``intake_started_at`` 已被
      重置为 now → max() 取 now → 给 7 天宽限期, 不会立即再次归档;
    * 正常活跃候选人 chat 里有新消息 → max() 取最新消息时间, 业务行为不变。

    任何一侧为 None 时取另一侧; 都为 None 返回 None。
    """
    chat_dt: datetime | None = None
    msgs = (chat_snapshot or {}).get("messages") or []
    for m in reversed(msgs):
        ts = (m or {}).get("sent_at")
        if not ts:
            continue
        try:
            chat_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            break
        except (ValueError, TypeError):
            continue

    chat_dt = _normalize_dt(chat_dt)
    fb = _normalize_dt(fallback)
    if chat_dt is None:
        return fb
    if fb is None:
        return chat_dt
    return max(chat_dt, fb)


def is_stale(
    last_dt: datetime | None,
    now: datetime | None = None,
    days: int = STALE_DAYS,
) -> bool:
    """判定 ``last_dt`` 是否陈旧 (``< now - days``).

    ``last_dt is None`` 不视为陈旧 (信息不足时倾向放过, 而非误杀)。
    naive datetime 视为 UTC, 避免 timezone 比较抛 TypeError。
    """
    if last_dt is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last_dt) > timedelta(days=days)
