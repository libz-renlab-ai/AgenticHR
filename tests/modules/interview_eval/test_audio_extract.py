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
