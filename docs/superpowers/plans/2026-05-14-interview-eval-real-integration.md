# F-interview-eval 真实接入补完 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补完 F-interview-eval 的两处真实 IO 占位（腾讯会议录像下载 + 腾讯云 ASR 大文件），并以一次真实端到端验收收尾。

**Architecture:** 录像下载改 Playwright `expect_download()` 骑已登录会话；ASR 大文件靠新增 `audio_extract.py` 用 imageio-ffmpeg 抽音频 + 动态码率压缩到 ≤4.5MB 后 base64，藏在 `tencent_asr.transcribe()` 内部，worker 状态机零改动。不用 COS。

**Tech Stack:** Python 3.12, Playwright 1.58, tencentcloud-sdk-python, imageio-ffmpeg, pytest, pydantic-settings.

**Spec:** [docs/superpowers/specs/2026-05-14-interview-eval-real-integration-design.md](../specs/2026-05-14-interview-eval-real-integration-design.md)

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/config.py` | 加 `interview_eval_asr_max_duration_sec` 配置项 | 改 |
| `requirements.txt` | 加 `imageio-ffmpeg` 依赖 | 改 |
| `app/modules/interview_eval/audio_extract.py` | mp4 → 压缩 mp3（动态码率，≤4.5MB） | 新建 |
| `app/modules/interview_eval/tencent_asr.py` | `transcribe()` 接线抽音频 + 清理临时文件 | 改 |
| `app/modules/interview_eval/tencent_meeting_recording.py` | 重写 `download()`：A2 方案 + 真实 selector | 改 |
| `tests/modules/interview_eval/test_config_validation.py` | 新配置项校验测试 | 改 |
| `tests/modules/interview_eval/test_audio_extract.py` | audio_extract 单测 | 新建 |
| `tests/modules/interview_eval/test_tencent_asr.py` | 抽音频接线 + 清理测试 | 改 |
| `tests/modules/interview_eval/test_tencent_meeting_recording.py` | A2 下载路径测试 | 改 |

**不碰**：`worker.py` / `router.py` / `service.py` / `models.py` / `schemas.py` / `reconcile.py` / `retention.py` / `feishu_push.py` / `audit.py` / `prompts.py` / 前端 / `core/`。

---

## Task 1: 配置项 + 依赖

**Files:**
- Modify: `app/config.py:64`
- Modify: `requirements.txt`
- Test: `tests/modules/interview_eval/test_config_validation.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/modules/interview_eval/test_config_validation.py` 末尾：

```python


def test_asr_max_duration_rejects_too_small():
    with pytest.raises(ValidationError):
        Settings(interview_eval_asr_max_duration_sec=59)


def test_asr_max_duration_default_and_valid():
    s = Settings()
    assert s.interview_eval_asr_max_duration_sec == 1680
    s2 = Settings(interview_eval_asr_max_duration_sec=600)
    assert s2.interview_eval_asr_max_duration_sec == 600
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/modules/interview_eval/test_config_validation.py::test_asr_max_duration_default_and_valid -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'interview_eval_asr_max_duration_sec'`

- [ ] **Step 3: 加配置项**

`app/config.py` 第 64 行 `interview_eval_recording_retention_days: int = 180` 之后插入：

```python
    # 无 COS 模式下 ASR 可处理的录像时长上限（秒）。audio_extract 在 16kbps 下
    # 仍超 4.5MB 时拒绝；默认 1680s ≈ 28 分钟。
    interview_eval_asr_max_duration_sec: int = Field(default=1680, ge=60)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/modules/interview_eval/test_config_validation.py -v`
Expected: PASS（全部，含原有 6 个）

- [ ] **Step 5: 加依赖并安装**

`requirements.txt` 末尾追加：

```
imageio-ffmpeg>=0.5.1  # F-interview-eval: 无 COS 模式抽音频，自带静态 ffmpeg 二进制
```

Run: `python -m pip install "imageio-ffmpeg>=0.5.1"`
Expected: 安装成功；`python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"` 打印一个 .exe 路径

- [ ] **Step 6: 提交**

```bash
git add app/config.py requirements.txt tests/modules/interview_eval/test_config_validation.py
git commit -m "feat(ie): 加 asr_max_duration 配置项 + imageio-ffmpeg 依赖"
```

---

## Task 2: audio_extract.py — mp4 抽音频压缩

**Files:**
- Create: `app/modules/interview_eval/audio_extract.py`
- Test: `tests/modules/interview_eval/test_audio_extract.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/modules/interview_eval/test_audio_extract.py`：

