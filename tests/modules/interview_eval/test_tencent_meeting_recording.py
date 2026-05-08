"""tencent_meeting_recording.download mock 测试."""
import pytest
from unittest.mock import MagicMock, patch


def test_download_happy_path(tmp_path, monkeypatch):
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    from app.modules.scheduling.models import Interview
    from datetime import datetime, timezone

    iv = Interview(
        id=1, resume_id=1, interviewer_id=1, meeting_id="abc-123",
        meeting_account="default",
        start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc),
    )

    fake_mp4_url = "https://meeting.tencent.com/storage/m/abc.mp4"
    fake_page = MagicMock()
    fake_page.evaluate.return_value = [
        {"meeting_id": "abc-123", "mp4_url": fake_mp4_url, "duration_sec": 1800}
    ]

    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)) as p:
        with patch.object(tmr, "_stream_download", side_effect=lambda url, dest: (
            open(dest, "wb").write(b"\x00" * 1024), 1024
        )[1]):
            dest = str(tmp_path / "1.mp4")
            path, size, duration = tmr.download(iv, dest)
            assert path == dest
            assert size == 1024
            assert duration == 1800


def test_download_recording_not_found(tmp_path):
    """meeting_id 在 record list 找不到 → RuntimeError 录像未生成."""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    from app.modules.scheduling.models import Interview
    from datetime import datetime, timezone

    iv = Interview(
        id=1, resume_id=1, interviewer_id=1, meeting_id="not-found",
        meeting_account="default",
        start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc),
    )
    fake_page = MagicMock()
    fake_page.evaluate.return_value = []  # 空 list
    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)):
        with pytest.raises(RuntimeError) as exc:
            tmr.download(iv, str(tmp_path / "x.mp4"))
        assert "录像未生成" in str(exc.value) or "not-found" in str(exc.value)


def test_download_login_expired():
    """登录态过期（页面跳到登录页）→ 抛带 'session 过期' 字样的 RuntimeError."""
    from app.modules.interview_eval import tencent_meeting_recording as tmr
    from app.modules.scheduling.models import Interview
    from datetime import datetime, timezone

    iv = Interview(
        id=1, resume_id=1, interviewer_id=1, meeting_id="m",
        meeting_account="default",
        start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc),
    )
    fake_page = MagicMock()
    fake_page.url = "https://meeting.tencent.com/login"
    with patch.object(tmr, "_open_record_page", return_value=(MagicMock(), fake_page)):
        with pytest.raises(RuntimeError) as exc:
            tmr.download(iv, "/tmp/x.mp4")
        assert "扫码" in str(exc.value) or "登录" in str(exc.value)
