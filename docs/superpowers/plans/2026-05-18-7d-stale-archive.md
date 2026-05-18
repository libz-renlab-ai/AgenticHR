# 7 天无反应自动归档 — 实施计划

**Date**: 2026-05-18
**Spec**: `docs/superpowers/specs/2026-05-18-7d-stale-archive-design.md`
**Worktree**: `worktree-stale-archive-7d`

## 执行顺序 (TDD)

### Phase 1: 纯函数 + 测试

**T1.1**: 新建 `app/modules/im_intake/staleness.py`
- `STALE_DAYS = 7` 常量
- `last_message_dt(snapshot, fallback) -> datetime | None`
- `is_stale(last_dt, now=None, days=STALE_DAYS) -> bool`

**T1.2**: 新建 `tests/modules/im_intake/test_staleness.py`
- `test_last_message_dt_returns_last_valid_sent_at`
- `test_last_message_dt_falls_back_when_all_missing`
- `test_last_message_dt_parses_Z_suffix`
- `test_last_message_dt_skips_invalid_then_uses_prior`
- `test_is_stale_with_recent_dt_false`
- `test_is_stale_with_old_dt_true`
- `test_is_stale_with_none_returns_false`
- `test_is_stale_handles_naive_datetime_as_utc`

跑 → 全过 → 提交

### Phase 2: 已入库归档 (analyze_chat)

**T2.1**: 改 `app/modules/im_intake/service.py` `analyze_chat`:
- import `staleness.last_message_dt / is_stale / STALE_DAYS`
- 在终态守卫之后、其它分支之前加 stale 判定
- 设 `intake_status=timed_out`, `reject_reason=auto_archive_7d_no_reply`
- 返回 `NextAction(type="archived_stale", reason="...")`

**T2.2**: 改 `app/modules/im_intake/schemas.py` (如需):
- `NextAction.type` 现有 Literal 加 `"archived_stale"` 和 `"skipped_stale_new"`

**T2.3**: 新建 `tests/modules/im_intake/test_archive_stale.py`
- `test_analyze_chat_recent_message_normal_flow`
- `test_analyze_chat_stale_message_archives`
- `test_analyze_chat_already_terminal_skipped_by_guard`
- `test_analyze_chat_no_chat_snapshot_uses_started_at_fallback`
- `test_analyze_chat_writes_audit_event_on_archive`

跑 → 全过 → 提交

### Phase 3: 入库前拦截 (collect-chat 入口)

**T3.1**: 改 `app/modules/im_intake/router.py` `collect_chat`:
- 在 `_build_service / ensure_candidate` 之前查 `existing` candidate
- 如 None 且 messages 是 stale → 不创建, 返回特殊 `next_action`
- 已有 candidate → 走原 flow, 后端 analyze_chat 内会归档

**T3.2**: 新建测试到 `test_archive_stale.py`:
- `test_collect_chat_new_with_stale_messages_does_not_create`
- `test_collect_chat_new_with_fresh_messages_creates_normally`
- `test_collect_chat_existing_candidate_unaffected_by_pre_ingest_check`

跑 → 全过 → 提交

### Phase 4: 反归档端点

**T4.1**: 改 `app/modules/im_intake/router.py`:
- 新增 `POST /candidates/{id}/unarchive`
- 仅 `timed_out` → `awaiting_reply`
- 重置 `intake_started_at = now`, 清 `intake_completed_at` 和 `reject_reason`
- 写 audit `f4_unarchived`

**T4.2**: 新建测试到 `test_archive_stale.py`:
- `test_unarchive_timed_out_to_awaiting_reply`
- `test_unarchive_resets_intake_started_at`
- `test_unarchive_rejects_non_timed_out_status`
- `test_unarchive_404_for_other_users_candidate`
- `test_unarchive_then_analyze_chat_no_immediate_re_archive` (闭环)

跑 → 全过 → 提交

### Phase 5: 全量回归 + 推送

**T5.1**: 跑 `tests/modules/im_intake/ tests/modules/recruit_bot/ tests/integration/`
- 期望: 全过
- 如有失败 → 修复后再跑

**T5.2**: 跑 typecheck (如有) / lint (如有)

**T5.3**: 合并 worktree 分支到 main → push renlab/main
- 因为这次开了独立 worktree, merge 回 main 用 `git merge --no-ff`
- 远程 push

## 不在本计划范围

- 前端 popup 加"已归档列表" 与 "恢复"按钮 (后续单独 plan)
- 现有数据库历史 awaiting_reply 候选人的批量回填扫描 (即让现有"早就该归档"
  的存量数据立即归档): 不主动做, 让下次 Step2 自然推进, 慢慢稀释。

## 验收标准

1. `tests/modules/im_intake/test_staleness.py`: 全过 (8 个)
2. `tests/modules/im_intake/test_archive_stale.py`: 全过 (13 个)
3. `tests/modules/im_intake/` 整体回归: 0 个新 fail
4. `tests/modules/recruit_bot/` 整体回归: 0 个新 fail
5. `tests/integration/` 整体回归: 0 个新 fail
6. `git push renlab main`: 推送成功
7. spec + plan 文档已提交进 git history