```python
"""audio_extract: mp4 → 压缩 mp3，动态码率 + 超长拒绝。"""
import os
from unittest.mock import MagicMock

import pytest


def test_compute_bitrate_clamps():
    from app.modules.interview_eval.audio_extract import (
        _compute_bitrate, BITRATE_FLOOR_BPS, BITRATE_CEIL_BPS,
    )
    # 短录像 → 顶到 ceil
    assert _compute_bitrate(60, 4_500_000) == BITRATE_CEIL_BPS
    # 中等录像 → 落在区间内
    mid = _compute_bitrate(1200, 4_500_000)
    assert BITRATE_FLOOR_BPS < mid < BITRATE_CEIL_BPS
    # 长录像 → 砸到 floor
    assert _compute_bitrate(2200, 4_500_000) == BITRATE_FLOOR_BPS


def test_extract_audio_happy_path(tmp_path, monkeypatch):
    from app.modules.interview_eval import audio_extract

    monkeypatch.setattr(audio_extract, "_probe_duration_sec", lambda p: 300.0)
    monkeypatch.setattr(audio_extract, "_ffmpeg_exe", lambda: "ffmpeg")

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        with open(cmd[-1], "wb") as f:
            f.write(b"\x00" * 2048)
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(audio_extract.subprocess, "run", fake_run)

    mp4 = tmp_path / "rec.mp4"
    mp4.write_bytes(b"\x00" * 100)
    out_path = audio_extract.extract_audio(str(mp4))
    assert out_path.endswith(".mp3")
    assert os.path.exists(out_path)
    assert "-ac" in captured["cmd"] and "16000" in captured["cmd"]
    os.remove(out_path)


def test_extract_audio_rejects_too_long_by_duration(tmp_path, monkeypatch):
    from app.modules.interview_eval import audio_extract
    monkeypatch.setattr(audio_extract, "_probe_duration_sec", lambda p: 2000.0)
    mp4 = tmp_path / "long.mp4"
    mp4.write_bytes(b"\x00")
    with pytest.raises(RuntimeError) as exc:
        audio_extract.extract_audio(str(mp4), max_duration_sec=1680)
    assert "COS" in str(exc.value)


def test_extract_audio_rejects_too_long_by_size(tmp_path, monkeypatch):
    from app.modules.interview_eval import audio_extract
    # max_duration 放宽到 4000，但 16kbps × 3000s / 8 = 6MB > 4.5MB → 拒绝
    monkeypatch.setattr(audio_extract, "_probe_duration_sec", lambda p: 3000.0)
    mp4 = tmp_path / "long.mp4"
    mp4.write_bytes(b"\x00")
    with pytest.raises(RuntimeError) as exc:
        audio_extract.extract_audio(str(mp4), max_bytes=4_500_000, max_duration_sec=4000)
    assert "COS" in str(exc.value)


def test_extract_audio_ffmpeg_failure_cleans_tmp(tmp_path, monkeypatch):
    from app.modules.interview_eval import audio_extract
    monkeypatch.setattr(audio_extract, "_probe_duration_sec", lambda p: 300.0)
    monkeypatch.setattr(audio_extract, "_ffmpeg_exe", lambda: "ffmpeg")

    leaked = []

    def fake_run(cmd, **kw):
        leaked.append(cmd[-1])  # 记下 tmp 路径
        return MagicMock(returncode=1, stderr="boom: invalid data")

    monkeypatch.setattr(audio_extract.subprocess, "run", fake_run)

    mp4 = tmp_path / "rec.mp4"
    mp4.write_bytes(b"\x00")
    with pytest.raises(RuntimeError) as exc:
        audio_extract.extract_audio(str(mp4))
    assert "音频抽取失败" in str(exc.value)
    # 失败时临时文件已清理
    assert not os.path.exists(leaked[0])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/modules/interview_eval/test_audio_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.interview_eval.audio_extract'`

- [ ] **Step 3: 实现 audio_extract.py**

新建 `app/modules/interview_eval/audio_extract.py`：

