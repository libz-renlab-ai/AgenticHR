"""mock 腾讯云 SDK；说话人映射启发式."""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _no_sleep_no_real_client(monkeypatch):
    """开发期凭证为空，credential.Credential('','') 会抛 InvalidCredential；
    + 测试不真等 POLL_INTERVAL_S 秒。统一在 fixture 处理：

    1. POLL_INTERVAL_S = 0 → time.sleep(0)，但仍走一次轮询路径
    2. _get_client → MagicMock，避免 SDK 校验空凭证
    """
    from app.modules.interview_eval import tencent_asr

    monkeypatch.setattr(tencent_asr, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(tencent_asr, "_get_client", lambda: MagicMock())
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
