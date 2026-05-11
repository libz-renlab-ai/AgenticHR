"""F-interview-eval Pydantic 请求/响应 + LLM 输出 schema."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator


# === 请求 ===
class StartJobRequest(BaseModel):
    interview_id: int


# === 内部：LLM 评分输出（严格校验，重试用） ===
class EvidenceSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker: Literal["interviewer", "candidate"]
    text: str

    @model_validator(mode="after")
    def _end_ge_start(self) -> "EvidenceSegment":
        # IE-009: end_ms 必须 ≥ start_ms（防 LLM 时间反序导致前端跳转错乱）
        if self.end_ms < self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) must be >= start_ms ({self.start_ms})")
        return self


class DimensionScore(BaseModel):
    name: str
    score: int = Field(ge=1, le=10)
    reasoning: str = Field(max_length=400)
    # IE-026: 放宽到 5（prompts.py 没明确限制证据数量，LLM 偶尔输出 4-5 个，
    # 配合 IE-014 修复后会被判永久错误一次失败而非 retry，需要给 LLM 余量）
    evidence: list[EvidenceSegment] = Field(min_length=1, max_length=5)


class ScorecardOutput(BaseModel):
    """LLM 必须严格输出此结构."""
    dimensions: list[DimensionScore] = Field(min_length=1)
    hire_recommendation: Literal["strong_hire", "hire", "hold", "no_hire"]
    strengths: list[str] = Field(max_length=5)
    risks: list[str] = Field(max_length=5)
    followups: list[str] = Field(max_length=5)


# === 响应 ===
class JobResponse(BaseModel):
    id: int
    interview_id: int
    status: str
    error_msg: str = ""
    created_at: str
    recording_path: str = ""
    duration_sec: int = 0


class ScorecardResponse(BaseModel):
    job_id: int
    interview_id: int
    dimensions: list[DimensionScore]
    hire_recommendation: str
    strengths: list[str]
    risks: list[str]
    followups: list[str]
    transcript_available: bool
    recording_available: bool
    llm_model: str
    prompt_version: str
    created_at: str