```python
"""F-interview-eval: mp4 → 压缩 mp3，绕开腾讯云 ASR base64 5MB 上限（无 COS 模式）。

用 imageio-ffmpeg 自带的静态 ffmpeg 二进制，无需系统安装。码率按时长动态算，
保证输出 ≤max_bytes；时长过长（16kbps 仍超限）则拒绝并提示需启用 COS。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile

import imageio_ffmpeg

from app.config import settings

logger = logging.getLogger(__name__)

BITRATE_FLOOR_BPS = 16_000   # 低于此码率 ASR 准确率塌掉
BITRATE_CEIL_BPS = 64_000    # 短录像也没必要超过
SIZE_SAFETY = 0.92           # mp3 帧/容器开销余量


def _ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _probe_duration_sec(mp4_path: str) -> float:
    """ffmpeg -i 解析 stderr 里的 Duration 行，返回秒数。"""
    proc = subprocess.run(
        [_ffmpeg_exe(), "-i", mp4_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # ffmpeg -i 无输出文件会非零退出，但 stderr 里有 Duration 信息
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        raise RuntimeError(
            f"无法解析录像时长（ffmpeg -i 输出异常）：{proc.stderr[-300:]}"
        )
    h, mm, ss = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(ss)


def _compute_bitrate(duration_sec: float, max_bytes: int) -> int:
    """按时长反推码率，保证 mp3 ≤ max_bytes，clamp 到 [floor, ceil]。"""
    raw = int(max_bytes * 8 / duration_sec * SIZE_SAFETY)
    return max(BITRATE_FLOOR_BPS, min(BITRATE_CEIL_BPS, raw))


def extract_audio(
    mp4_path: str,
    max_bytes: int = 4_500_000,
    max_duration_sec: int | None = None,
) -> str:
    """mp4 → 压缩 mp3（单声道 16kHz），保证 ≤max_bytes。

    返回临时 mp3 路径，调用方负责删除。

    Raises:
        RuntimeError: 录像时长超上限（duration > max_duration_sec，
                      或 16kbps 下仍超 max_bytes，二者取严）
        RuntimeError: ffmpeg 执行失败
    """
    if max_duration_sec is None:
        max_duration_sec = settings.interview_eval_asr_max_duration_sec

    duration = _probe_duration_sec(mp4_path)

    # 上限检查 1：配置的时长上界
    if duration > max_duration_sec:
        raise RuntimeError(
            f"录像约 {int(duration / 60)} 分钟，超出无 COS 模式上限 "
            f"~{int(max_duration_sec / 60)} 分钟，需启用 COS"
        )
    # 上限检查 2：16kbps 下仍超 max_bytes
    if BITRATE_FLOOR_BPS * duration / 8 > max_bytes:
        raise RuntimeError(
            f"录像约 {int(duration / 60)} 分钟，即使 16kbps 压缩仍超 "
            f"{max_bytes // 1024 // 1024}MB，需启用 COS"
        )

    bitrate = _compute_bitrate(duration, max_bytes)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()

    cmd = [
        _ffmpeg_exe(), "-y", "-i", mp4_path,
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", str(bitrate),
        "-f", "mp3", tmp.name,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        raise RuntimeError(
            f"音频抽取失败（ffmpeg exit {proc.returncode}）：{proc.stderr[-300:]}"
        )

    logger.info(
        "extract_audio: %s → %s (duration=%.0fs, bitrate=%dbps)",
        mp4_path, tmp.name, duration, bitrate,
    )
    return tmp.name
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/modules/interview_eval/test_audio_extract.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: 提交**

```bash
git add app/modules/interview_eval/audio_extract.py tests/modules/interview_eval/test_audio_extract.py
git commit -m "feat(ie): 新增 audio_extract — mp4 抽音频动态码率压缩"
```

---

## Task 3: tencent_asr.py 接线抽音频

**Files:**
- Modify: `app/modules/interview_eval/tencent_asr.py`
- Test: `tests/modules/interview_eval/test_tencent_asr.py`

- [ ] **Step 1: 改测试 — autouse fixture 加 mock + 新增清理测试**

把 `tests/modules/interview_eval/test_tencent_asr.py` 的 `_no_sleep_no_real_client` fixture 整体替换为（加 `tmp_path` 参数 + mock `extract_audio`）：

```python
@pytest.fixture(autouse=True)
def _no_sleep_no_real_client(monkeypatch, tmp_path):
    """开发期凭证为空 + 不真等轮询 + 不跑真 ffmpeg 抽音频。

    1. POLL_INTERVAL_S = 0
    2. _get_client → MagicMock
    3. 灌测试凭证绕过 IE-010 fail-fast
    4. extract_audio → 返回独立的小临时 mp3（不跑真 ffmpeg）
    """
    from app.modules.interview_eval import tencent_asr
    from app.modules.interview_eval import audio_extract
    from app.config import settings

    monkeypatch.setattr(tencent_asr, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(tencent_asr, "_get_client", lambda: MagicMock())
    monkeypatch.setattr(settings, "tencent_cloud_secret_id", "test-id")
    monkeypatch.setattr(settings, "tencent_cloud_secret_key", "test-key")

    def _fake_extract(mp4_path, **kw):
        p = tmp_path / "fake_audio.mp3"
        p.write_bytes(b"\x00" * 512)
        return str(p)

    monkeypatch.setattr(audio_extract, "extract_audio", _fake_extract)
    yield
```

然后在文件末尾追加新测试：

```python


def test_transcribe_extracts_audio_and_cleans_up(tmp_path, monkeypatch):
    """transcribe 走 extract_audio，结束后删临时音频。"""
    from app.modules.interview_eval import tencent_asr
    from app.modules.interview_eval import audio_extract

    created = []

    def _fake_extract(mp4_path, **kw):
        p = tmp_path / "audio_to_clean.mp3"
        p.write_bytes(b"\x00" * 256)
        created.append(str(p))
        return str(p)

    monkeypatch.setattr(audio_extract, "extract_audio", _fake_extract)

    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"\x00" * 1024)
    with patch.object(tencent_asr, "_submit_task", return_value={"Data": {"TaskId": 1}}), \
         patch.object(tencent_asr, "_query_task", return_value={
             "Data": {"Status": 2, "ResultDetail": [
                 {"StartMs": 0, "EndMs": 1000, "SpeakerId": 0, "FinalSentence": "hi"}]}}):
        result = tencent_asr.transcribe(str(mp4))
    assert len(result) == 1
    assert not os.path.exists(created[0])  # 临时音频已清理
```

文件头部的 import 区加一行 `import os`（若没有）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/modules/interview_eval/test_tencent_asr.py::test_transcribe_extracts_audio_and_cleans_up -v`
Expected: FAIL — `transcribe` 当前未调用 `extract_audio`，临时文件不会被创建/清理（断言 `os` 未 import 或 `created` 为空）

- [ ] **Step 3: 改 transcribe()**

`app/modules/interview_eval/tencent_asr.py`：

a) 模块顶部 import 区加 `import os`（在 `import logging` 后）。

b) 把整个 `transcribe()` 函数体替换为：

```python
def transcribe(mp4_path: str) -> list[dict[str, Any]]:
    """提交 → 轮询 → 返回结构化 [{start_ms, end_ms, speaker, text}].

    无 COS 模式：先 extract_audio 抽音频压缩到 ≤4.5MB，再 base64 提交 ASR。

    Raises:
        RuntimeError: 凭证未配置 / 鉴权失败 / 配额超限 / 识别失败 / 轮询超时 /
                      录像过长（audio_extract 抛）
    """
    # IE-010: 凭证空时 fail-fast，避免 SDK 抛 AuthFailure 误导用户
    if not settings.tencent_cloud_secret_id or not settings.tencent_cloud_secret_key:
        raise RuntimeError(
            "腾讯云 ASR 凭证未配置：请在 .env 设置 "
            "TENCENT_CLOUD_SECRET_ID / TENCENT_CLOUD_SECRET_KEY"
        )

    # 无 COS 模式：抽音频压缩绕开 base64 5MB 上限
    from app.modules.interview_eval.audio_extract import extract_audio
    audio_path = extract_audio(mp4_path)
    try:
        try:
            client = _get_client()
            submit_resp = _submit_task(client, audio_path)
            task_id = submit_resp["Data"]["TaskId"]
        except TencentCloudSDKException as e:
            if "AuthFailure" in str(e):
                raise RuntimeError("腾讯云 ASR 鉴权失败，请检查 .env 凭证") from e
            if "Quota" in str(e):
                raise RuntimeError("腾讯云 ASR 配额超限") from e
            raise RuntimeError(f"腾讯云 ASR 调用失败：{e}") from e

        for _ in range(POLL_MAX_ATTEMPTS):
            time.sleep(POLL_INTERVAL_S)
            try:
                r = _query_task(client, task_id)
            except TencentCloudSDKException as e:
                raise RuntimeError(f"ASR 查询失败：{e}") from e
            status = r.get("Data", {}).get("Status", 0)
            if status == 2:  # 成功
                detail = r["Data"].get("ResultDetail", []) or []
                speaker_map = _map_speakers(detail)
                return [
                    {
                        "start_ms": int(seg["StartMs"]),
                        "end_ms": int(seg["EndMs"]),
                        "speaker": speaker_map.get(seg.get("SpeakerId", 0), "candidate"),
                        "text": seg.get("FinalSentence", ""),
                    }
                    for seg in detail
                ]
            if status == 3:  # 失败
                raise RuntimeError(
                    f"ASR 识别失败：{r.get('Data', {}).get('ErrorMsg', '未知错误')}"
                )
        raise RuntimeError(f"ASR 轮询超时（{POLL_MAX_ATTEMPTS * POLL_INTERVAL_S}s）")
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass
```

