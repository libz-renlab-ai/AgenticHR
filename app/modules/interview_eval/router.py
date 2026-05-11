"""F-interview-eval API 路由（8 endpoints）.

ServiceError → HTTPException 映射；多用户隔离透传 user_id。
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app.database import SessionLocal
from app.modules.auth.deps import get_current_user_id
from app.modules.interview_eval import service
from app.modules.interview_eval.models import InterviewEvalScorecard
from app.modules.interview_eval.schemas import StartJobRequest
from app.modules.interview_eval.service import ServiceError

router = APIRouter(prefix="/api/interview-eval", tags=["interview_eval"])


def _err_to_http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.code, detail=e.message)


@router.post("/start")
def start_job(req: StartJobRequest, user_id: int = Depends(get_current_user_id)):
    try:
        job_id = service.create_job(interview_id=req.interview_id, user_id=user_id)
    except ServiceError as e:
        raise _err_to_http(e)
    return {"job_id": job_id, "status": "pending"}


@router.get("/by-interview/{interview_id}")
def by_interview(interview_id: int, user_id: int = Depends(get_current_user_id)):
    """聚合：某场面试的最新一条 job。注意路由顺序：放在 /{job_id} 之前避免被吃。"""
    job = service.latest_job_for_interview(
        interview_id=interview_id, user_id=user_id
    )
    if job is None:
        return {"job": None}
    return {"job": {
        "id": job.id, "status": job.status, "error_msg": job.error_msg,
        "created_at": job.created_at.isoformat(),
    }}


@router.get("/by-resume/{resume_id}")
def by_resume(resume_id: int, user_id: int = Depends(get_current_user_id)):
    """聚合：候选人详情页用，多场面试 scorecard 列表。"""
    rows = service.scorecards_for_resume(resume_id=resume_id, user_id=user_id)
    return {"scorecards": rows}


@router.get("/{job_id}")
def get_job(job_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        job = service.get_job(job_id=job_id, user_id=user_id)
    except ServiceError as e:
        raise _err_to_http(e)
    return {
        "id": job.id, "interview_id": job.interview_id, "status": job.status,
        "error_msg": job.error_msg, "duration_sec": job.duration_sec,
        "created_at": job.created_at.isoformat(),
    }


@router.get("/{job_id}/scorecard")
def get_scorecard(job_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        job = service.get_job(job_id=job_id, user_id=user_id)  # 401/404 校验
    except ServiceError as e:
        raise _err_to_http(e)
    db = SessionLocal()
    try:
        sc = (
            db.query(InterviewEvalScorecard)
            .filter_by(job_id=job_id)
            .order_by(InterviewEvalScorecard.created_at.desc())
            .first()
        )
        if sc is None:
            raise HTTPException(404, "scorecard 尚未生成")
        # IE-025: 用 job.recording_path 字段而非硬编码相对路径，
        # 与 IE-013 retention 修复对齐（RECORDING_DIR 配置变化时一致）
        recording_path = job.recording_path or f"data/recordings/{job_id}.mp4"
        return {
            "job_id": sc.job_id, "interview_id": sc.interview_id,
            "dimensions": sc.dimensions_json,
            "hire_recommendation": sc.hire_recommendation,
            "strengths": sc.strengths, "risks": sc.risks, "followups": sc.followups,
            "transcript_available": os.path.exists(sc.transcript_path),
            "recording_available": os.path.exists(recording_path),
            "llm_model": sc.llm_model, "prompt_version": sc.prompt_version,
            "created_at": sc.created_at.isoformat(),
        }
    finally:
        db.close()


@router.get("/{job_id}/transcript")
def get_transcript(job_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        service.get_job(job_id=job_id, user_id=user_id)
    except ServiceError as e:
        raise _err_to_http(e)
    path = f"data/transcripts/{job_id}.json"
    if not os.path.exists(path):
        raise HTTPException(404, "转录稿已被清理或尚未生成")
    with open(path, encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


@router.get("/{job_id}/recording")
def get_recording(job_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        job = service.get_job(job_id=job_id, user_id=user_id)
    except ServiceError as e:
        raise _err_to_http(e)
    # IE-025: 同 get_scorecard，用 job.recording_path 字段优先
    path = job.recording_path or f"data/recordings/{job_id}.mp4"
    if not os.path.exists(path):
        raise HTTPException(404, "录像已被清理或尚未下载完成")
    return FileResponse(path, media_type="video/mp4")


@router.post("/{job_id}/cancel")
def cancel(job_id: int, user_id: int = Depends(get_current_user_id)):
    try:
        service.cancel_job(job_id=job_id, user_id=user_id)
    except ServiceError as e:
        raise _err_to_http(e)
    return {"job_id": job_id, "cancel_requested": True}
