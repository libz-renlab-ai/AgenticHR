# 7 天无反应自动归档 + 老候选人不再入库 — 设计文档

**Date**: 2026-05-18
**Status**: Draft
**Spec ID**: 2026-05-18-7d-stale-archive

## 背景

F4 自动采集流程 (im_intake) 长期跑下来出现两个治理问题:

1. **僵尸候选人堆积**: Step2 每 3 小时分析所有未终态候选人 (`intake_status` ∈
   `collecting / awaiting_reply`)。候选人长期不回复但又不满足现有"问询 3 次用完
   → timed_out"或"3 次扫描不在列表 → abandoned"条件时, 会一直在活跃池里
   被反复尝试, 占 Boss API 配额 + 占 HR 简历库视觉空间。

2. **过期候选人误入库**: 批量采集新候选人时 (`batchCollectNewFromList` / Step2
   首次 collect-chat 命中新 boss_id), 即使候选人最后一次活跃是几个月前, 也会
   入库走完整采集流程, 浪费 LLM tokens。

HR 真实诉求 (2026-05-18 反馈): 超过 **7 天** 双方无聊天的候选人, 不再花精力
跟进 — 既不入库新人, 也归档已入库的旧人, 必要时支持手动反归档。

## 目标

* 候选人最后一次与 HR 在 Boss 聊天的时间 > 7 天 → 已入库则自动归档,
  未入库则拒绝创建
* 归档复用现有 `intake_status = "timed_out"`, 不引入新状态值
* 反归档操作给 HR 一个新的 7 天宽限期, 避免反归档后立即被再次归档
* 不增加新的 cron / scheduler, 复用现有 Step2 3 小时巡检节奏

## 非目标

* 不区分"问询 3 次用完"与"7 天无反应"两种 `timed_out` 子原因的统计 (用
  `reject_reason` 字段做语义区分供未来 audit, 但不暴露给 UI)
* 不在前端 popup 加"已归档候选人列表"页 — 后续由前端简历库改造承担
* 不修改 IM 通道以外的 candidate 源 (例如简历详情页/PDF 直接采集场景不
  受影响)
* 不增加新 DB 表 / 列 — 完全复用现有 schema

## 关键决策 (brainstorm 已锁定)

| 决策点 | 选择 |
|---|---|
| "一周" 时间起点 | 候选人最后一次跟 HR 在 Boss 聊天的时间 (`chat_snapshot.messages[-1].sent_at`) |
| 归档状态字段 | 复用 `intake_status = "timed_out"`,`reject_reason` 区分原因 |
| 后台扫描触发 | 搭车 Step2 (`step2_enrichCandidates` 每 3 小时一次) |
| 时间源 | `chat_snapshot.messages` 里最后一条的 `sent_at` |
| 反归档宽限 | 重置 `intake_started_at = now()`, 作为时间锚的兜底下界 |

## 架构

### 时间判定函数

```python
# app/modules/im_intake/staleness.py (新文件)

from datetime import datetime, timezone, timedelta
from typing import Optional

STALE_DAYS = 7

def last_message_dt(
    chat_snapshot: dict | None,
    fallback: datetime | None = None,
) -> datetime | None:
    """从 chat_snapshot 找最新 sent_at, 再与 fallback 取较新者。

    返回 max(chat_last_ts, fallback) — fallback 是"时间下界"而非"全空时
    才用". 这样反归档时把 intake_started_at 重置为 now → 即使 chat 里
    仍有 10 天前老消息, max() 也会取 now → 给 7 天宽限期, 否则反归档
    后立即会被再次归档 (设计陷阱, 实现时被测试发现并修正)。

    任一侧为 None 时返回另一侧; 都为 None 返回 None。
    """
    chat_dt = None
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
    # naive datetime 视为 UTC, 都规范化后 max
    if chat_dt is None:
        return fallback
    if fallback is None:
        return chat_dt
    return max(chat_dt, fallback)


def is_stale(
    last_dt: datetime | None,
    now: datetime | None = None,
    days: int = STALE_DAYS,
) -> bool:
    """last_dt < now - days 视为陈旧。last_dt 为 None 时不视为陈旧
    (新建候选人首次入库 chat_snapshot 可能为空)。"""
    if last_dt is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (now - last_dt) > timedelta(days=days)
```

### 归档判定流程 (analyze_chat 入口)

