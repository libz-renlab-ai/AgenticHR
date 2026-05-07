"""AI 智能筛选 pydantic schemas."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PreviewResponse(BaseModel):
    eligible_count: int
    has_running: bool


class StartRequest(BaseModel):
    mode: Literal["count", "ratio"]
    threshold: int = Field(..., ge=1)

    @field_validator("threshold")
    @classmethod
    def _ratio_max_100(cls, v, info):
        if info.data.get("mode") == "ratio" and v > 100:
            raise ValueError("ratio threshold must be 1..100")
        return v


class StartResponse(BaseModel):
    screening_job_id: int
    # BUG-148: 后端权威 total — 前端 onStart 不再用 stale 的本地 eligibleCount,
    # 直接用 backend 在 start 时锁定的候选池大小, 防止并发 promote 让 UI 显示错。
    total: int = 0


class CurrentResponse(BaseModel):
    id: Optional[int] = None
    status: str = "idle"  # idle 表示无任何任务
    mode: Optional[str] = None
    threshold: Optional[int] = None
    total: int = 0
    processed: int = 0
    error_msg: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ItemResponse(BaseModel):
    id: int
    candidate_id: int
    candidate_name: str
    score: Optional[int]
    reason: Optional[str]
    pass_flag: int
    error: Optional[str]
    decision_action: Optional[str] = None  # 现行决策表的状态


class ItemsListResponse(BaseModel):
    items: list[ItemResponse]
    threshold: int
    mode: str
    total: int
