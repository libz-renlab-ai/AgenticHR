# F-interview-eval 真实接入补完 — 设计文档

**Status**: Approved
**Date**: 2026-05-14
**Phase**: F-interview-eval 收尾（补完两处真实 IO 占位）
**Parent**: [docs/superpowers/specs/2026-05-07-ai-interview-eval-design.md](./2026-05-07-ai-interview-eval-design.md)

---

## 1. 背景

F-interview-eval（AI 面试评估）模块的代码骨架、数据库、router、worker、前端、18 个测试文件均已完成并经 IE-002~IE-025 共 25 轮修复。但有**两处真实外部 IO 是占位骨架,真实环境跑不通**:

1. **腾讯会议录像下载** (`tencent_meeting_recording.py`) — DOM selector 是抽象骨架（`[data-record-item]` / `data-mp4` 等占位符），且下载用裸 `requests.get()` 不带鉴权 cookie,真实云录制页跑不通。
2. **腾讯云 ASR 大文件** (`tencent_asr.py`) — 走 `SourceType=1`（base64 内联），硬上限 5MB。真实面试录像（几十分钟 mp4，几百 MB）远超,代码直接抛 RuntimeError，标注 "需先上传到 COS 改 SourceType=0（待生产灰度后实施）"。

本 spec 补完这两处,并以**一次真实端到端验收**（真实录像 → 下载 → ASR → LLM 打分 → 前端 scorecard）作为完工标准。

## 2. 决策摘要

全部在 2026-05-14 brainstorm 与用户拍板:

| # | 决策项 | 选择 | 理由 |
|---|---|---|---|
| R1 | 浏览器登录态 | 复用 AgenticHR 的 Playwright 持久化 profile（`data/meeting_browser_{label}/`） | 用户已在该 profile 扫码登录腾讯会议;与既有 `tencent_meeting_web.py` 一致 |
| R2 | ASR 大文件路径 | **不用 COS** —— ffmpeg 抽音频 + 动态码率压缩到 ≤4.5MB + base64 `SourceType=1` | 用户不接受 COS 依赖;opus/mp3 低码率单声道 5MB 可装 ~20-30 分钟,覆盖多数真实面试 |
| R3 | ffmpeg 来源 | `imageio-ffmpeg` pip 包（自带静态二进制） | 无需系统装 ffmpeg;跨平台;PyInstaller 打包有现成 hook |
| R4 | 音频格式 | mp3,单声道,16kHz,码率按时长动态算 | mp3 是腾讯云 ASR 最稳兼容格式;动态码率保证 ≤4.5MB |
| R5 | 超长录像 | 16kbps 仍 >4.5MB → 明确 RuntimeError 拒绝,提示需 COS | 不做分片（时间戳拼接 + 跨片说话人分离破裂,复杂度不值）;documented 限制 |
| R6 | mp4 下载机制 | **A2**：Playwright 已登录上下文内点「下载」按钮,`page.expect_download()` 接住落盘 | 录像下载 URL 几乎必然鉴权/临时签名,裸 requests 拉不到;骑鉴权会话最稳 |
| R7 | DOM selector | 实地抓真实录制页 DOM 后填实（实现第一步） | 占位符无法盲填;`tencent_meeting_web.py` 的 cancel_meeting 已有"实地 DOM 检查"先例 |
| R8 | 抽音频位置 | 藏在 `tencent_asr.transcribe()` 内部,worker 状态机零改动 | 最小爆炸半径;不碰已验证的 worker.py |
| R9 | 改动范围 | 只补两处缺口 + 配套测试,不重构周边 | worker/router/service/models/schemas/前端 已完成且测试覆盖;`core/` 不碰 |
| R10 | 验收标准 | 一次真实录像跑通全链路,贴真实输出为证 | 用户要求"完整的真实的使用作为验收";符合 verification-before-completion |

## 3. 范围

### 3.1 In scope

- 重写 `tencent_meeting_recording.py` 的 `download()`：A2 方案 + 实地抓的真实 selector
- 新增 `audio_extract.py`：mp4 → 压缩 mp3
- 改 `tencent_asr.py` `transcribe()`：内部先抽音频,对音频 base64
- `requirements.txt` 加 `imageio-ffmpeg`
- `config.py` 加 `interview_eval_asr_max_duration_sec`（超长拒绝阈值,可调）
- 配套测试：改 2 个、新增 1 个测试文件;既有 18 个测试文件零回归
- 一次真实端到端验收

