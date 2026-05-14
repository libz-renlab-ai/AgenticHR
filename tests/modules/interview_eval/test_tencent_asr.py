"""mock 腾讯云 SDK；说话人映射启发式."""
import os
import pytest
from unittest.mock import MagicMock, patch


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


def test_transcribe_happy_path(tmp_path):
    from app.modules.interview_eval import tencent_asr

    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"\x00" * 1024)

    fake_submit = MagicMock(return_value={"Data": {"TaskId": 12345}})
    fake_query = MagicMock(return_value={
        "Data": {
            "Status": 2,  # 2 = 成功
            "ResultDetail": [
                {"StartMs": 0, "EndMs": 1000, "SpeakerId": 0, "FinalSentence": "你好你能介绍下自己吗"},
                {"StartMs": 1100, "EndMs": 4000, "SpeakerId": 1, "FinalSentence": "我是张三 用过 Spring 三年"},
                {"StartMs": 4100, "EndMs": 5000, "SpeakerId": 0, "FinalSentence": "项目里你负责什么"},
            ],
        }
    })
    with patch.object(tencent_asr, "_submit_task", fake_submit), \
         patch.object(tencent_asr, "_query_task", fake_query):
        result = tencent_asr.transcribe(str(mp4))
        assert len(result) == 3
        assert result[0]["start_ms"] == 0
        # 说话人映射：发言占比少的 SpeakerId=0 → interviewer，多的 SpeakerId=1 → candidate
        assert result[0]["speaker"] == "interviewer"
        assert result[1]["speaker"] == "candidate"
        assert result[2]["speaker"] == "interviewer"


def test_transcribe_auth_error(tmp_path):
    from app.modules.interview_eval import tencent_asr
    mp4 = tmp_path / "x.mp4"; mp4.write_bytes(b"\x00")
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )
    with patch.object(tencent_asr, "_submit_task",
                      side_effect=TencentCloudSDKException("AuthFailure", "invalid", "x")):
        with pytest.raises(RuntimeError) as exc:
            tencent_asr.transcribe(str(mp4))
        assert "鉴权" in str(exc.value) or "AuthFailure" in str(exc.value)


def test_transcribe_quota_exceeded(tmp_path):
    from app.modules.interview_eval import tencent_asr
    mp4 = tmp_path / "x.mp4"; mp4.write_bytes(b"\x00")
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )
    with patch.object(tencent_asr, "_submit_task",
                      side_effect=TencentCloudSDKException("QuotaExceeded", "...", "x")):
        with pytest.raises(RuntimeError) as exc:
            tencent_asr.transcribe(str(mp4))
        assert "配额" in str(exc.value) or "Quota" in str(exc.value)


def test_transcribe_query_failure(tmp_path):
    """Status=3 表示识别失败."""
    from app.modules.interview_eval import tencent_asr
    mp4 = tmp_path / "x.mp4"; mp4.write_bytes(b"\x00")

    with patch.object(tencent_asr, "_submit_task", return_value={"Data": {"TaskId": 1}}), \
         patch.object(tencent_asr, "_query_task",
                      return_value={"Data": {"Status": 3, "ErrorMsg": "音频损坏"}}):
        with pytest.raises(RuntimeError) as exc:
            tencent_asr.transcribe(str(mp4))
        assert "音频" in str(exc.value)


def test_speaker_map_only_one_speaker(tmp_path):
    """只有一个 SpeakerId → 全部归 candidate（保守）."""
    from app.modules.interview_eval import tencent_asr
    mp4 = tmp_path / "x.mp4"; mp4.write_bytes(b"\x00")

    with patch.object(tencent_asr, "_submit_task", return_value={"Data": {"TaskId": 1}}), \
         patch.object(tencent_asr, "_query_task", return_value={
             "Data": {
                 "Status": 2,
                 "ResultDetail": [
                     {"StartMs": 0, "EndMs": 1000, "SpeakerId": 0, "FinalSentence": "x"},
                     {"StartMs": 1000, "EndMs": 2000, "SpeakerId": 0, "FinalSentence": "y"},
                 ],
             }
         }):
        result = tencent_asr.transcribe(str(mp4))
        assert all(s["speaker"] == "candidate" for s in result)


# ===== Round 11 chaos QA 回归测试 =====

def test_transcribe_no_credentials_fail_fast(tmp_path, monkeypatch):
    """IE-010: 凭证空时 transcribe 入口立即抛 RuntimeError，不进 SDK 调用."""
    from app.modules.interview_eval import tencent_asr
    from app.config import settings
    # 覆盖 fixture 设的 test 凭证，模拟开发期空配置
    monkeypatch.setattr(settings, "tencent_cloud_secret_id", "")
    monkeypatch.setattr(settings, "tencent_cloud_secret_key", "")
    mp4 = tmp_path / "x.mp4"; mp4.write_bytes(b"\x00")
    with pytest.raises(RuntimeError) as exc:
        tencent_asr.transcribe(str(mp4))
    assert "凭证未配置" in str(exc.value)
    assert "TENCENT_CLOUD_SECRET_ID" in str(exc.value)


def test_submit_task_oversize_mp4_rejected(tmp_path):
    """IE-006: 文件超过 5MB 上限时 _submit_task 抛 RuntimeError（防御性边界，
    现在 transcribe 已 extract_audio 压到 4.5MB 内，此校验作兜底）."""
    from app.modules.interview_eval import tencent_asr
    big_mp4 = tmp_path / "big.mp4"
    # 写 6MB 文件
    big_mp4.write_bytes(b"\x00" * (6 * 1024 * 1024))
    with pytest.raises(RuntimeError) as exc:
        tencent_asr._submit_task(MagicMock(), str(big_mp4))
    assert "5MB" in str(exc.value) or "上限" in str(exc.value)
    assert "COS" in str(exc.value) or "SourceType" in str(exc.value)


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