注：`_submit_task` 不改 —— 它现在收到的是 ≤4.5MB 的音频文件，原有 5MB 防御性校验天然通过；`test_submit_task_oversize_mp4_rejected` 仍验证该防御边界。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/modules/interview_eval/test_tencent_asr.py -v`
Expected: PASS（原有 8 个 + 新增 1 个 = 9 个）

- [ ] **Step 5: 提交**

```bash
git add app/modules/interview_eval/tencent_asr.py tests/modules/interview_eval/test_tencent_asr.py
git commit -m "feat(ie): tencent_asr 接线 audio_extract，绕开 base64 5MB 上限"
```

---

## Task 4: 实地抓腾讯会议录制页 DOM（投查任务，非 TDD）

**前置：** 用户已在 `data/meeting_browser_main` profile 扫码登录腾讯会议，且 `main` 账号下有至少一场云录制（哪怕旧的，用于看 DOM 结构）。

**Files:** 无代码改动 —— 产出是「selector findings」记录，供 Task 5 使用。

- [ ] **Step 1: 确认录像就绪**

询问用户其录制的测试会议是否已生成完成（云录制在会议结束后需几分钟处理）。若未就绪，等待。

- [ ] **Step 2: 用 Playwright 打开录制页**

写一个一次性脚本 `scripts_tmp/inspect_record_page.py`（投查后删除）：

```python
"""一次性：打开腾讯会议录制页，dump DOM 结构供填 selector。投查后删除。"""
import time
from playwright.sync_api import sync_playwright
from app.adapters.tencent_meeting_web import browser_data_dir_for

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=browser_data_dir_for("main"), headless=False, timeout=60_000,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://meeting.tencent.com/user-center/meeting-record",
              wait_until="networkidle")
    time.sleep(3)
    print("URL:", page.url)
    # dump 录制列表区域的 outerHTML（按实际容器收窄）
    print(page.content()[:20000])
    input("按回车关闭...")
    ctx.close()
```

Run: `python scripts_tmp/inspect_record_page.py`

- [ ] **Step 3: 记录 selector findings**

从 dump 的 DOM 里确定并写下（作为 Task 5 实现依据）：
1. **录制列表行容器** selector（每场录制一行/一卡）
2. **meeting_id 在行内如何暴露**（属性？文本？需匹配 `interview.meeting_id`）—— 若 DOM 不直接暴露会议号，回退用「会议主题 + 日期」匹配
3. **录像时长** 在行内的位置（抓不到则填 0）
4. **录像状态** —— 「生成中 / 可下载」如何区分（按钮 disabled？状态文本？）
5. **下载触发方式** —— 行内「下载」按钮 selector；点击后是直接触发 `download` 事件，还是先弹菜单再点

把 findings 以注释块写进 `tencent_meeting_recording.py` 文件头，并在本 plan 此处补记。

- [ ] **Step 4: 清理投查脚本**

```bash
rm scripts_tmp/inspect_record_page.py
```

---

## Task 5: 重写 tencent_meeting_recording.py 的 download()

**Files:**
- Modify: `app/modules/interview_eval/tencent_meeting_recording.py`
- Test: `tests/modules/interview_eval/test_tencent_meeting_recording.py`

- [ ] **Step 1: 重写测试**

把 `tests/modules/interview_eval/test_tencent_meeting_recording.py` 整体替换为（mock `_open_record_page` + 4 个 selector 辅助函数，测试不依赖真实 selector）：

```python
"""tencent_meeting_recording.download — A2 expect_download 路径 mock 测试。"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


def _make_interview(meeting_id="abc-123", account="main"):
    from app.modules.scheduling.models import Interview
    return Interview(
        id=1, resume_id=1, interviewer_id=1, meeting_id=meeting_id,
        meeting_account=account,
        start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc),
    )


class _FakeDLInfo:
    def __init__(self, download):
        self.value = download


class _FakeExpectDownload:
    """模拟 page.expect_download() 返回的上下文管理器。"""
    def __init__(self, download):
        self._info = _FakeDLInfo(download)

    def __enter__(self):
        return self._info

    def __exit__(self, *a):
        return False


def test_download_login_expired():
    """页面跳到登录页 → RuntimeError 含登录/扫码字样。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    fake_page = MagicMock()
    fake_page.url = "https://meeting.tencent.com/login"
    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)):
        with pytest.raises(RuntimeError) as exc:
            tmr.download(_make_interview(), "/tmp/x.mp4")
        assert "登录" in str(exc.value) or "扫码" in str(exc.value)