### 3.2 Out of scope（明确不做）

| 项 | 理由 |
|---|---|
| COS 集成 | 用户决策不用（R2） |
| ASR 分片支持超长录像 | R5 —— documented 限制,不值复杂度 |
| 自动触发（会议结束自动跑） | 设计文档 D6 已锁半自动（HR 点按钮）;不在本 spec 改 |
| worker / router / service / models / schemas 重构 | 已完成且测试覆盖,R9 |
| 前端改动 | AI 面评 Tab 已完成;本 spec 不碰 |
| `core/` 任何改动 | CLAUDE.md 架构边界 |

## 4. 详细设计

### 4.1 Gap 1 — 录像下载（`tencent_meeting_recording.py`）

重写 `download(interview, dest_path) -> tuple[str, int, int]`：

```
download(interview, dest_path):
  1. 用 browser_data_dir_for(interview.meeting_account) 启 Playwright 持久化 context
     （headless=False，复用既有 _cleanup_stale_chrome 清僵尸进程/锁文件）
  2. goto 录制列表页;若 URL 落到 /login → RuntimeError("账号 '{label}' 登录态过期，
     请到 meeting.tencent.com 重新扫码登录")
  3. 在列表里按 interview.meeting_id 匹配录制行
     —— 匹配策略 + 行/按钮 selector 实地抓 DOM 后填实（R7）
     —— 匹配优先级：meeting_id 精确匹配 > 会议主题+日期回退
     —— 命中 0 行 → RuntimeError("录像未生成或已被清理（meeting_id=...），
        请几分钟后重试，或检查云录制 1GB 配额")
     —— 录像状态为"生成中" → RuntimeError("录像尚未生成完成，请几分钟后重试")
  4. A2 下载：with page.expect_download() as dl: 点行内「下载」按钮
     dl.value.save_as(dest_path)
  5. 探测 size = os.path.getsize(dest_path);duration_sec 从列表行抓（抓不到填 0）
  6. 保留 MAX_DOWNLOAD_BYTES = 2GB 守卫：下载后 size 超限 → 删文件 + RuntimeError
  7. 返回 (dest_path, size, duration_sec)
  finally: ctx.close()
```

**与现状的差异**：
- 删除 `_stream_download`（裸 requests）—— 改 `page.expect_download()`
- `page.evaluate()` 抓列表的 JS 从占位符改为实地 selector
- 其余（context 启动、登录检测、2GB 守卫）语义不变

### 4.2 Gap 2 — 音频抽取（新增 `app/modules/interview_eval/audio_extract.py`）

```python
def extract_audio(
    mp4_path: str,
    max_bytes: int = 4_500_000,
    max_duration_sec: int | None = None,   # None → 读 settings.interview_eval_asr_max_duration_sec
) -> str:
    """mp4 → 压缩 mp3，保证 ≤max_bytes。返回临时 mp3 路径，调用方负责删除。

    Raises:
        RuntimeError: 录像时长超过无-COS 模式上限（duration > max_duration_sec
                      或 16kbps 仍超 max_bytes，二者取严）
        RuntimeError: ffmpeg 执行失败
    """
```

实现要点：
- ffmpeg 二进制：`imageio_ffmpeg.get_ffmpeg_exe()`
- 先 ffprobe（或 ffmpeg -i 解析 stderr）拿 `duration_sec`
- **动态码率**：`bitrate = clamp(max_bytes * 8 / duration_sec * 0.92, floor=16kbps, ceil=64kbps)`
  - `* 0.92` 安全余量（mp3 帧/容器开销）
  - 若 `duration_sec` 长到 16kbps 仍 > max_bytes → RuntimeError(
    "录像约 {N} 分钟，超出无 COS 模式上限 ~{limit} 分钟，需启用 COS")
- ffmpeg 命令：`-i mp4 -vn -ac 1 -ar 16000 -b:a {bitrate} -f mp3 {tmp.mp3}`
- 输出到 `tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)`,返回路径
- ffmpeg 非零退出 → RuntimeError 带 stderr 摘要
- subprocess 显式 `encoding="utf-8"`（Windows GBK 解码挂线程的已知坑）

### 4.3 Gap 2 — ASR 接线（改 `tencent_asr.py`）

