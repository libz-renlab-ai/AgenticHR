# 修复计划：intake 重复发问 + Step2 网络韧性

> 2026-05-14 — 根因调查见本次会话。两个用户报告 bug：陈成功重复发问、李振华简历未进系统。

## 根因摘要（已用 DB + 审计表证实）

### Bug 1 — 陈成功重复发问（DB id=334）
链条：
1. 第一次发问时 `intake_typeAndSendChatMessage` 返回 `ok:false`（慢网，5s 确认窗口不够）→ 扩展跳过 `ack-sent` → 后端 `record_asked` 没跑 → `asked_at=None, ask_count=0`。
2. 候选人回复，但 glm-4-flash 没抽到槽位 → 槽位仍空。
3. `decide_next_action`：`asked_at=None` → `_cooled()` 返回 `True` → 决策 `send_hard` → **重复发问**。
4. 防循环护栏（BUG-B1）被绕过：它判定条件 `already_asked_some` 看 `ask_count>0`，而第 1 步使 `ask_count=0`。

**架构性根因**：后端"是否已发问"的认知**完全依赖扩展回调 `ack-sent`**。这是脆弱的分布式状态——已是第 3 次复发（98068f8、e18a54b 修过同类）。

### Bug 2 — 李振华简历未进系统（DB id=332）
`collect-chat` 从未为他调用过（0 槽位行、无 snapshot）。Step2 处理单个候选人的流程在到达 collect-chat 前的某网络步骤失败，而 `step2_enrichCandidates` **每个失败分支都是裸 `continue`、零重试**。系统性：`collecting` 198 人中 173 人 0 槽位行。

## 修复方案

### FIX 1（后端，架构性）— 聊天记录作为"是否已发问"的真相源

**文件**：`app/modules/im_intake/service.py` `analyze_chat`

`pack_hard` 生成的硬槽位问题必含marker `"想跟您先确认几个信息"`。利用它：

1. 在 `analyze_chat` 中，`merged_messages` 构建后、`decide_next_action` 前，计算 `hr_asked_in_chat` = 是否存在 `sender_id=="self"` 且内容含该 marker 的消息。
2. **自愈回填**：若 `hr_asked_in_chat` 为真，但所有硬槽位 `asked_at` 均为 `None`（扩展漏调 ack-sent）→ 对未填充的硬槽位回填 `asked_at=now`、`ask_count=max(ask_count,1)`。这样 `decide_next_action` 的 `_cooled()` 不再立即重发（返回 `wait_reply`）。
3. **护栏加固**：BUG-B1 的 `already_asked_some` 改为 `already_asked_some or hr_asked_in_chat`，不再单独依赖 `ask_count`。

效果：陈成功这类（已问+已回复+抽取失败）→ `mark_pending_human`（转人工复核），而非重复发问。

**不在本次范围**：glm-4-flash 抽取质量本身（pre-existing 系统性问题，134 成功/多数失败）——属另一个更大的坑，用户报告的是"重复发问"，转人工已消除该症状。

### FIX 2（扩展）— 部署已有的 5s→20s 确认窗口修复

`edge_extension/content.js` `intake_typeAndSendChatMessage` 的确认窗口已在 worktree 改为 20s（降低 FIX 1 触发器的发生率）。本次随扩展一起部署。

### FIX 3（扩展）— Step2 单候选人网络步骤加重试

**文件**：`edge_extension/content.js` `step2_enrichCandidates` + `downloadPdf`

- 面板同步 `intake_waitFor(..., 12000)` 超时 → 重试 `geek.click()` + 等待，共 3 次尝试再放弃。
- `collect-chat` fetch → 包一层重试（3 次，退避）。
- `downloadPdf`：iframe 等待 10s→延长 + 条件等待；下载/上传 fetch 加重试。

一次 BOSS 卡顿不再 = 候选人永久跳过。

## 测试策略（TDD）

**FIX 1（后端，可测核心）**：新增 `tests/modules/im_intake/test_analyze_chat_resend_selfheal.py`
- 用例 A：候选人硬槽位全空、`asked_at=None`、`ask_count=0`，messages 含 "self" 含 marker 的问题 + 候选人回复 ≥2 条 → `analyze_chat` 返回 `mark_pending_human`（修复前返回 `send_hard`，失败）。
- 用例 B：同上但候选人只回复 1 条 → 返回 `wait_reply`（不是 `send_hard`）。
- 用例 C（回归）：无 "self" 问题消息、首次分析 → 仍返回 `send_hard`（首问不受影响）。

**FIX 2/3（扩展，纯浏览器 JS）**：`edge_extension/` 无 JS 测试框架（仓库唯一 package.json 在 frontend/）。验证 = `node --check` + 数据流走查。诚实声明此限制。

## 验证 & 部署

1. `pytest tests/modules/im_intake/` 全绿（含新测试 + 不破坏现有 30 个测试文件）
2. `node --check edge_extension/*.js`
3. 部署：扩展改动需用户在浏览器 reload 扩展；后端改动需重启 uvicorn（当前 PID 18164）。
