"""AI 智能筛选 REST API."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.ai_screening import service as svc
from app.modules.ai_screening import worker as wk
from app.modules.ai_screening.cli_runner import detect_claude_cli, resolve_claude_binary
from app.modules.ai_screening.schemas import (
    CurrentResponse,
    ItemResponse,
    ItemsListResponse,
    PreviewResponse,
    StartRequest,
    StartResponse,
)
from app.modules.ai_screening.service import ScreeningError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ai_screening"])


_ERR_TO_STATUS = {
    "job_not_found": 404,
    "not_found": 404,
    "invalid_mode": 400,
    "invalid_threshold": 400,
    "empty_pool": 422,
    "already_running": 409,
    "not_running": 400,
    "not_finished": 409,
}

_ERR_TO_MSG = {
    "job_not_found": "岗位不存在",
    "not_found": "记录不存在",
    "invalid_mode": "mode 必须为 count/ratio",
    "invalid_threshold": "threshold 不合法 (count: 1..池大小; ratio: 1..100)",
    "empty_pool": "候选池为空, 请先在 [匹配候选人] 跑硬筛",
    "already_running": "已有进行中的筛选任务",
    "not_running": "任务已结束, 无法取消",
    "not_finished": "任务尚未完成, 请等待跑完后再查看明细",
}


def _raise(e: ScreeningError):
    status = _ERR_TO_STATUS.get(e.code, 500)
    msg = _ERR_TO_MSG.get(e.code, e.code)
    raise HTTPException(status_code=status, detail=msg)


@router.get("/api/jobs/{job_id}/ai-screening/preview", response_model=PreviewResponse)
def preview(
    job_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        result = svc.preview(db, user_id=user_id, job_id=job_id)
    except ScreeningError as e:
        _raise(e)
    return result


@router.post("/api/jobs/{job_id}/ai-screening/start", response_model=StartResponse)
async def start(
    job_id: int,
    body: StartRequest,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    if not detect_claude_cli():
        raise HTTPException(
            status_code=503,
            detail="Claude Code CLI 未检测到, 请安装 (npm i -g @anthropic-ai/claude-code) 或设置 CLAUDE_CLI_PATH",
        )
    # BUG-102: 锁定 binary 绝对路径到 ScreeningJob.cli_path, worker 跑时不再 resolve
    cli_path = resolve_claude_binary()
    try:
        sj = svc.start(
            db,
            user_id=user_id,
            job_id=job_id,
            mode=body.mode,
            threshold=body.threshold,
            cli_path=cli_path,
        )
    except ScreeningError as e:
        _raise(e)

    # fire-and-forget worker
    wk.spawn(sj.id)
    # BUG-148: 把后端权威 total 返给前端, 防 UI 用 stale eligibleCount。
    return StartResponse(screening_job_id=sj.id, total=sj.total or 0)


@router.get(
    "/api/jobs/{job_id}/ai-screening/current",
    response_model=CurrentResponse,
)
def current(
    job_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        sj = svc.current(db, user_id=user_id, job_id=job_id)
    except ScreeningError as e:
        _raise(e)
    if not sj:
        return CurrentResponse(status="idle")
    return CurrentResponse(
        id=sj.id,
        status=sj.status,
        mode=sj.mode,
        threshold=sj.threshold,
        total=sj.total,
        processed=sj.processed,
        error_msg=sj.error_msg,
        started_at=sj.started_at,
        finished_at=sj.finished_at,
    )


@router.post("/api/ai-screening/{screening_job_id}/cancel")
def cancel(
    screening_job_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        sj, terminated = svc.cancel(db, user_id=user_id, screening_job_id=screening_job_id)
    except ScreeningError as e:
        _raise(e)
    # BUG-135: terminated=False 表示子进程未被立即杀掉 (handle 缺失或 terminate 抛错),
    # 让前端可以提示用户 "已请求取消, 但当前批次将自然结束 (≤5min)"。
    return {
        "id": sj.id,
        "cancel_requested": sj.cancel_requested,
        "terminated": terminated,
    }


@router.get(
    "/api/ai-screening/{screening_job_id}/items",
    response_model=ItemsListResponse,
)
def list_items(
    screening_job_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        sj, items = svc.list_items(
            db, user_id=user_id, screening_job_id=screening_job_id
        )
    except ScreeningError as e:
        _raise(e)
    return ItemsListResponse(
        items=[ItemResponse(**it) for it in items],
        threshold=sj.threshold,
        mode=sj.mode,
        total=sj.total,
    )
