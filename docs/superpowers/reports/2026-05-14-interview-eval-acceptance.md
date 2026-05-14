# F-interview-eval 真实接入补完 — 验收报告

**日期**: 2026-05-14
**Spec**: [docs/superpowers/specs/2026-05-14-interview-eval-real-integration-design.md](../specs/2026-05-14-interview-eval-real-integration-design.md)
**Plan**: [docs/superpowers/plans/2026-05-14-interview-eval-real-integration.md](../plans/2026-05-14-interview-eval-real-integration.md)

---

## 目标

补完 F-interview-eval（AI 面试评估）的两处真实 IO 占位 —— 腾讯会议录像下载、腾讯云 ASR 大文件 —— 并以**一次真实端到端验收**收尾。

## 架构决策（实地投查后调整）

实地抓腾讯会议录制页 DOM 时发现：**播放页已自带 AI 转写稿**（说话人 + 时间戳 + 文本），可直接 scrape；但经查证 AI 转写是腾讯会议**免费限时体验**，非永久免费。据此用户拍板 **R11：Path B 主 + Path A 兜底**：

- **Path B（主）**：scrape 腾讯会议播放页「逐字稿」（`.minutes-module-paragraph-box`）。免费、快、无 5MB/COS 限制。
- **Path A（兜底）**：Path B 不可用（`TranscriptUnavailable`）时回退到 mp4 下载 → ffmpeg 抽音频 → 腾讯云 ASR。

## 实现（9 个 Task，全部 TDD，11 次提交）

| Task | 内容 | 测试 |
|---|---|---|
| 1 | `config.py` 加 `interview_eval_asr_max_duration_sec` + `imageio-ffmpeg` 依赖 | +2 |
| 2 | `audio_extract.py` —— mp4 抽音频动态码率压缩 | +5 |
| 3 | `tencent_asr.py` 接线 `extract_audio` —— 绕开 base64 5MB 上限 | +1 |
| 4 | 实地抓录制页 DOM —— 两路 selector findings | — |
| 5 | `tencent_meeting_recording.scrape_transcript()` —— Path B | +8 |
| 6 | `tencent_meeting_recording.download()` —— Path A 兜底 | +5 |
| 7 | `worker._acquire_transcript()` —— Path B 主 + Path A 兜底编排 | +2 |
| 8 | 全量回归 | — |
| 9 | 真实端到端验收 + 收尾修复 | +3 |

**新增/修改测试 26 个**。后端全量 `pytest tests/modules/`：**762 passed**（基线 e42214f 为 742，+20 零回归；Task 9 schema 修复后 interview_eval 模块 110 passed）。前端 `pnpm build` 通过。

## 真实端到端验收证据

**输入**：真实腾讯会议云录制「转写_01001的快速会议」（会议号 670 210 027，`main` 账号）。
**执行**：seed 真实 job/resume/interview → `service.create_job()`（router 内部同款代码路径）→ 真 worker 线程。

| 验证项 | 结果 |
|---|---|
| 任务状态机 | `pending → transcribing → scoring → done` ✓ |
| Path B scrape | 真 Playwright 抓到 **9 段**转写稿（说话人 + 时间戳 + 文本）✓ |
| LLM 打分 | 真 `glm-4-flash` 调用，scorecard 落库 ✓ |
| scorecard 内容 | 技术深度 3/10（evidence 0 条）、沟通表达 4/10（evidence 3 条）、`hire_recommendation=hold`，优势/风险/追问点均真实 LLM 中文输出 ✓ |
| audit_events | `ieval_start → asr_call → llm_call → publish` ✓ |
| HTTP API | `GET /api/interview-eval/{66}` `/scorecard` `/transcript` `/by-interview/77001` 四端点均返回正确数据（JWT 鉴权）✓ |
| 前端 UI | AI 面评 Tab 完整渲染：「已完成」状态、`hold` 徽章、总分 3.5/10、维度评分卡、evidence 时间戳跳转 chip、优势/风险/追问点、「AI 草稿」免责声明 ✓ |

证据文件：`artifacts/ie-acceptance/`（acceptance_result.json、record_page.png、player_page.png、saveas_dropdown.png、transcript2.json）。

> 说明：测试录像内容是一段「麦克风测试」（"喂你能听见吗"…），无真实面试内容，故 LLM 给出 3/4 分 + hold 是**正确行为**（garbage in → 合理 out）—— 验收证明的是**管线打通**，非评分质量。

## 验收期暴露并修复的问题

1. **`ScorecardOutput.evidence` schema 过严**：弱模型 glm-4-flash 对内容稀薄的维度会返回空 evidence，原 `min_length=1` 导致整张 scorecard 校验失败、任务 `failed`。修复：放宽到 `min_length=0`（一条维度缺引用不应让整任务失败，score+reasoning 仍有价值）。与既有 IE-026 放宽 max_length 的先例一致。
2. **`_open_player_page` 泄漏 playwright driver 进程**：`sync_playwright().start()` 后未 `.stop()`。修复：加 `_close_player()` 完整清理。

## 已知限制

- **Path A 兜底对「转写记录」类录制不可用**：实地发现这类录制的「原视频文件」下载项 DOM 上 `display:none`（需腾讯会议付费版或该录制无视频文件）。`download()` 会给出明确 `RuntimeError`。Path A 的 mp4-下载happy-path 未能在现有录制上实地验证（mock 测试覆盖）。
- **Path B 依赖腾讯会议免费转写体验期**：体验期过/未开转写 → Path B 抛 `TranscriptUnavailable` → 自动回退 Path A。
- **Path B 成功时无 mp4**：前端录像播放器显示「无录像」（设计取舍，scorecard + 转写稿是核心价值）。
- **超长录像（Path A）**：无 COS 模式下 ASR 上限约 28 分钟（`interview_eval_asr_max_duration_sec`），超出明确拒绝。

## 改动文件

**改**：`app/config.py`、`requirements.txt`、`app/modules/interview_eval/{tencent_asr,tencent_meeting_recording,worker,schemas}.py`
**新增**：`app/modules/interview_eval/audio_extract.py`
**测试**：`tests/modules/interview_eval/{test_config_validation,test_audio_extract,test_tencent_asr,test_tencent_meeting_recording,test_worker,test_schemas}.py`
**未碰**：`router.py` / `service.py` / `models.py` / `reconcile.py` / `retention.py` / `feishu_push.py` / `audit.py` / `prompts.py` / 前端 / `core/`

## 真实花费

- 腾讯云 ASR：本次验收 Path B 命中，未触发 ASR（¥0）
- LLM：1 次 glm-4-flash 调用（约几分钱）
