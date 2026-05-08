"""F-interview-eval Worker：4 步异步流水线 + cancel handle.

外部 IO 通过模块级函数 _download_recording / _transcribe / _score_with_llm /
_publish_feishu 表达；Task 5/6/7 用模块级 import 替换。tests 用 monkeypatch
注入 fakes，状态机本身可独立验证。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from app.database import SessionLocal
from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard
from app.modules.interview_eval.schemas import ScorecardOutput
from app.modules.scheduling.models import Interview
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)

RECORDING_DIR = "data/recordings"
TRANSCRIPT_DIR = "data/transcripts"
LLM_MAX_RETRY = 3

_HANDLE_LOCK = threading.Lock()
_ACTIVE_HANDLES: dict[int, threading.Event] = {}


# ---- 外部 IO（Task 5/6/7/8 替换成真实 import）----
def _download_recording(interview, dest_path: str) -> tuple[str, int, int]:
    """返回 (mp4_path, size_bytes, duration_sec). Task 5 替换."""
    raise NotImplementedError("Task 5 will inject tencent_meeting_recording.download")


def _transcribe(mp4_path: str) -> list[dict[str, Any]]:
    """返回 [{start_ms, end_ms, speaker, text}, ...]. Task 6 替换."""
    raise NotImplementedError("Task 6 will inject tencent_asr.transcribe")


def _score_with_llm(interview, transcript: list[dict]) -> dict:
    """返回 LLM 原始 dict（待 Pydantic 校验）. Task 7 替换."""
    raise NotImplementedError("Task 7 will inject prompts + ai_provider")


def _publish_feishu(interview, scorecard) -> None:
    """Task 8 替换."""
    raise NotImplementedError("Task 8 will inject feishu_push")


def _audit(action: str, **kwargs) -> None:
    """Task 8 替换为真实 audit_events 写入."""
    pass


# ---- cancel handle 注册 ----
def _register_handle(job_id: int) -> threading.Event:
    handle = threading.Event()
    with _HANDLE_LOCK:
        _ACTIVE_HANDLES[job_id] = handle
    return handle


def _unregister_handle(job_id: int) -> None:
    with _HANDLE_LOCK:
        _ACTIVE_HANDLES.pop(job_id, None)


def terminate_active(job_id: int) -> bool:
    """通知 worker 主动中断（只设标志，由 worker 主动检查）."""
    with _HANDLE_LOCK:
        h = _ACTIVE_HANDLES.get(job_id)
    if h is None:
        return False
    h.set()
    return True


# ---- 主流水线 ----
def _check_cancel(db, job_id: int) -> bool:
    job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
    if job is None:
        return True
    if job.cancel_requested:
        job.status = "cancelled"
        db.commit()
        _audit("cancel", entity_id=job_id)
        return True
    return False


def _set_status(db, job_id: int, status: str, **fields) -> None:
    db.query(InterviewEvalJob).filter_by(id=job_id).update(
        {"status": status, **fields}
    )
    db.commit()


def run(job_id: int) -> None:
    handle = _register_handle(job_id)
    db = SessionLocal()
    current_step = "init"
    try:
        os.makedirs(RECORDING_DIR, exist_ok=True)
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

        if _check_cancel(db, job_id):
            return

        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        if job is None:
            return
        interview = db.query(Interview).filter_by(id=job.interview_id).first()
        if interview is None:
            _set_status(db, job_id, "failed", error_msg="interview 不存在")
            return

        # ---- 1. download ----
        current_step = "download"
        _set_status(db, job_id, "downloading")
        if _check_cancel(db, job_id): return
        _audit("ieval_start", entity_id=job_id)
        dest = os.path.join(RECORDING_DIR, f"{job_id}.mp4")
        recording_path, size, duration = _download_recording(interview, dest)
        _set_status(db, job_id, "downloading",
                    recording_path=recording_path, recording_size=size,
                    duration_sec=duration)
        _audit("download_recording", entity_id=job_id, size=size, duration=duration)

        # ---- 2. transcribe ----
        current_step = "transcribe"
        _set_status(db, job_id, "transcribing")
        if _check_cancel(db, job_id): return
        transcript = _transcribe(recording_path)
        transcript_path = os.path.join(TRANSCRIPT_DIR, f"{job_id}.json")
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        _audit("asr_call", entity_id=job_id, segments=len(transcript))

        # ---- 3. score ----
        current_step = "score"
        _set_status(db, job_id, "scoring")
        if _check_cancel(db, job_id): return

        last_err = None
        scorecard_data: ScorecardOutput | None = None
        for attempt in range(LLM_MAX_RETRY):
            if _check_cancel(db, job_id): return
            try:
                raw = _score_with_llm(interview, transcript)
                scorecard_data = ScorecardOutput(**raw)
                break
            except Exception as e:
                last_err = e
                logger.warning("LLM scoring attempt %d failed: %s", attempt + 1, e)
        if scorecard_data is None:
            raise RuntimeError(
                f"LLM 输出 schema validation 失败 {LLM_MAX_RETRY} 次: {last_err}"
            )

        # 维度数量必须等于 competency_model.assessment_dimensions
        job_row = db.query(Job).filter_by(id=interview.job_id).first()
        expected_dims = (job_row.competency_model or {}).get(
            "assessment_dimensions", []
        ) if job_row else []
        if expected_dims and len(scorecard_data.dimensions) != len(expected_dims):
            raise RuntimeError(
                f"LLM 输出 dimensions 数量 {len(scorecard_data.dimensions)} "
                f"与 assessment_dimensions {len(expected_dims)} 不一致"
            )

        # 写 scorecard 行
        from app.config import settings
        sc = InterviewEvalScorecard(
            job_id=job_id, interview_id=interview.id,
            transcript_path=transcript_path,
            dimensions_json=[d.model_dump() for d in scorecard_data.dimensions],
            hire_recommendation=scorecard_data.hire_recommendation,
            strengths=scorecard_data.strengths,
            risks=scorecard_data.risks,
            followups=scorecard_data.followups,
            llm_model=settings.ai_model or "unknown",
            prompt_version=__import__(
                "app.modules.interview_eval.prompts", fromlist=["PROMPT_VERSION"]
            ).PROMPT_VERSION if _prompts_available() else "unknown",
        )
        db.add(sc); db.commit()
        _audit("llm_call", entity_id=job_id, model=settings.ai_model)

        # ---- 4. publish ----
        current_step = "publish"
        _set_status(db, job_id, "done", llm_model=sc.llm_model,
                    prompt_version=sc.prompt_version)
        _publish_feishu(interview, sc)
        _audit("publish", entity_id=job_id)

    except Exception as e:
        logger.exception("Worker failed at %s for job %d", current_step, job_id)
        _set_status(db, job_id, "failed", error_msg=f"[{current_step}] {e}")
        _audit(f"failed_at_{current_step}", entity_id=job_id, error=str(e))
    finally:
        db.close()
        _unregister_handle(job_id)


def _prompts_available() -> bool:
    try:
        import app.modules.interview_eval.prompts  # noqa: F401
        return True
    except ImportError:
        return False