`transcribe(mp4_path)` 开头插入抽音频:

```python
def transcribe(mp4_path: str) -> list[dict]:
    # 凭据 fail-fast（现状保留）
    if not settings.tencent_cloud_secret_id or not settings.tencent_cloud_secret_key:
        raise RuntimeError("腾讯云 ASR 凭证未配置...")

    audio_path = extract_audio(mp4_path)        # 新增
    try:
        client = _get_client()
        submit_resp = _submit_task(client, audio_path)   # 改：传 audio_path 而非 mp4_path
        ...                                               # 轮询逻辑不变
    finally:
        try: os.remove(audio_path)              # 新增：清临时音频
        except OSError: pass
```

- `_submit_task` 的 `MAX_BASE64_INPUT_BYTES = 5MB` 校验保留 —— 由 `extract_audio` 的 4.5MB 上限天然通过,校验变成防御性兜底
- `EngineModelType="16k_zh_large"` / `SpeakerDiarization=1` / `ResTextFormat=2` 等参数不变
- mp3 是腾讯云 ASR 支持格式,无需额外格式声明

### 4.4 worker.py — 零改动

`worker.py` 的 `_transcribe(recording_path)` → `tencent_asr.transcribe(recording_path)`,抽音频藏在 `transcribe()` 内部。worker 4 步状态机（download/transcribe/score/publish）完全不动（R8）。

### 4.5 config.py — 加一个可调项

```python
# F-interview-eval：无 COS 模式下 ASR 可处理的录像时长上限（秒）。
# extract_audio 在 16kbps 下仍超 4.5MB 时拒绝；默认 ~28 分钟。
interview_eval_asr_max_duration_sec: int = Field(default=1680, ge=60)
```

`extract_audio` 用此值做 duration 上界检查（与码率下限二者取严）。

### 4.6 requirements.txt

新增一行 `imageio-ffmpeg`（纯 pip,自带跨平台静态 ffmpeg 二进制）。

## 5. 数据流

```
HR 点 [分析面试] → POST /api/interview-eval/start → worker.run(job_id):

  ① downloading
     tencent_meeting_recording.download(interview, dest)
       └→ Playwright(已登录 profile) → 录制页 → expect_download → data/recordings/{job_id}.mp4
  ② transcribing
     tencent_asr.transcribe(mp4_path)
       ├→ audio_extract.extract_audio(mp4)  → 临时 mp3 (≤4.5MB)
       ├→ base64(mp3) → 腾讯云 ASR CreateRecTask → 轮询 DescribeTaskStatus
       ├→ 说话人启发式映射 → [{start_ms,end_ms,speaker,text}]
       └→ finally: 删临时 mp3
     → data/transcripts/{job_id}.json
  ③ scoring
     LLM 按 job.competency_model.assessment_dimensions 打分 → interview_eval_scorecards 行
  ④ done
     飞书推送 + 7 类 audit_events（ieval_start/download_recording/asr_call/llm_call/publish/...）
```

## 6. 错误处理

| 步骤 | 失败场景 | 处理 |
|---|---|---|
| download | profile 登录态过期 | RuntimeError "账号 '{label}' 登录态过期，请重新扫码登录" |
| download | meeting_id 匹配不到录制行 | RuntimeError "录像未生成或已被清理..." |
| download | 录像状态"生成中" | RuntimeError "录像尚未生成完成，请几分钟后重试" |
| download | 下载文件 >2GB | 删文件 + RuntimeError "录像超过单文件上限 2GB" |
| extract_audio | 录像时长超无-COS 上限 | RuntimeError "录像约 N 分钟，超出上限 ~28 分钟，需启用 COS" |
| extract_audio | ffmpeg 非零退出 | RuntimeError "音频抽取失败：{stderr 摘要}" |
| transcribe | 腾讯云凭据未配置/鉴权失败 | RuntimeError（现状保留） |
| transcribe | ASR 配额超限/识别失败/轮询超时 | RuntimeError（现状保留） |

所有 RuntimeError 由 worker 捕获 → `job.status='failed'` + `error_msg` + `audit('failed_at_{step}')`（worker 现有逻辑,不改）。

## 7. 测试策略（TDD：先测后写）

