from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


SlotKey = str
SlotCategory = Literal["hard", "pdf", "soft"]
IntakeStatus = Literal["collecting", "awaiting_reply", "pending_human", "complete", "abandoned", "timed_out"]


class SlotOut(BaseModel):
    id: int
    slot_key: SlotKey
    slot_category: SlotCategory
    value: str | None = None
    ask_count: int = 0
    asked_at: datetime | None = None
    answered_at: datetime | None = None
    msg_sent_at: datetime | None = None
    phrase_timestamps: list | None = None
    last_ask_text: str | None = None
    source: str | None = None
    question_meta: dict | None = None


class CandidateOut(BaseModel):
    resume_id: int
    boss_id: str
    name: str
    job_id: int | None = None
    job_title: str = ""
    intake_status: IntakeStatus
    progress_done: int
    progress_total: int
    last_activity_at: datetime | None = None
    last_checked_at: datetime | None = None
    promoted_resume_id: int | None = None


class CandidateDetailOut(CandidateOut):
    slots: list[SlotOut]


class SlotPatchIn(BaseModel):
    value: str = Field(min_length=1)


# ---- F3.1 additions ----

class ChatMessageIn(BaseModel):
    sender_id: str
    content: str
    sent_at: str | None = None


def _validate_boss_id(v: str) -> str:
    """BUG-043 / BUG-048: strip + reject all-whitespace, enforce max length."""
    s = (v or "").strip()
    if not s:
        raise ValueError("boss_id must not be empty or whitespace-only")
    if len(s) > 64:
        raise ValueError("boss_id exceeds 64 characters")
    return s


def _validate_pdf_url(v: str | None) -> str | None:
    """BUG-044 / BUG-032 / BUG-038: reject path-traversal strings.

    Accept: http(s):// URLs, or simple filename / relative path under storage
    (no '..', no leading '/' or '\\', no null byte). Backslashes inside the
    path are normalized to forward slashes so Windows-style paths produced by
    ``pathlib.Path`` on the server are accepted (the storage layer also
    re-resolves the path against ``settings.resume_storage_path`` at read
    time).
    """
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    if "\x00" in s:
        raise ValueError("pdf_url contains null byte")
    low = s.lower()
    if low.startswith(("http://", "https://")):
        return s
    # Normalize Windows separators before traversal checks so a legitimate
    # `data\resumes\foo.pdf` is treated identically to `data/resumes/foo.pdf`.
    s = s.replace("\\", "/")
    if ".." in s or s.startswith("/"):
        raise ValueError("pdf_url must be http(s) URL or safe relative path")
    if len(s) > 512:
        raise ValueError("pdf_url exceeds 512 characters")
    return s


class RegisterCandidateIn(BaseModel):
    boss_id: str = Field(min_length=1, max_length=64)
    name: str = ""
    job_title: str | None = None

    @field_validator("boss_id")
    @classmethod
    def _v_boss_id(cls, v: str) -> str:
        return _validate_boss_id(v)


class CollectChatIn(BaseModel):
    boss_id: str = Field(min_length=1, max_length=64)
    name: str = ""
    job_intention: str | None = None
    messages: list[ChatMessageIn] = Field(default_factory=list)
    pdf_present: bool = False
    pdf_url: str | None = None
    skip_outbox: bool = False

    @field_validator("boss_id")
    @classmethod
    def _v_boss_id(cls, v: str) -> str:
        return _validate_boss_id(v)

    @field_validator("pdf_url")
    @classmethod
    def _v_pdf_url(cls, v: str | None) -> str | None:
        return _validate_pdf_url(v)

    @model_validator(mode="after")
    def _v_pdf_consistency(self):
        # BUG-053: pdf_present=True implies pdf_url required; pdf_url alone
        # without pdf_present silently ignored downstream — reject the
        # contradictory combination so client knows.
        if self.pdf_present and not self.pdf_url:
            raise ValueError("pdf_present=True requires pdf_url")
        return self


class NextActionOut(BaseModel):
    # BUG-013: 移除 timed_out —— decide_next_action 从不产生该动作，schema 与实现不一致
    # 2026-05-18: 新增 archived_stale (已入库 7d 无消息归档) /
    # skipped_stale_new (入库前 stale 拦截)
    type: Literal["send_hard", "request_pdf", "wait_pdf", "wait_reply",
                  "send_soft", "complete", "mark_pending_human", "abandon",
                  "archived_stale", "skipped_stale_new"]
    text: str = ""
    slot_keys: list[str] = Field(default_factory=list)


class CollectChatOut(BaseModel):
    # candidate_id 可为 None: 当入库前拦截 (skipped_stale_new) 时, 不创建 candidate
    candidate_id: int | None = None
    intake_status: str
    next_action: NextActionOut


class AckSentIn(BaseModel):
    action_type: Literal["send_hard", "request_pdf", "send_soft"]
    delivered: bool = True


class StartConversationOut(BaseModel):
    candidate_id: int
    boss_id: str
    deep_link: str


# ---- F4 Task 9: outbox HTTP API schemas ----

class OutboxClaimIn(BaseModel):
    limit: int = Field(default=1, ge=1, le=1)  # hard capped; see outbox_service.claim_batch


class OutboxClaimItem(BaseModel):
    id: int
    candidate_id: int
    boss_id: str
    action_type: str
    text: str
    slot_keys: list = []
    attempts: int


class OutboxClaimOut(BaseModel):
    items: list[OutboxClaimItem]


class OutboxAckIn(BaseModel):
    success: bool
    error: str = ""


# ---- F5 Task 6: settings HTTP API schemas ----

class IntakeSettingsOut(BaseModel):
    enabled: bool
    target_count: int = Field(ge=0)
    complete_count: int = Field(ge=0)
    is_running: bool


class IntakeSettingsIn(BaseModel):
    enabled: bool | None = None
    target_count: int | None = Field(default=None, ge=0)


# ---- BUG-045 / BUG-051: typed body for autoscan/tick ----

class AutoScanTickIn(BaseModel):
    """Plugin → /autoscan/tick body. Reject non-numeric / null processed/skipped/total."""
    processed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    ts: str | None = None
