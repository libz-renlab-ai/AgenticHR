"""F-interview-eval：腾讯云 ASR 录音文件识别（极速版）.

定价：1 元/小时；自带说话人分离（SpeakerId）+ 句级时间戳。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any

from tencentcloud.asr.v20190614 import asr_client, models
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from app.config import settings

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 5
POLL_MAX_ATTEMPTS = 120  # 最多等 10min
MAX_BASE64_INPUT_BYTES = 5 * 1024 * 1024  # IE-006: 腾讯云 ASR SourceType=1 单次请求 ≤5MB


def _get_client():
    cred = credential.Credential(
        settings.tencent_cloud_secret_id, settings.tencent_cloud_secret_key,
    )
    httpProfile = HttpProfile(); httpProfile.endpoint = "asr.tencentcloudapi.com"
    cp = ClientProfile(); cp.httpProfile = httpProfile
    return asr_client.AsrClient(cred, settings.tencent_cloud_asr_region, cp)


def _submit_task(client, mp4_path: str) -> dict[str, Any]:
    """提交识别任务，返回 {Data: {TaskId: int}}."""
    import os as _os
    # IE-006: 检查 mp4 大小，超过 5MB 时 base64 路径不可用
    raw_size = _os.path.getsize(mp4_path)
    if raw_size > MAX_BASE64_INPUT_BYTES:
        raise RuntimeError(
            f"录像 {raw_size // (1024**2)}MB 超过腾讯云 ASR base64 上限 "
            f"{MAX_BASE64_INPUT_BYTES // (1024**2)}MB（SourceType=1）。"
            "需先上传到 COS 改 SourceType=0（待生产灰度后实施）"
        )
    with open(mp4_path, "rb") as f:
        data_b64 = base64.b64encode(f.read()).decode("ascii")
    req = models.CreateRecTaskRequest()
    req.from_json_string(json.dumps({
        "EngineModelType": "16k_zh_large",  # 中文大模型
        "ChannelNum": 1,
        "ResTextFormat": 2,                  # 详细 + 词级时间戳
        "SourceType": 1,                     # 1 = 上传 base64
        "Data": data_b64,
        "DataLen": len(data_b64),
        "SpeakerDiarization": 1,            # 启用说话人分离
        "SpeakerNumber": 0,                  # 0 = 自动判断
    }))
    resp = client.CreateRecTask(req)
    return json.loads(resp.to_json_string())


def _query_task(client, task_id: int) -> dict[str, Any]:
    req = models.DescribeTaskStatusRequest()
    req.from_json_string(json.dumps({"TaskId": task_id}))
    resp = client.DescribeTaskStatus(req)
    return json.loads(resp.to_json_string())


def _map_speakers(detail: list[dict]) -> dict[int, str]:
    """启发式：按发言时长，发言少的 SpeakerId → interviewer，多的 → candidate.

    只有一个 SpeakerId → 全部 candidate（保守，UI 上可手改）.
    """
    durations: dict[int, int] = defaultdict(int)
    for seg in detail:
        sid = seg.get("SpeakerId", 0)
        durations[sid] += int(seg.get("EndMs", 0)) - int(seg.get("StartMs", 0))
    if len(durations) <= 1:
        return {sid: "candidate" for sid in durations}
    sorted_sids = sorted(durations.items(), key=lambda kv: kv[1])
    interviewer_sid = sorted_sids[0][0]  # 发言最少
    return {
        sid: ("interviewer" if sid == interviewer_sid else "candidate")
        for sid in durations
    }


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
