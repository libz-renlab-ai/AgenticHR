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
