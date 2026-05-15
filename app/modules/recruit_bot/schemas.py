"""F3 recruit_bot 请求 / 响应 Pydantic schemas."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator

from app.modules.recruit_bot.education_check import EducationFilter


# 单 HR 单日打招呼上限绝对上界。业务侧默认 1000（`Settings.f3_default_daily_cap`），
# 但此处 schema 保护不让任何 PUT 写超过 DAILY_CAP_MAX 的值。
# 超过就让 HR 去联系运维加大总量阈值，而不是单点改 DB。
DAILY_CAP_MAX = 10000


class ScrapedCandidate(BaseModel):
    """Edge 扩展从 Boss 推荐列表 list card 抠出的字段.

    LIST-only 策略：spec §5.2. 不开 modal，字段全部来自 list 卡片可见区.
    """
    name: str = Field(..., min_length=1, max_length=100)
    boss_id: str = Field(..., min_length=1, max_length=100)
    age: int | None = None
    education: str = ""
    grad_year: int | None = None
    work_years: int = 0
    school: str = ""
    major: str = ""
    intended_job: str = ""
    skill_tags: list[str] = Field(default_factory=list)
    school_tier_tags: list[str] = Field(default_factory=list)
    ranking_tags: list[str] = Field(default_factory=list)
    expected_salary: str = ""
    active_status: str = ""
    recommendation_reason: str = ""
    latest_work_brief: str = ""
    raw_text: str = ""
    boss_current_job_title: str = ""


class RecruitEvaluateRequest(BaseModel):
    """F3 评估请求体。education_filter 必填；删旧 strategy 字段。"""
    job_id: int
    candidate: ScrapedCandidate
    education_filter: EducationFilter

    @model_validator(mode="after")
    def _require_tags_when_required(self) -> "RecruitEvaluateRequest":
        ef = self.education_filter
        if ef.require_prestigious and not ef.prestigious_tags:
            raise ValueError(
                "require_prestigious=True 时 prestigious_tags 不可为空"
            )
        return self


class RecruitDecision(BaseModel):
    """后端对单候选人的决策."""
    decision: Literal[
        "should_greet",
        "skipped_already_greeted",
        "rejected_low_education",
        "blocked_daily_cap",
    ]
    resume_id: int | None = None
    score: int | None = None
    threshold: int | None = None
    reason: str = ""


class GreetRecordRequest(BaseModel):
    """Edge 扩展点击"打招呼"按钮后回传动作结果。success=True 表示 Boss UI 的
    按钮状态确实变成"已打招呼"；False 表示点了但按钮没反应或异常，error_msg
    必填。"""
    resume_id: int
    success: bool
    error_msg: str = ""


class UsageInfo(BaseModel):
    """GET /api/recruit/daily-usage 的返回体。"""
    used: int
    cap: int
    remaining: int


class DailyCapUpdateRequest(BaseModel):
    """PUT /api/recruit/daily-cap 请求体。上限受 DAILY_CAP_MAX 硬约束。"""
    cap: int = Field(..., ge=0, le=DAILY_CAP_MAX)