```python
# app/modules/im_intake/service.py 内, analyze_chat 起首

async def analyze_chat(self, candidate, messages, job):
    now = datetime.now(timezone.utc)

    # ── 1. 终态守卫 (已有) ─────────────────────────────────────
    if candidate.intake_status in _TERMINAL_STATUSES:
        return self._build_terminal_action(candidate)

    # ── 2. 7d 陈旧度判定 (新增) ────────────────────────────────
    # 候选人有 candidate.id 表示已入库; chat_snapshot 来自 DB 或当前请求 messages
    snapshot = {"messages": messages or []}
    last_dt = last_message_dt(snapshot, fallback=candidate.intake_started_at)
    if is_stale(last_dt, now=now):
        candidate.intake_status = "timed_out"
        candidate.intake_completed_at = now
        candidate.reject_reason = f"auto_archive_{STALE_DAYS}d_no_reply"
        self.db.commit()
        log_event(
            f_stage="f4_auto_archive",
            action="stale_no_reply",
            entity_type="intake_candidate",
            entity_id=candidate.id,
            input_payload={
                "last_message_dt": last_dt.isoformat() if last_dt else None,
                "stale_days_threshold": STALE_DAYS,
            },
            reviewer_id=self.user_id,
        )
        return NextAction(
            type="archived_stale",
            reason=f"超过 {STALE_DAYS} 天无新消息, 自动归档",
        )

    # ── 3. 进入既有 collecting / awaiting_reply / pending_human 流程 ─
    ...
```

`NextAction.type = "archived_stale"` 是新增的动作类型。前端 content.js
现有 if/else 链 (`send_hard / send_soft / request_pdf / complete /
timed_out`) 不识别该类型 → 直接走 fall-through, 不发消息不点按钮,
下一轮循环 → 主循环 `await closeDialog()` (前面已修)
→ `geek.click()` 切下一候选人。**前端零改动**。

### 入库前拦截 (新候选人场景)

`collect-chat` 入口里, `ensure_candidate` 之前判定 messages 时间。注意:
- 该路径仅在 boss_id 是首次出现时触发 create
- 已有 candidate 走 update 分支, 在 analyze_chat 内归档(上一节)

```python
# router.py collect-chat
existing = db.query(IntakeCandidate).filter_by(
    user_id=user_id, boss_id=body.boss_id
).first()

if existing is None:
    # 新候选人: 入库前先看时间, 老的不收
    msgs_dicts = [m.model_dump() for m in body.messages]
    last_dt = last_message_dt({"messages": msgs_dicts}, fallback=None)
    if is_stale(last_dt):
        # 不创建 candidate, 返回特殊 action 让前端跳过
        log_event(
            f_stage="f4_pre_ingest_reject",
            action="stale_skip_create",
            entity_type="boss_id",
            entity_id=None,
            input_payload={
                "boss_id": body.boss_id,
                "last_message_dt": last_dt.isoformat() if last_dt else None,
            },
            reviewer_id=user_id,
        )
        return CollectChatOut(
            candidate_id=None,
            next_action=NextAction(
                type="skipped_stale_new",
                reason=f"候选人最后聊天超过 {STALE_DAYS} 天, 跳过入库",
            ),
        )
```

注: `CollectChatOut.candidate_id` 字段当前类型可能是 `int` 必填; 实施时
若需要改成 `int | None`, 同步修 schema。

### 反归档端点

```python
# router.py 新增

@router.post("/candidates/{candidate_id}/unarchive")
def unarchive_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """手动反归档: 把 timed_out 候选人放回 awaiting_reply, 重置时间锚给
    7 天宽限期。仅对 timed_out 状态生效, 其它终态 (complete/abandoned/
    pending_human) 不动 — 那些有各自的恢复路径或不该被简单恢复。"""
    c = db.query(IntakeCandidate).filter_by(
        id=candidate_id, user_id=user_id,
    ).first()
    if not c:
        raise HTTPException(404, "candidate not found")
    if c.intake_status != "timed_out":
        raise HTTPException(
            400,
            f"only timed_out can be unarchived, current status: {c.intake_status}",
        )

    now = datetime.now(timezone.utc)
    old_reject = c.reject_reason
    c.intake_status = "awaiting_reply"
    c.intake_started_at = now  # 关键: 重置时间锚, 给 7 天宽限
    c.intake_completed_at = None
    c.reject_reason = ""
    c.last_checked_at = now
    db.commit()
    _audit_safe(
        "f4_unarchived",
        "manual_unarchive",
        c.id,
        {"from_reject_reason": old_reject},
        reviewer_id=user_id,
    )
    return {
        "ok": True,
        "status": c.intake_status,
        "intake_started_at": c.intake_started_at.isoformat(),
    }
```

## 数据流

```
Step2 (3 小时 / 手动触发)
  ↓ 拉 /api/intake/autoscan/rank → 取 collecting / awaiting_reply
  ↓ 对每个候选人:
  ↓   geek.click → 等同步 → parseChatFromDOM
  ↓   POST /api/intake/collect-chat (body.messages 含最新 sent_at)
  ↓     ↓ 后端:
  ↓     ↓   if not existing:
  ↓     ↓     last_dt = last_message_dt(body.messages, fallback=None)
  ↓     ↓     if is_stale(last_dt): return next_action=skipped_stale_new   ← 入库前拦
  ↓     ↓   else:
  ↓     ↓     ensure_candidate (update 或 noop)
  ↓     ↓     analyze_chat:
  ↓     ↓       last_dt = last_message_dt(snapshot, fallback=intake_started_at)
  ↓     ↓       if is_stale(last_dt):
  ↓     ↓         intake_status = timed_out
  ↓     ↓         reject_reason = auto_archive_7d_no_reply
  ↓     ↓         return next_action=archived_stale                         ← 已入库归档
  ↓ 前端: 收到 archived_stale / skipped_stale_new → 默认跳过, 不发消息
  ↓ closeDialog (已有, 保证下一人开始前简历预览关上)
  ↓ next candidate

HR 在前端简历库 (未来) 点"恢复" 按钮:
  → POST /api/intake/candidates/{id}/unarchive
  → intake_status = awaiting_reply
  → intake_started_at = now (7d 宽限锚)
```