| 文件 | 类型 | 覆盖 |
|---|---|---|
| `test_tencent_meeting_recording.py` | 改 | mock Playwright：登录过期 / meeting_id 匹配不到 / 录像生成中 / expect_download 成功路径 / >2GB 守卫 |
| `test_audio_extract.py` | 新 | mock subprocess(ffmpeg)：正常抽取 / 动态码率计算（短/中/长时长三档）/ 超长拒绝 / ffmpeg 非零退出 |
| `test_tencent_asr.py` | 改 | mock extract_audio + SDK：抽音频→base64→ASR 链路 / 临时文件清理 / 鉴权错 / 配额错 / 轮询超时（后三者现状保留） |
| 既有 18 个测试文件 | 回归 | 全跑 `pytest tests/modules/interview_eval/` —— **零回归** |

完工前必跑 `pnpm test` + `pnpm typecheck`（CLAUDE.md 要求）。

## 8. 真实验收流程（实现完成后执行）

**前置（用户）**：在 `main` 账号录一场 2-5 分钟的腾讯会议云录制（已完成或进行中）。

**执行（AI）**：
1. **实地抓 DOM** —— 打开 `data/meeting_browser_main` profile,导航到录制页,抓真实 DOM 结构,填实 `tencent_meeting_recording.py` 的 selector（R7）
2. **TDD 实现** 两处缺口,`pytest tests/modules/interview_eval/` 零回归 + `pnpm test` + `pnpm typecheck`
3. **ASR 凭据早期冒烟** —— `.env` 的 `TENCENT_CLOUD_SECRET_ID/KEY` 注释写着 "QA 测试录位",先用一个 <5MB 音频单独验真;无效则停下找用户要有效凭据
4. **预置真实数据** —— 在 `data/recruitment.db` 建：1 个 job（`competency_model_status='approved'` + 有 `assessment_dimensions`）+ 1 个 resume + 1 个 interview（真实 `meeting_id` + `meeting_account='main'`）
5. **跑真实全链路** —— `POST /api/interview-eval/start`,轮询 job 走完 `pending→downloading→transcribing→scoring→done`
6. **验证证据（全部贴真实输出）**：
   - `data/recordings/{job_id}.mp4` 真实落盘且可播放（非占位符）
   - `data/transcripts/{job_id}.json` 有真实 ASR 分段 + 说话人标注
   - `interview_eval_scorecards` 有真实 LLM 维度打分 + 录用建议
   - `audit_events` 7 类事件齐全
   - 前端 AI 面评 Tab 渲染 scorecard 正常（浏览器实测截图）
7. **真实花费**（已告知用户）：腾讯云 ASR ~1元/小时（5 分钟录像 ≈ 0.08 元）+ LLM 评分几分钱

## 9. 风险

| 风险 | 缓解 |
|---|---|
| `.env` 腾讯云凭据可能是无效占位 | 验收第 3 步早期冒烟,失败即停 + 找用户,不空跑全链路 |
| 腾讯会议录制页 DOM 未来变动 | selector 实地抓,验收当下准确;错误信息写清楚便于日后排查 |
| 云录制生成延迟 | 会议结束后腾讯云需几分钟生成;download 给"尚未生成完成"明确提示 |
| 录像 >28 分钟（无 COS 上限） | extract_audio 明确拒绝 + 提示需 COS;documented 限制,非 bug |
| imageio-ffmpeg 首次 import 触发二进制下载 | 验收环境提前 `pip install` 预热;失败回退系统 ffmpeg（若有） |

## 10. 改动文件清单

**改**：
- `app/modules/interview_eval/tencent_meeting_recording.py`（重写 `download()`）
- `app/modules/interview_eval/tencent_asr.py`（`transcribe()` 接线抽音频）
- `app/config.py`（加 `interview_eval_asr_max_duration_sec`）
- `requirements.txt`（加 `imageio-ffmpeg`）
- `tests/modules/interview_eval/test_tencent_meeting_recording.py`
- `tests/modules/interview_eval/test_tencent_asr.py`

**新增**：
- `app/modules/interview_eval/audio_extract.py`
- `tests/modules/interview_eval/test_audio_extract.py`

**不碰**：`worker.py` / `router.py` / `service.py` / `models.py` / `schemas.py` / `reconcile.py` / `retention.py` / `feishu_push.py` / `audit.py` / `prompts.py` / 前端 / `core/`

---

**作者**：Claude Opus 4.7 + AgenticHR maintainer
**Brainstorm 决策记录**：2026-05-14 对话（R1-R10）
