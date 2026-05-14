"""tencent_meeting_recording: Path B scrape_transcript（本文件）+ Path A download（Task 6 追加）。"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _make_interview(meeting_id="670210027", account="main"):
    from app.modules.scheduling.models import Interview
    return Interview(
        id=1, resume_id=1, interviewer_id=1, meeting_id=meeting_id,
        meeting_account=account,
        start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc),
    )


# ---- 纯函数 ----

def test_parse_ts_to_ms():
    from app.modules.interview_eval.tencent_meeting_recording import _parse_ts_to_ms
    assert _parse_ts_to_ms("00:27") == 27_000
    assert _parse_ts_to_ms("01:32") == 92_000
    assert _parse_ts_to_ms("1:02:03") == 3_723_000


def test_parse_raw_transcript_single_speaker():
    """只有一个说话人 → 全部 candidate。"""
    from app.modules.interview_eval.tencent_meeting_recording import _parse_raw_transcript
    raw = [
        {"name": "李博泽", "time": "00:27", "text": "你好"},
        {"name": "李博泽", "time": "00:35", "text": "我来介绍一下"},
    ]
    segs = _parse_raw_transcript(raw)
    assert len(segs) == 2
    assert all(s["speaker"] == "candidate" for s in segs)
    assert segs[0]["start_ms"] == 27_000
    assert segs[0]["end_ms"] == 35_000  # 下一条 start
    assert segs[1]["end_ms"] == 35_000 + 3000  # 末条 +3s


def test_parse_raw_transcript_two_speakers():
    """两个说话人 → 发言时长少的归 interviewer。"""
    from app.modules.interview_eval.tencent_meeting_recording import _parse_raw_transcript
    raw = [
        {"name": "面试官", "time": "00:00", "text": "请自我介绍"},          # 0-10s
        {"name": "候选人", "time": "00:10", "text": "我叫张三做后端三年"},   # 10-40s
        {"name": "候选人", "time": "00:40", "text": "项目主要用 Spring"},   # 40s+3
    ]
    segs = _parse_raw_transcript(raw)
    # 面试官发言 10s < 候选人发言 33s → 面试官=interviewer
    assert segs[0]["speaker"] == "interviewer"
    assert segs[1]["speaker"] == "candidate"
    assert segs[2]["speaker"] == "candidate"


def test_parse_raw_transcript_skips_empty_text():
    from app.modules.interview_eval.tencent_meeting_recording import _parse_raw_transcript
    raw = [
        {"name": "A", "time": "00:01", "text": "有内容"},
        {"name": "A", "time": "00:05", "text": ""},  # 空文本跳过
    ]
    segs = _parse_raw_transcript(raw)
    assert len(segs) == 1


# ---- _find_recording_row ----

def _mock_row(num_text):
    row = MagicMock()
    span = MagicMock()
    span.inner_text.return_value = num_text
    row.query_selector.return_value = span
    return row


def test_find_recording_row_match():
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    page = MagicMock()
    r1 = _mock_row("会议号：111 222 333")
    r2 = _mock_row("会议号：670 210 027")
    page.query_selector_all.return_value = [r1, r2]
    assert tmr._find_recording_row(page, "670210027") is r2


def test_find_recording_row_not_found():
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    page = MagicMock()
    page.query_selector_all.return_value = []
    assert tmr._find_recording_row(page, "999888777") is None


# ---- scrape_transcript ----

def test_scrape_transcript_happy(monkeypatch):
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    fake_ctx = MagicMock()
    fake_player = MagicMock()
    fake_player.evaluate.return_value = [
        {"name": "李博泽", "time": "00:27", "text": "喂，你能听见吗"},
        {"name": "李博泽", "time": "00:35", "text": "可以听见各位老师"},
    ]
    monkeypatch.setattr(tmr, "_open_player_page", lambda iv: (fake_ctx, fake_player))
    segs = tmr.scrape_transcript(_make_interview())
    assert len(segs) == 2
    assert segs[0]["text"] == "喂，你能听见吗"
    assert segs[0]["start_ms"] == 27_000
    assert segs[0]["speaker"] == "candidate"
    fake_ctx.close.assert_called_once()  # 资源清理


def test_scrape_transcript_unavailable(monkeypatch):
    """播放页无逐字稿 → TranscriptUnavailable（触发 Path A 兜底）。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    fake_ctx = MagicMock()
    fake_player = MagicMock()
    fake_player.evaluate.return_value = []  # 空
    monkeypatch.setattr(tmr, "_open_player_page", lambda iv: (fake_ctx, fake_player))
    with pytest.raises(tmr.TranscriptUnavailable):
        tmr.scrape_transcript(_make_interview())
    fake_ctx.close.assert_called_once()


