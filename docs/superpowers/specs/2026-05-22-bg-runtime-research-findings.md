# 后台运行问题 — 研究结论

**Date:** 2026-05-22
**Status:** Research complete, design ready
**Author:** liboze + Claude

## TL;DR

用户感觉"切走 tab 后自动化就停了"——**实测证明：不是停了，是被 Chrome 节流到 30-300× 慢，看起来像停了**。

最大根因不是输入失效（execCommand/dispatchEvent 在 hidden tab 里**仍然工作**），而是 **timer 节流**——`setTimeout(30ms)` 实际等了 700-8800ms。

## 实测证据（claude-in-chrome 真实 Chrome，2026-05-22）

测试方法：在 example.com 注入合成 contenteditable + 测试函数。开第二个 tab 让 example.com 进入 hidden 状态。运行 60s。

**关键数据（hidden tab + 另一个 active tab）**：

| 指标 | 文档预期 | 实测 |
|---|---|---|
| `setTimeout(30ms)` 实际 | ≥1000ms | **8837ms 平均**（chained throttling 把单循环搞到 30× 比 1s 更慢）|
| `setTimeout(300ms)` 实际 | ≥1000ms | 2186ms |
| `execCommand("insertText")` | ❌ 应该 do nothing | ✅ **8/8 全成功** |
| `dispatchEvent(InputEvent)` 触发监听器 | ❓ | ✅ **8/8 全成功** |
| `element.focus()` | 文档不保证 | ✅ 改 activeElement（但 hasFocus=false） |
| 60s 期望样本 30 | — | **实际只跑 8 样本**（循环被搞慢） |

**Hidden tab + 单 tab 无其他**：avg30 = 704ms（standard 1s 节流）—— 即只 1 个 tab 时，被钳到 ~1s 而非 ~9s

**节流分层**：
1. Tab 可见：无节流
2. Tab 隐藏（standard）：setTimeout 钳到 ~1s
3. Tab 隐藏 + idle 5min + 链式 timer 5 个以上 + 无 audio + 无 WebRTC（intensive）：钳到 ~1min
4. Tab 隐藏 + 内存压力（frozen）：JS 完全停

**意外发现**：
- w3c/editing#193 说 execCommand 在 invisible context 不工作 —— 但那是 **iframe**，不是 **hidden tab**
- 实测 hidden tab 中 execCommand("insertText") **仍然工作**
- 我之前在 brainstorm 里把这个搞错了，需要更正

## 推翻的旧假设

| 旧假设 | 实测/文档真相 |
|---|---|
| 后台 tab 中 execCommand 失效 | ❌ 不对 —— 仅 invisible iframe 失效，hidden tab 不影响 |
| input.focus() 完全失效 | ❌ 不对 —— activeElement 仍然切换 |
| 必须强制激活 tab 才能发消息 | ❌ 不必 —— dispatchEvent 路径完全可用 |
| 离开页面就停 = 完全停止 | ❌ 不对 —— 是慢 30-300×，看起来停了 |

## 重新设计：实际的修复路径

### 唯一真问题：Timer 节流

实测的 BOSS Step1/Step2 影响估算：

| 场景 | 前台 | 后台 (standard 1s) | 后台 (intensive 60s) |
|---|---|---|---|
| Step1 1000 候选人注册 (`sleep(30)` × 1000) | 30s | 17 min | **16 hours** ⚠️ |
| Step2 单消息发送 (50 字 × 50ms) | 2.5s | ~50s | ~50 min |
| Step2 100 候选人轮询 | 7 min | 1.5 hr | 100+ hr |

**Step1 进入 intensive 节流时 16 小时 = 用户看起来彻底停了**

### 修复方案：分层

#### Layer 1（必做）：Audio 保活
- 启动一个**听不见的音频流**（频率 40kHz 或音量 0.001 但非零）
- 让 Chrome 把 tab 当作"播放媒体" → **豁免 intensive 节流**
- 效果：把后台从 1-60s 钳制 → 1s 钳制
- Step1 1000 人：**16h → 17min** ✅
- Step2 消息：50s 仍可接受
- 代价：tab title 旁出现 🔊 图标（用户预期可控）
- 限制：AudioContext 需要 user gesture 启动 → 通过 popup 按钮触发

#### Layer 2（可选 v2）：Worker 心跳
- Web Worker 的 setTimeout **不受 throttling 影响**
- 主线程注册"等 X ms"时实际由 Worker postMessage 唤醒
- 完全消除 timer 节流
- 复杂度高，工作量大，留 v2

#### Layer 3（保留现状）：现有 pause/stop 不变
- 用户点击 BOSS 内任意位置仍然暂停（防自动化扰民）
- 切 tab 不触发 stop（已确认 grep 无 visibilitychange）

#### Non-Goals：不做
- 不做"强制激活 tab" 方案（B 方案）—— 实测发现根本不需要，因为输入机制都工作
- 不迁移到桌面 playwright —— 杀鸡用牛刀
- 不动 execCommand 路径 —— 它在 hidden tab 实际工作

## 实施计划

详见配套 plan：`docs/superpowers/plans/2026-05-22-bg-keep-alive.md`

简版：

1. content.js 增加 `intake_startBgKeepAlive()` / `intake_stopBgKeepAlive()` 函数
2. popup.html 加 toggle "🔇 后台保活" + 状态展示
3. popup.js 监听 toggle，发消息触发 content.js
4. 用户第一次点击 toggle → AudioContext.resume() 用 user gesture 启动
5. 状态持久化到 chrome.storage.local
6. 重新打开 BOSS tab 时自动恢复（如果用户上次开了）

## 风险评估

| 风险 | 概率 | 缓解 |
|---|---|---|
| 长时间 audio 播放 + 自动化 → BOSS 风控 | 低 | Audio 是合法 web 行为；不与自动化时序耦合 |
| 用户看到 🔊 图标困惑 | 中 | popup 文案明确说明；toggle 可关 |
| AudioContext.resume() 因为浏览器更新失效 | 低 | 标准 API；降级处理（toast 提示用户保持前台） |
| 静音流被用户耳机/Bluetooth 误识别 | 低 | 40kHz 超声波 + 0.001 增益，绝大多数设备听不到 |
| 仍残留 standard 1s 节流让人觉得慢 | 中 | UI 加进度 toast（已 Step1 加过）；用户预期可控 |

## 接下来要做的事

立即执行 implementation plan：
1. 写实施 plan（拆步骤）
2. 改 content.js + popup
3. 提交到 `worktree-extension-bg-runtime` 分支
4. 等用户实际跑诊断验证 + 实际试用 audio 保活
5. 根据反馈决定是否做 v2 (Worker 心跳)