def test_download_recording_not_found():
    """meeting_id 匹配不到录制行 → RuntimeError 录像未生成。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    fake_page = MagicMock()
    fake_page.url = "https://meeting.tencent.com/user-center/meeting-record"
    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)), \
         patch.object(tmr, "_find_recording_row", return_value=None):
        with pytest.raises(RuntimeError) as exc:
            tmr.download(_make_interview(meeting_id="not-found"), "/tmp/x.mp4")
        assert "录像未生成" in str(exc.value) or "not-found" in str(exc.value)


def test_download_recording_generating():
    """录像状态为生成中 → RuntimeError 尚未生成完成。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    fake_page = MagicMock()
    fake_page.url = "https://meeting.tencent.com/user-center/meeting-record"
    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)), \
         patch.object(tmr, "_find_recording_row", return_value=MagicMock()), \
         patch.object(tmr, "_is_generating", return_value=True):
        with pytest.raises(RuntimeError) as exc:
            tmr.download(_make_interview(), "/tmp/x.mp4")
        assert "尚未生成" in str(exc.value)


def test_download_happy_path(tmp_path):
    """正常路径：找到行 → expect_download → save_as → 返回 (path, size, duration)。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr

    dest = str(tmp_path / "1.mp4")
    fake_download = MagicMock()
    fake_download.save_as = lambda p: open(p, "wb").write(b"\x00" * 4096)
    fake_page = MagicMock()
    fake_page.url = "https://meeting.tencent.com/user-center/meeting-record"
    fake_page.expect_download.return_value = _FakeExpectDownload(fake_download)

    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)), \
         patch.object(tmr, "_find_recording_row", return_value=MagicMock()), \
         patch.object(tmr, "_is_generating", return_value=False), \
         patch.object(tmr, "_extract_duration", return_value=180), \
         patch.object(tmr, "_click_download"):
        path, size, duration = tmr.download(_make_interview(), dest)
        assert path == dest
        assert size == 4096
        assert duration == 180


def test_download_oversize_guard(tmp_path, monkeypatch):
    """下载文件超过 MAX_DOWNLOAD_BYTES → 删文件 + RuntimeError。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    import os

    monkeypatch.setattr(tmr, "MAX_DOWNLOAD_BYTES", 10)  # 人为压低到 10 字节
    dest = str(tmp_path / "big.mp4")
    fake_download = MagicMock()
    fake_download.save_as = lambda p: open(p, "wb").write(b"\x00" * 1024)
    fake_page = MagicMock()
    fake_page.url = "https://meeting.tencent.com/user-center/meeting-record"
    fake_page.expect_download.return_value = _FakeExpectDownload(fake_download)

    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)), \
         patch.object(tmr, "_find_recording_row", return_value=MagicMock()), \
         patch.object(tmr, "_is_generating", return_value=False), \
         patch.object(tmr, "_extract_duration", return_value=60), \
         patch.object(tmr, "_click_download"):
        with pytest.raises(RuntimeError) as exc:
            tmr.download(_make_interview(), dest)
        assert "2GB" in str(exc.value) or "上限" in str(exc.value)
        assert not os.path.exists(dest)  # 超限文件已删
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/modules/interview_eval/test_tencent_meeting_recording.py -v`
Expected: FAIL — `_find_recording_row` / `_is_generating` / `_extract_duration` / `_click_download` 不存在（`AttributeError`）

- [ ] **Step 3: 重写 tencent_meeting_recording.py**

整体替换 `app/modules/interview_eval/tencent_meeting_recording.py`。下面是完整结构 —— **4 个 selector 辅助函数的函数体用 Task 4 findings 填实**（findings 注释块放文件头）；`download()` 编排逻辑、错误处理、2GB 守卫、context 生命周期都是确定的，照抄：

```python
"""F-interview-eval：从腾讯会议录制页下载 mp4。

复用 meeting/account_pool 的 Playwright 持久化 profile（已登录态），
A2 方案：在已登录会话内点「下载」按钮，page.expect_download() 接住文件落盘。

约束：腾讯会议免费版只能在 web 端管理本人录制；超出 1GB 配额会失败。

=== Task 4 实地 DOM findings（2026-05-14 抓自真实录制页）===
[此处填 Task 4 Step 3 记录的 5 项 findings：行容器 / meeting_id 暴露方式 /
 时长位置 / 生成中状态判定 / 下载触发方式]
"""
from __future__ import annotations

import logging
import os

from playwright.sync_api import sync_playwright

# browser_data_dir_for 定义在 tencent_meeting_web（同一份持久化 profile 约定）
from app.adapters.tencent_meeting_web import browser_data_dir_for

logger = logging.getLogger(__name__)

RECORD_LIST_URL = "https://meeting.tencent.com/user-center/meeting-record"
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 单录像 ≤2GB（防磁盘写爆）
DOWNLOAD_TIMEOUT_MS = 300_000  # 下载最长等 5 分钟


def _open_record_page(account_label: str):
    """返回 (browser_context, page)；调用方负责关闭。"""
    p = sync_playwright().start()
    user_data_dir = browser_data_dir_for(account_label)
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir, headless=False, timeout=60_000,
        accept_downloads=True,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(RECORD_LIST_URL, wait_until="networkidle")
    return ctx, page


def _find_recording_row(page, meeting_id: str):
    """在录制列表里找匹配 meeting_id 的行；找不到返回 None。

    实现依据 Task 4 findings：优先按 meeting_id 精确匹配，DOM 不暴露会议号
    时回退「会议主题 + 日期」。返回一个可继续 query 的 Locator/ElementHandle。
    """
    # [Task 4 findings 填实：列表行 selector + meeting_id 匹配逻辑]
    raise NotImplementedError("Task 5 Step 3：用 Task 4 findings 填实")