# ---- Path A: download ----

class _FakeDLInfo:
    def __init__(self, download):
        self.value = download


class _FakeExpectDownload:
    """模拟 page.expect_download() 上下文管理器。"""
    def __init__(self, download):
        self._info = _FakeDLInfo(download)

    def __enter__(self):
        return self._info

    def __exit__(self, *a):
        return False


def test_download_happy_path(tmp_path, monkeypatch):
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    dest = str(tmp_path / "rec.mp4")
    fake_download = MagicMock()
    fake_download.save_as = lambda p: open(p, "wb").write(b"\x00" * 4096)
    fake_ctx = MagicMock()
    fake_player = MagicMock()
    fake_player.expect_download.return_value = _FakeExpectDownload(fake_download)
    monkeypatch.setattr(tmr, "_open_player_page", lambda iv: (fake_ctx, fake_player))
    monkeypatch.setattr(tmr, "_click_video_download", lambda player: None)

    path, size, duration = tmr.download(_make_interview(), dest)
    assert path == dest
    assert size == 4096
    assert duration == 0  # 播放页抓不到时长，填 0
    fake_ctx.close.assert_called_once()


def test_download_oversize_guard(tmp_path, monkeypatch):
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    import os
    monkeypatch.setattr(tmr, "MAX_DOWNLOAD_BYTES", 10)  # 人为压低
    dest = str(tmp_path / "big.mp4")
    fake_download = MagicMock()
    fake_download.save_as = lambda p: open(p, "wb").write(b"\x00" * 1024)
    fake_ctx = MagicMock()
    fake_player = MagicMock()
    fake_player.expect_download.return_value = _FakeExpectDownload(fake_download)
    monkeypatch.setattr(tmr, "_open_player_page", lambda iv: (fake_ctx, fake_player))
    monkeypatch.setattr(tmr, "_click_video_download", lambda player: None)

    with pytest.raises(RuntimeError) as exc:
        tmr.download(_make_interview(), dest)
    assert "2GB" in str(exc.value) or "上限" in str(exc.value)
    assert not os.path.exists(dest)  # 超限文件已删


def test_download_login_expired(monkeypatch):
    """_open_player_page 抛登录过期 → download 透传。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr

    def _raise(iv):
        raise RuntimeError("账号 'main' 登录态过期，请重新扫码登录")

    monkeypatch.setattr(tmr, "_open_player_page", _raise)
    with pytest.raises(RuntimeError) as exc:
        tmr.download(_make_interview(), "/tmp/x.mp4")
    assert "登录" in str(exc.value)


def test_click_video_download_unavailable():
    """原视频文件 display:none / 不可见 → RuntimeError。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    player = MagicMock()
    header = MagicMock()
    submenu = MagicMock()
    video_li = MagicMock()
    video_li.is_visible.return_value = False  # display:none

    def _qs(sel):
        if "met-dropdown__header" in sel:
            return header
        if "tea-list__submenu" in sel and "原视频文件" not in sel:
            return submenu
        if "原视频文件" in sel:
            return video_li
        return None

    player.query_selector.side_effect = _qs
    with pytest.raises(RuntimeError) as exc:
        tmr._click_video_download(player)
    assert "原视频文件" in str(exc.value)


def test_click_video_download_success():
    """所有元素就位 → 依次点击，不抛异常。"""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    player = MagicMock()
    header = MagicMock()
    submenu = MagicMock()
    video_li = MagicMock()
    video_li.is_visible.return_value = True

    def _qs(sel):
        if "met-dropdown__header" in sel:
            return header
        if "tea-list__submenu" in sel and "原视频文件" not in sel:
            return submenu
        if "原视频文件" in sel:
            return video_li
        return None

    player.query_selector.side_effect = _qs
    tmr._click_video_download(player)  # 不应抛
    header.click.assert_called_once()
    video_li.click.assert_called_once()
