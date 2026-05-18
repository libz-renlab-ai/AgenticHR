"""F4 intake 候选人陈旧度判定 — 纯函数, 无 DB 依赖。

规则 (2026-05-18 HR 反馈):
- 候选人最后一次在 Boss 聊天的时间 > 7 天 → 视为陈旧
- 陈旧候选人: 已入库则自动归档为 timed_out, 未入库则拒绝创建
- 反归档时重置 intake_started_at, 作为 fallback 时间锚 → 给 7 天宽限期
"""
from datetime import datetime, timezone, timedelta

STALE_DAYS = 7


def last_message_dt(
    chat_snapshot: dict | None,
    fallback: datetime | None = None,
) -> datetime | None:
    """从 chat_snapshot 取最后一条消息的 sent_at, 失败回退到 fallback.

    chat_snapshot 结构: ``{"messages": [{"sender_id", "content", "sent_at"}]}``
    反向遍历, 找最近一条有合法 ISO-8601 ``sent_at`` 的消息。无 sent_at 或
    parse 失败逐条跳过, 全部失败 → 返回 ``fallback``.

    fallback 通常传 ``candidate.intake_started_at`` — 既给新建候选人完整
    7 天缓冲, 也给反归档候选人完整 7 天宽限。
    """
    msgs = (chat_snapshot or {}).get("messages") or []
    for m in reversed(msgs):
        ts = (m or {}).get("sent_at")
        if not ts:
            continue
        try:
            # 支持 'Z' 后缀的 ISO 8601 (Boss/扩展常传)
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
    return fallback


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