def _is_generating(row) -> bool:
    """该录制是否仍在「生成中」（不可下载）。"""
    # [Task 4 findings 填实：生成中状态判定]
    raise NotImplementedError("Task 5 Step 3：用 Task 4 findings 填实")


def _extract_duration(row) -> int:
    """从行里抓录像时长（秒）；抓不到返回 0。"""
    # [Task 4 findings 填实：时长位置；解析失败 return 0]
    raise NotImplementedError("Task 5 Step 3：用 Task 4 findings 填实")


def _click_download(page, row) -> None:
    """点行内「下载」按钮（必要时先开菜单）。调用方已包在 expect_download 上下文里。"""
    # [Task 4 findings 填实：下载按钮 selector + 是否需先开菜单]
    raise NotImplementedError("Task 5 Step 3：用 Task 4 findings 填实")


def download(interview, dest_path: str) -> tuple[str, int, int]:
    """根据 interview.meeting_id 在 interview.meeting_account 账号下抓 mp4。

    返回 (path, size_bytes, duration_sec)。

    Raises:
        RuntimeError: 登录过期 / 录像不存在 / 录像生成中 / 下载超 2GB / 网络失败
    """
    ctx, page = _open_record_page(interview.meeting_account)
    try:
        if "/login" in (page.url or ""):
            raise RuntimeError(
                f"腾讯会议账号 '{interview.meeting_account}' 登录态过期，"
                "请到 meeting.tencent.com 重新扫码登录"
            )

        row = _find_recording_row(page, interview.meeting_id)
        if row is None:
            raise RuntimeError(
                f"录像未生成或已被清理（meeting_id={interview.meeting_id}），"
                "请几分钟后重试，或检查云录制 1GB 配额是否已满"
            )
        if _is_generating(row):
            raise RuntimeError("录像尚未生成完成，请几分钟后重试")

        duration_sec = _extract_duration(row)

        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
            _click_download(page, row)
        dl_info.value.save_as(dest_path)

        size = os.path.getsize(dest_path)
        if size > MAX_DOWNLOAD_BYTES:
            try:
                os.remove(dest_path)
            except OSError:
                pass
            raise RuntimeError(
                f"录像超过单文件上限 {MAX_DOWNLOAD_BYTES // (1024**3)}GB，"
                "请缩短会议时长或调整 MAX_DOWNLOAD_BYTES"
            )

        logger.info(
            "download: meeting_id=%s → %s (%d bytes, %ds)",
            interview.meeting_id, dest_path, size, duration_sec,
        )
        return dest_path, size, duration_sec
    finally:
        try:
            ctx.close()
        except Exception:
            pass
```

实现 4 个辅助函数体后，删掉对应的 `raise NotImplementedError`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/modules/interview_eval/test_tencent_meeting_recording.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: 提交**

```bash
git add app/modules/interview_eval/tencent_meeting_recording.py tests/modules/interview_eval/test_tencent_meeting_recording.py
git commit -m "feat(ie): 重写录像下载 — A2 expect_download + 实地 selector"
```

---

## Task 6: 全量回归

**Files:** 无改动 —— 纯验证。

- [ ] **Step 1: interview_eval 模块全测**

Run: `python -m pytest tests/modules/interview_eval/ -v`
Expected: 全 PASS，零回归（原 18 个测试文件 + 本次改动）

- [ ] **Step 2: 后端全量测试**

Run: `python -m pytest tests/modules/ -q`
Expected: 全 PASS（对照 commit e42214f 的 742 passed 基线，不低于此数）

- [ ] **Step 3: 前端测试 + 类型检查**

Run: `cd frontend; pnpm test; pnpm typecheck`（PowerShell：分两条跑）
Expected: 均 PASS（本次未改前端，应与基线一致）

- [ ] **Step 4: 若有回归 → 修复后重跑，不带病进验收**

回归必须定位根因修复（不 xfail 绕过），重跑 Step 1-3 全绿才进 Task 7。

---

## Task 7: 真实端到端验收

**前置：** 用户的测试录像已生成完成；Task 1-6 全绿。

**Files:** 验收期间可能在 `data/recruitment.db` 建测试数据 —— 验收后清理。

- [ ] **Step 1: ASR 凭据早期冒烟**

写一次性脚本 `scripts_tmp/smoke_asr.py`（验后删）：

```python
"""一次性：验证 .env 腾讯云 ASR 凭据有效。生成 3 秒静音音频跑一次 ASR。"""
import subprocess, tempfile, imageio_ffmpeg
from app.modules.interview_eval import tencent_asr

# 生成 3 秒静音 mp3
tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False); tmp.close()
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi",
                "-i", "anullsrc=r=16000:cl=mono", "-t", "3", tmp.name],
               capture_output=True)
client = tencent_asr._get_client()
try:
    resp = tencent_asr._submit_task(client, tmp.name)
    print("ASR 凭据有效，TaskId:", resp["Data"]["TaskId"])
except Exception as e:
    print("ASR 凭据冒烟失败:", e)
    raise