## 状态机变更

```
                  ┌──────────────────────────┐
                  │     collecting           │←─┐
                  │     awaiting_reply       │  │
                  └──────────────────────────┘  │ manual /
                       │      │                  │ unarchive
                       │      │                  │
              7d 无消息│      │ 3 次问询用完     │
        (auto_archive)│      │                  │
                       ↓      ↓                  │
                  ┌──────────────────────────┐   │
                  │       timed_out          │───┘
                  │ reject_reason:           │
                  │   auto_archive_7d_no_reply (新)
                  │   timed_out_max_questions (旧, 不动)
                  └──────────────────────────┘
```

## 错误处理

1. **chat_snapshot.messages[-1].sent_at 缺失或格式异常**: `last_message_dt`
   逐条往前找, 全部失败 → 用 fallback。fallback 是 candidate.intake_started_at
   (已入库) 或 None (新候选人入库前)。
   - 入库前 + 所有消息时间无效 → `is_stale(None) == False` → 允许创建。
     合理 (信息不足, 倾向放过而非误杀)。

2. **时区**: candidate.intake_started_at 在 DB 里是 naive datetime (SQLAlchemy
   行为)。`is_stale` 把 naive 视为 UTC 处理。所有比较都在 UTC 域。

3. **并发**: 两个 Step2 实例同时跑同一候选人 — 已有 phase_running 互斥锁
   阻止此场景。Worst case: 第二个 Step2 在第一个把候选人改成 timed_out 之后
   读到, 终态守卫 (`if candidate.intake_status in _TERMINAL_STATUSES`)
   会直接返回, 不重复写。

4. **反归档后立即又 stale**: HR 反归档时 intake_started_at = now, fallback
   为 now → is_stale 必为 False, 7 天内一定不会被再次自动归档。除非 HR
   反归档后**主动给候选人发了消息**, 那 chat_snapshot 里会出现新 sent_at,
   时间会被 fallback 之后的真实消息 timestamp 覆盖 — 但既然有新消息,
   也不应该被归档。✅

## 测试

| 测试用例 | 期望 |
|---|---|
| `test_no_messages_uses_fallback` | chat_snapshot 空, fallback=now → 不归档 |
| `test_recent_message_not_archived` | 最后消息 1 天前 → 不归档 |
| `test_stale_message_archived` | 最后消息 8 天前 → intake_status=timed_out, reject_reason=auto_archive_7d_no_reply |
| `test_collect_chat_new_candidate_with_stale_messages_not_created` | 新 boss_id POST collect-chat, 消息都 8 天前 → DB 无该候选人, 返回 skipped_stale_new |
| `test_collect_chat_new_candidate_with_fresh_messages_created` | 新 boss_id, 最后消息 1 天前 → 正常创建 |
| `test_unarchive_resets_started_at_and_status` | timed_out → POST unarchive → status=awaiting_reply, intake_started_at≈now |
| `test_unarchive_then_analyze_chat_no_re_archive` | 反归档后立即 analyze_chat (chat_snapshot 不变, 全是老消息) → fallback=now → 不再归档 |
| `test_unarchive_only_works_on_timed_out` | complete / abandoned / awaiting_reply → POST unarchive → 400 |
| `test_terminal_complete_skipped_by_archive_check` | complete 候选人 chat 已 30 天前 → 归档逻辑 skip (终态守卫先 return) |
| `test_isostring_with_Z_suffix_parses` | sent_at = "2026-05-10T08:00:00Z" → 解析为 UTC datetime |

## 风险

| 风险 | 缓解 |
|---|---|
| `chat_snapshot` 历史脏数据无 sent_at | fallback 用 intake_started_at, 旧数据 intake_started_at 通常有值; 即便没有 → is_stale(None)=False 保守放过 |
| `timed_out` 语义混用 (问询次数用完 vs 7天无反应) | `reject_reason` 字段区分, 写 audit log; 未来需要数据分析时通过 reject_reason 反查 |
| 前端 popup 没"已归档列表" | 暂时通过 PATCH status 或新 unarchive 端点 + 简历库 UI 解决; 不阻塞本次后端落地 |
| 反归档后 HR 立即又看到候选人不回复 | intake_started_at 重置给 7 天宽限期, 是产品决策非 bug |
| 误归档热门候选人 (HR 自己 7 天没看 Boss) | 反归档零摩擦, HR 一键恢复 + 7 天宽限。可接受。 |

## 部署

无 DB migration, 无前端必改 (前端 fall-through 处理未知 action.type
等于不操作 + 进下一个)。后端代码改动重启 FastAPI 即生效。