```

Run: `python scripts_tmp/smoke_asr.py`
Expected: 打印 "ASR 凭据有效, TaskId: ..."。
**若 AuthFailure → 停止，找用户要有效的 `TENCENT_CLOUD_SECRET_ID/KEY`，不空跑全链路。**
通过后 `rm scripts_tmp/smoke_asr.py`。

- [ ] **Step 2: 预置真实验收数据**

用户提供其测试会议的真实 `meeting_id`（腾讯会议 9 位会议号或录制页显示的标识）。写一次性脚本 `scripts_tmp/seed_acceptance.py`（验后删）插入：
- 1 个 `Job`：`competency_model_status='approved'`，`competency_model` 含至少 2 个 `assessment_dimensions`
- 1 个 `Resume`：候选人基本信息
- 1 个 `Interview`：`meeting_id=<真实会议号>`、`meeting_account='main'`、关联上面的 job/resume

打印插入的 `interview_id`。（脚本按 `app/modules/scheduling/models.py`、`app/modules/screening/models.py`、`app/modules/resume/models.py` 的真实字段写，执行时现读现写。）

- [ ] **Step 3: 启后端 + 触发分析**

```
# 启后端（后台）
python launcher.py   # 或 uvicorn app.main:app --port 8000
```
确认启动日志含 interview_eval router 已挂载（`settings.interview_eval_enabled` + `tencent_cloud_secret_id` 均真）。

用 gen_token.py 拿 JWT，`POST /api/interview-eval/start` body `{"interview_id": <Step 2 的 id>}`。

- [ ] **Step 4: 轮询任务到终态**

`GET /api/interview-eval/{job_id}` 轮询，观察 status 走 `pending → downloading → transcribing → scoring → done`。
若 `failed` → 读 `error_msg` 定位（登录态 / 录像未生成 / ASR / LLM），修复根因后重试。

- [ ] **Step 5: 验证证据（全部贴真实输出）**

逐项确认并贴输出：
1. `data/recordings/{job_id}.mp4` 真实落盘、文件头是真 mp4（非 `DEMO_MP4_PLACEHOLDER`）、可播放
2. `data/transcripts/{job_id}.json` 有真实 ASR 分段（中文文本 + start_ms/end_ms + speaker 标注）
3. `interview_eval_scorecards` 表有该 job 的行：`dimensions_json` 维度数 == competency_model 维度数、`hire_recommendation` 合法、strengths/risks/followups 非空
4. `audit_events` 表该 job 有 7 类事件：`ieval_start` / `download_recording` / `asr_call` / `llm_call` / `publish`（+ 过程事件）
5. 前端：启 `frontend` dev server，登录，进对应面试详情页「AI 面评」Tab，截图确认 scorecard 渲染（录用建议徽章 + 维度卡 + 优势/风险/追问 + 转录稿折叠面板）

- [ ] **Step 6: 清理验收临时物**

```bash
rm -f scripts_tmp/seed_acceptance.py
# 验收测试数据：保留或清理由用户定；默认保留 job_id 记录供用户复看
```
`git status` 确认无遗留临时脚本/未跟踪垃圾文件。

- [ ] **Step 7: 收尾提交 + 验收报告**

```bash
git add -A
git commit -m "test(ie): F-interview-eval 真实端到端验收通过"
```
写验收报告到 `docs/superpowers/reports/2026-05-14-interview-eval-acceptance.md`：贴 5 项证据 + 真实花费（ASR 时长 × 费率 + LLM token）。

---

## Self-Review

**Spec coverage：**
- R1 复用 Playwright profile → Task 5 `_open_record_page` 用 `browser_data_dir_for`
- R2 ffmpeg 抽音频 + 动态码率 + base64 → Task 2 + Task 3
- R3 imageio-ffmpeg → Task 1 Step 5
- R4 mp3 单声道 16kHz → Task 2 `extract_audio` cmd
- R5 超长拒绝 → Task 2 两道上限检查
- R6 A2 expect_download → Task 5 `download()`
- R7 实地抓 DOM → Task 4
- R8 worker 零改动 → Task 3 抽音频藏在 transcribe 内部；plan 全程不碰 worker.py
- R9 改动范围 → 文件结构表「不碰」清单
- R10 真实验收 → Task 7
- spec §4.5 config 项 → Task 1
- spec §7 测试（改 2 新 1 + 18 零回归）→ Task 1/2/3/5 测试步骤 + Task 6
- spec §8 验收 7 步 → Task 7

**Placeholder scan：** Task 5 的 4 个 selector 辅助函数体标注 `[Task 4 findings 填实]` —— 这不是偷懒占位，是 spec R7 明确锁定的「先投查后实现」依赖；编排逻辑/错误处理/测试均已完整给出，执行时仅填 4 个 selector 字符串。其余无 TBD/TODO。

**Type consistency：** `extract_audio(mp4_path, max_bytes, max_duration_sec)` 签名 Task 2/3 一致；`download(interview, dest_path) -> tuple[str,int,int]` 与 worker `_download_recording` 调用契约一致；辅助函数名 `_find_recording_row/_is_generating/_extract_duration/_click_download` 在 Task 5 实现与测试间一致。

---

## REVISION 2026-05-14 — Path B 主 + Path A 兜底（Task 4 投查后重排）

见 spec REVISION 节（R11）。Task 1-3 已完成（不变，作为 Path A 兜底链路）。**Task 4-7 作废，重排为下列 Task 4-9。**

### Task 4（已完成）：实地抓录制页 DOM —— 两路 findings

投查产物在 `artifacts/ie-acceptance/`（record_page.png/html、rows_probe.json、export.json、player.json、saveas.json、transcript_probe.json 等）。关键 selector：

- **录制列表行**：`tr[class*="recordListRow"]`；会议号文本在 `[class*="recordInfo"] span`，格式 `会议号：XXX XXX XXX`（带空格）
- **进播放页**：点行内视频封面 `[class*="VideoCover_Video"]` → `ctx.expect_page()` 接新 tab，URL `meeting.tencent.com/ctw/...`
- **Path B 转写稿**：播放页右侧「逐字稿」，条目容器 `.minutes-module-paragraph-box`（每条含说话人块 `.minutes-module-speaker` / 名+时间 `.minutes-module-name-time`）；实现时需再 1 次微探确认「名 / 时间 / 正文」子 selector
- **Path A 下载**：播放页 `div.met-dropdown.saveas-btn`（「另存为」）→ 下拉「下载至本地」；`met-dropdown` 组件需 hover/click `.met-dropdown__header` 触发，实现时需再 1 次微探确认下拉项 selector
- **不可用**：列表行「导出」下的是转写稿 `.docx`（非 mp4），弃用

### Task 5：Path B —— `scrape_transcript()` + 微探子结构

**Files:** Modify `app/modules/interview_eval/tencent_meeting_recording.py`；Test `tests/modules/interview_eval/test_tencent_meeting_recording.py`

- [ ] 微探 1 次：确认转写稿条目内「说话人名 / mm:ss 时间 / 正文」子 selector（写 `scripts_tmp/inspect_transcript2.py`，投后删）
- [ ] 写失败测试：`test_scrape_transcript_*` —— mock Playwright，验证：有转写稿→返回 `[{start_ms,end_ms,speaker,text}]`（speaker 归一 interviewer/candidate）；无逐字稿面板→抛 `TranscriptUnavailable`；登录过期→`RuntimeError`
- [ ] 实现：
  - `class TranscriptUnavailable(Exception)` —— Path B 不可用信号（触发兜底）
  - `_open_player_page(interview) -> (ctx, page)` —— 列表页→按 meeting_id 找行→点封面→接新 tab→等播放页加载
  - `scrape_transcript(interview) -> list[dict]` —— scrape `.minutes-module-paragraph-box`；每条解析说话人名+`mm:ss`→`start_ms`+正文；`mm:ss` 转 `start_ms`，`end_ms` 用下一条 start（末条 +一个估值）；说话人按发言时长启发式归一 `interviewer`/`candidate`；0 条 → 抛 `TranscriptUnavailable`；登录态过期 → `RuntimeError`
- [ ] 跑测试绿 + 提交

### Task 6：Path A 兜底 —— `download()` + 微探 saveas

**Files:** Modify `tencent_meeting_recording.py`；Test 同上文件

- [ ] 微探 1 次：确认「另存为」下拉触发方式 + 「下载至本地」exact selector（`scripts_tmp/inspect_saveas2.py`，投后删）
- [ ] 写失败测试：`test_download_*` —— mock Playwright：登录过期→`RuntimeError`；meeting_id 找不到行→`RuntimeError`；`expect_download` 成功路径→返回 `(path,size,duration)`；>2GB 守卫
- [ ] 实现 `download(interview, dest_path) -> tuple[str,int,int]`：复用 `_open_player_page`；播放页点「另存为」→「下载至本地」→ `page.expect_download()` 接住 `save_as(dest_path)`；2GB 守卫；duration 抓不到填 0
- [ ] 跑测试绿 + 提交

### Task 7：worker 混合编排 —— `_acquire_transcript()`

**Files:** Modify `app/modules/interview_eval/worker.py`；Test `tests/modules/interview_eval/test_worker.py`

- [ ] 读 `test_worker.py` 摸清现有状态机测试如何 mock `_download_recording`/`_transcribe`
- [ ] 写失败测试：worker 在 Path B 成功时不调 download；Path B 抛 `TranscriptUnavailable` 时回退调 download+transcribe；其余状态机路径（cancel/failed）不回归
- [ ] 实现：
  - 新增 `_acquire_transcript(interview) -> (transcript, recording_path, size, duration)`：先 `tencent_meeting_recording.scrape_transcript()`；捕获 `TranscriptUnavailable` → 回退 `_download_recording()` + `_transcribe()`；Path B 成功返回 `(transcript, "", 0, 0)`
  - 改 `run()`：原「download」+「transcribe」两段合并为一段 transcribe 步，调 `_acquire_transcript`；保留 `_audit("ieval_start")` / `_audit("asr_call", segments=...)` / `_set_status(..., recording_path/size/duration)` / 写 transcript json / `_check_cancel`
  - 保留 `_download_recording`/`_transcribe` 两个 seam（`_acquire_transcript` 内部调用，测试可 monkeypatch）
- [ ] 跑 `test_worker.py` + 全 `tests/modules/interview_eval/` 绿 + 提交

### Task 8：全量回归

- [ ] `python -m pytest tests/modules/interview_eval/ -v` 零回归
- [ ] `python -m pytest tests/modules/ -q` 不低于基线（e42214f：742 passed）
- [ ] `cd frontend; pnpm test; pnpm typecheck` 绿
- [ ] 有回归→修根因重跑

### Task 9：真实端到端验收

前置：用户 `main` 账号有真实录制（已确认有 15 个，含「转写_面试-Jeason-李博泽」会议号 204 110 229）。

- [ ] ASR 凭据冒烟（Path A 兜底链路验真，`scripts_tmp/smoke_asr.py`）—— 失败则停下找用户
- [ ] 预置真实数据：在 `data/recruitment.db` 建 job（competency_model approved）+ resume + interview（真实 meeting_id，如 `204110229`）
- [ ] 启后端，`POST /api/interview-eval/start`，轮询 job 走 `pending→transcribing→scoring→done`（Path B 命中，不下 mp4）
- [ ] 验证 5 项证据：transcript json 有真实分段 + scorecard 有真实 LLM 结论 + audit_events 齐 + 前端 AI 面评 Tab 渲染正常 + （可选）单独验一次 Path A 兜底
- [ ] 清理 `scripts_tmp/`、临时验收数据脚本；`git status` 无遗留垃圾
- [ ] 写验收报告 `docs/superpowers/reports/2026-05-14-interview-eval-acceptance.md`
