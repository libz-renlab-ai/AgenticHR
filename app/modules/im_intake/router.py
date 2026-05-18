"""F4 Boss IM Intake HTTP API (extension-driven; no backend Playwright daemon)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit.logger import log_event
from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.outbox_model import IntakeOutbox
from app.modules.im_intake.outbox_service import (
    ack_failed as _outbox_ack_failed,
    ack_sent as _outbox_ack_sent,
    claim_batch as _outbox_claim_batch,
    expire_pending_for_candidate as _outbox_expire_pending,
)
from app.modules.im_intake.settings_service import (
    get_or_create as _settings_get_or_create,
    update as _settings_update,
    complete_count as _settings_complete_count,
    is_running as _settings_is_running,
)
from app.modules.im_intake.schemas import (
    AckSentIn,
    AutoScanTickIn,
    CandidateDetailOut,
    CandidateOut,
    CollectChatIn,
    CollectChatOut,
    IntakeSettingsIn,
    IntakeSettingsOut,
    NextActionOut,
    OutboxAckIn,
    OutboxClaimIn,
    OutboxClaimItem,
    OutboxClaimOut,
    RegisterCandidateIn,
    SlotOut,
    SlotPatchIn,
    StartConversationOut,
)
from urllib.parse import quote as _url_quote
from app.modules.im_intake.promote import promote_to_resume
from app.modules.im_intake.service import IntakeService
from app.modules.im_intake.templates import HARD_SLOT_KEYS
from app.core.audit.models import AuditEvent
from app.modules.screening.models import Job

router = APIRouter(prefix="/api/intake", tags=["intake"])

import logging as _logging
_log = _logging.getLogger(__name__)


def _is_valid_pdf_url(url: str | None) -> bool:
    """Reject extension-side title fallback values like '简历.pdf' that crash
    /api/resumes/{id}/pdf with 404. Accept either:
      - http(s)://… URL
      - absolute local path under settings.resume_storage_path that actually exists
    """
    if not url:
        return False
    if url.startswith(("http://", "https://")):
        return True
    try:
        from pathlib import Path as _P
        p = _P(url).resolve()
        storage_root = _P(settings.resume_storage_path).resolve()
        return str(p).startswith(str(storage_root)) and p.exists()
    except (OSError, ValueError):
        return False


def _audit_safe(f_stage: str, action: str, entity_id: int, payload: dict | None = None,
                reviewer_id: int | None = None) -> None:
    try:
        log_event(
            f_stage=f_stage, action=action, entity_type="intake_candidate",
            entity_id=entity_id, input_payload=payload, reviewer_id=reviewer_id,
        )
    except Exception as e:
        _log.warning("audit log_event %s failed: %s", f_stage, e)


def _build_service(db: Session, user_id: int = 0) -> IntakeService:
    """Late import to avoid circular import with app.main."""
    from app import main as _main
    return IntakeService(
        db=db,
        llm=getattr(_main, "llm_client", None),
        hard_max_asks=getattr(settings, "f4_hard_max_asks", 3),
        pdf_timeout_hours=getattr(settings, "f4_pdf_timeout_hours", 72),
        ask_cooldown_hours=getattr(settings, "f4_ask_cooldown_hours", 6),
        soft_max_n=getattr(settings, "f4_soft_question_max", 3),
        user_id=user_id,
    )


def _candidate_summary(c: IntakeCandidate, slots: list[IntakeSlot], job_title: str = "") -> CandidateOut:
    expected = list(HARD_SLOT_KEYS) + ["pdf"]
    soft_keys = [s.slot_key for s in slots if s.slot_category == "soft"]
    expected += soft_keys
    done = sum(1 for s in slots if s.value)
    candidate_ts = c.updated_at or c.intake_started_at
    last = max((s.updated_at for s in slots if getattr(s, "updated_at", None)), default=candidate_ts)
    if candidate_ts and last and candidate_ts > last:
        last = candidate_ts
    return CandidateOut(
        resume_id=c.id,  # NOTE: field kept as resume_id for frontend compat; semantically = candidate_id
        boss_id=c.boss_id,
        name=c.name,
        job_id=getattr(c, "job_id", None),
        job_title=job_title,
        intake_status=c.intake_status,
        progress_done=done,
        progress_total=len(expected),
        last_activity_at=last,
        last_checked_at=getattr(c, "last_checked_at", None),
        promoted_resume_id=getattr(c, "promoted_resume_id", None),
    )


# Source of truth for intake_status is schemas.IntakeStatus; derive the
# filter enum from it so a new state added there can't silently break list().
from typing import get_args as _get_args
from app.modules.im_intake.schemas import IntakeStatus as _IntakeStatus
_INTAKE_STATUS_ENUM = set(_get_args(_IntakeStatus))
_RECRUIT_STATUS_ENUM = {"pending", "passed", "rejected"}


@router.get("/candidates")
def list_candidates(
    status: str | None = None,
    recruit_status: str | None = None,
    job_id: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # BUG-122: recruit_status 必须是合法 enum, 防 typo (e.g. accepted) 静默返空
    if status and status not in _INTAKE_STATUS_ENUM:
        raise HTTPException(status_code=400, detail=f"status 必须是 {sorted(_INTAKE_STATUS_ENUM)} 之一")
    if recruit_status and recruit_status not in _RECRUIT_STATUS_ENUM:
        raise HTTPException(status_code=400, detail=f"recruit_status 必须是 {sorted(_RECRUIT_STATUS_ENUM)} 之一")
    q = db.query(IntakeCandidate).filter(IntakeCandidate.user_id == user_id)
    if status:
        # 历史语义: status filter intake_status (collecting/complete/abandoned/timed_out)
        q = q.filter(IntakeCandidate.intake_status == status)
    if recruit_status:
        # 录用状态 (passed/rejected/pending), spec 0429 阶段 A 引入
        q = q.filter(IntakeCandidate.status == recruit_status)
    if job_id:
        q = q.filter(IntakeCandidate.job_id == job_id)
    total = q.count()
    rows = q.order_by(IntakeCandidate.updated_at.desc()).offset((page - 1) * size).limit(size).all()
    items = []
    for c in rows:
        slots = db.query(IntakeSlot).filter_by(candidate_id=c.id).all()
        job = db.query(Job).filter_by(id=c.job_id).first() if getattr(c, "job_id", None) else None
        items.append(_candidate_summary(c, slots, job.title if job else ""))
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/candidates/register", status_code=201)
def register_candidate(
    body: RegisterCandidateIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Step1: 仅注册候选人身份（boss_id+name+job_title），不做LLM分析。幂等。"""
    svc = _build_service(db, user_id=user_id)
    c = svc.ensure_candidate(body.boss_id, name=body.name, job_intention=body.job_title)
    return {"candidate_id": c.id, "boss_id": c.boss_id, "status": c.intake_status}


@router.get("/candidates/{candidate_id}", response_model=CandidateDetailOut)
def get_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "not found")
    slots = db.query(IntakeSlot).filter_by(candidate_id=c.id).all()
    job = db.query(Job).filter_by(id=c.job_id).first() if getattr(c, "job_id", None) else None
    summary = _candidate_summary(c, slots, job.title if job else "")
    return CandidateDetailOut(
        **summary.model_dump(),
        slots=[SlotOut.model_validate(s, from_attributes=True) for s in slots],
    )


@router.put("/slots/{slot_id}", response_model=SlotOut)
def patch_slot(
    slot_id: int,
    body: SlotPatchIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    s = db.query(IntakeSlot).filter_by(id=slot_id).first()
    if not s:
        raise HTTPException(404, "slot not found")
    # Verify the parent candidate belongs to the calling user
    parent = db.query(IntakeCandidate).filter_by(id=s.candidate_id, user_id=user_id).first()
    if not parent:
        raise HTTPException(404, "slot not found")
    # Reject patches against permanently-terminal candidates — editing a slot
    # on a completed or abandoned candidate creates inconsistent data (the
    # resume row is a snapshot of the slot state at promotion time).
    # NOTE: ``pending_human`` is intentionally excluded — that state exists
    # *for* manual intervention; locking it out would defeat its purpose.
    if parent.intake_status in ("complete", "abandoned"):
        raise HTTPException(409, f"candidate is {parent.intake_status}, slot is read-only")
    s.value = body.value
    s.source = "manual"
    s.answered_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    return SlotOut.model_validate(s, from_attributes=True)


@router.post("/candidates/{candidate_id}/abandon")
def abandon(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "not found")
    # Idempotent: already-abandoned candidates skip the state mutation but
    # still re-run the outbox expire (defense against historical zombies).
    now = datetime.now(timezone.utc)
    if c.intake_status != "abandoned":
        c.intake_status = "abandoned"
        c.intake_completed_at = now
        db.commit()
    expired = _outbox_expire_pending(db, c.id, reason="manual_abandon")
    _audit_safe("f4_abandoned", "manual_abandon", c.id,
                {"boss_id": c.boss_id, "outbox_expired": expired}, reviewer_id=user_id)
    return {"ok": True, "outbox_expired": expired}


@router.delete("/candidates/{candidate_id}", status_code=204)
def delete_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "not found")
    db.query(IntakeSlot).filter_by(candidate_id=c.id).delete(synchronize_session=False)
    db.delete(c)
    db.commit()


@router.post("/candidates/{candidate_id}/force-complete")
def force_complete(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "not found")
    resume = promote_to_resume(db, c, user_id=user_id)
    db.commit()
    _audit_safe(
        "f4_completed", "manual_complete", c.id,
        {"boss_id": c.boss_id, "promoted_resume_id": resume.id if resume else None},
        reviewer_id=user_id,
    )
    return {"ok": True, "promoted_resume_id": resume.id if resume else None}


@router.post("/candidates/{candidate_id}/mark-timed-out")
def mark_timed_out(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Step2: 候选人超过最大问询次数无回应，标记超时未回复。"""
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "candidate not found")
    if c.intake_status in ("complete", "abandoned", "timed_out"):
        return {"ok": True, "noop": True, "status": c.intake_status}
    c.intake_status = "timed_out"
    c.intake_completed_at = datetime.now(timezone.utc)
    db.commit()
    _outbox_expire_pending(db, c.id, reason="timed_out")
    _audit_safe("f4_timed_out", "manual_timed_out", c.id, {}, reviewer_id=user_id)
    return {"ok": True, "status": "timed_out"}


@router.post("/candidates/{candidate_id}/unarchive")
def unarchive_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """2026-05-18: 手动反归档。把 timed_out 候选人放回 awaiting_reply, 重置
    intake_started_at 给 7 天宽限期 (作为 staleness 判定的 fallback 时间锚)。

    仅 timed_out 状态可反归档 — complete / abandoned / pending_human 各有其
    专门的恢复路径或不应被简单恢复。
    """
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "candidate not found")
    if c.intake_status != "timed_out":
        raise HTTPException(
            400,
            f"only timed_out can be unarchived, current status: {c.intake_status}",
        )
    now = datetime.now(timezone.utc)
    old_reject = c.reject_reason
    c.intake_status = "awaiting_reply"
    c.intake_started_at = now  # 关键: 重置时间锚, 给 7 天宽限
    c.intake_completed_at = None
    c.reject_reason = ""
    c.last_checked_at = now
    db.commit()
    _audit_safe(
        "f4_unarchived", "manual_unarchive", c.id,
        {"from_reject_reason": old_reject},
        reviewer_id=user_id,
    )
    return {
        "ok": True,
        "status": c.intake_status,
        "intake_started_at": c.intake_started_at.isoformat(),
    }


_MANUAL_ALLOWED_STATUSES = frozenset(
    ["collecting", "awaiting_reply", "pending_human", "complete", "abandoned", "timed_out"]
)
_TERMINAL_STATUSES = frozenset(["complete", "abandoned", "pending_human", "timed_out"])


@router.patch("/candidates/{candidate_id}/status")
def update_status(
    candidate_id: int,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """HR 手动调整候选人状态；终态同步记录 intake_completed_at。"""
    new_status = (body.get("status") or "").strip()
    if new_status not in _MANUAL_ALLOWED_STATUSES:
        raise HTTPException(400, f"invalid status: {new_status!r}")
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "not found")
    old_status = c.intake_status
    now = datetime.now(timezone.utc)
    c.intake_status = new_status
    c.last_checked_at = now
    if new_status in _TERMINAL_STATUSES:
        c.intake_completed_at = now
        _outbox_expire_pending(db, c.id, reason=f"manual_status_{new_status}")
        if new_status == "complete" and not c.promoted_resume_id:
            promote_to_resume(db, c, user_id=user_id)
    else:
        c.intake_completed_at = None
    db.commit()
    _audit_safe(
        "f4_status_changed", "manual_status", c.id,
        {"from": old_status, "to": new_status}, reviewer_id=user_id,
    )
    return {"ok": True, "status": new_status, "intake_completed_at": c.intake_completed_at}


@router.patch("/candidates/{candidate_id}/last-checked")
def update_last_checked(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Step2: 记录本次检查时间，下次比较用于判断有无新候选人消息。"""
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "candidate not found")
    c.last_checked_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "last_checked_at": c.last_checked_at.isoformat()}


@router.post("/collect-chat", response_model=CollectChatOut)
async def collect_chat(
    body: CollectChatIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    # 2026-05-18 入库前 stale 拦截: 新候选人 (DB 中无此 boss_id) 且本次 body.messages
    # 最后一条 > 7 天前 → 不创建 candidate, 直接返回 skipped_stale_new。
    # 已存在候选人走原 flow, 由 analyze_chat 内的归档逻辑兜底。
    from app.modules.im_intake.staleness import last_message_dt, is_stale, STALE_DAYS
    existing_candidate = (
        db.query(IntakeCandidate)
        .filter_by(user_id=user_id, boss_id=body.boss_id)
        .first()
    )
    if existing_candidate is None:
        msgs_dicts = [m.model_dump() for m in body.messages]
        first_seen_last_dt = last_message_dt({"messages": msgs_dicts}, fallback=None)
        if is_stale(first_seen_last_dt):
            _audit_safe(
                "f4_pre_ingest_reject", "stale_skip_create",
                entity_id=0,  # boss_id 还没对应 candidate.id
                payload={
                    "boss_id": body.boss_id,
                    "last_message_dt": first_seen_last_dt.isoformat() if first_seen_last_dt else None,
                    "stale_days_threshold": STALE_DAYS,
                },
                reviewer_id=user_id,
            )
            return CollectChatOut(
                candidate_id=None,
                intake_status="skipped_stale_new",
                next_action=NextActionOut(
                    type="skipped_stale_new",
                    text=f"候选人最后聊天 > {STALE_DAYS} 天, 跳过入库",
                    slot_keys=[],
                ),
            )

    svc = _build_service(db, user_id=user_id)
    c = svc.ensure_candidate(body.boss_id, name=body.name, job_intention=body.job_intention)
    job = db.query(Job).filter_by(id=c.job_id).first() if c.job_id else None

    # BUG-050: terminal-state guard — re-running LLM analysis on already
    # complete/abandoned/timed_out/pending_human candidates wastes budget and
    # produces actions that contradict the persisted state. Return current
    # state with a no-op action so the extension can clean up its UI.
    if c.intake_status in _TERMINAL_STATUSES:
        db.refresh(c)
        return CollectChatOut(
            candidate_id=c.id,
            intake_status=c.intake_status,
            next_action=NextActionOut(type="wait_reply", text="", slot_keys=[]),
        )

    # Clamp message list — extension might be looping or user pasted a giant
    # transcript. Persisting 50k messages into chat_snapshot bloats the row,
    # slows extraction, and feeds noise to the LLM. Keep the most recent N.
    max_msgs = getattr(settings, "f4_max_chat_messages", 500)
    raw = list(body.messages)
    if len(raw) > max_msgs:
        raw = raw[-max_msgs:]
    messages = [m.model_dump() for m in raw]

    if body.pdf_present and body.pdf_url and not c.pdf_path:
        if _is_valid_pdf_url(body.pdf_url):
            slots = svc.ensure_slot_rows(c.id)
            slots["pdf"].value = body.pdf_url
            slots["pdf"].source = "plugin_detected"
            slots["pdf"].answered_at = datetime.now(timezone.utc)
            c.pdf_path = body.pdf_url
            db.commit()
            _audit_safe("f4_pdf_received", "pdf_uploaded", c.id,
                        {"pdf_url": body.pdf_url}, reviewer_id=user_id)
        else:
            # BUG-A2: extension may fall back to card-title text ("简历.pdf") when
            # downloadPdf fails. Persisting that as pdf_path causes /resumes/{id}/pdf
            # to 404. Reject and let the candidate stay in collecting so the system
            # re-issues request_pdf.
            _audit_safe("f4_pdf_invalid_path", "rejected", c.id,
                        {"received": body.pdf_url}, reviewer_id=user_id)
        # PR3: 接到本地 PDF 后同步抽基础字段；AI 解析在后台异步进行（避免阻塞响应）
        try:
            from app.modules.im_intake.intake_pdf_parser import extract_basic_fields
            if extract_basic_fields(c, db):
                db.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "intake basic field extract failed: candidate=%s err=%s", c.id, e
            )

    action = await svc.analyze_chat(c, messages, job)
    svc.apply_terminal(c, action, user_id=user_id)
    # 非终态动作：内联生成 outbox（替代已禁用的后台 scheduler）
    # skip_outbox=True 时（如 Step2 inline 发送）不生成 outbox，防止 outbox alarm 重复发送
    from app.modules.im_intake.outbox_service import generate_for_candidate as _gen_outbox
    if not body.skip_outbox and action.type in ("send_hard", "request_pdf", "send_soft"):
        _gen_outbox(db, c, action)
    db.refresh(c)
    return CollectChatOut(
        candidate_id=c.id,
        intake_status=c.intake_status,
        next_action=NextActionOut(
            type=action.type,
            text=action.text,
            slot_keys=action.meta.get("slot_keys", []),
        ),
    )


@router.post("/candidates/{candidate_id}/reextract")
async def reextract_slots(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """对存量 chat_snapshot 重跑 SlotFiller, 修复历史漏抽 slot."""
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "candidate not found")

    msgs = []
    if isinstance(c.chat_snapshot, dict):
        msgs = c.chat_snapshot.get("messages") or []
    if not msgs:
        return {"id": c.id, "filled": [], "skipped": "no_messages"}

    # 顺序: all_hard_filled (无活可干, 不需 LLM) > LLM 配置检查 > 真跑。
    # 旧实现先检 LLM 后检 all_filled, 导致已齐用户在 LLM 未配置时被错误 503。
    slots = {s.slot_key: s for s in db.query(IntakeSlot).filter_by(candidate_id=c.id).all()}
    pending = [k for k in HARD_SLOT_KEYS if k in slots and not slots[k].value]
    if not pending:
        return {"id": c.id, "filled": [], "skipped": "all_hard_filled"}

    from app.modules.im_intake.slot_filler import SlotFiller
    from app import main as _main
    llm = getattr(_main, "llm_client", None)
    if llm is None:
        raise HTTPException(503, "LLM not configured")

    filler = SlotFiller(llm=llm)
    parsed = await filler.parse_conversation(msgs, c.boss_id, pending)

    now = datetime.now(timezone.utc)
    filled = []
    for key, (val, source) in parsed.items():
        s = slots[key]
        s.value = val if isinstance(val, str) else str(val)
        s.source = source
        s.answered_at = now
        filled.append(key)
    if filled:
        db.commit()
        _audit_safe("f4_reextract", "slot_fill", c.id,
                    {"filled": filled}, reviewer_id=user_id)
    return {"id": c.id, "filled": filled, "pending": pending}


@router.post("/candidates/{candidate_id}/ack-sent")
async def ack_sent(
    candidate_id: int,
    body: AckSentIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    if not body.delivered:
        return {"ok": True, "noop": True}
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "candidate not found")
    # Always expire the outbox row first — message already sent inline; must
    # prevent a duplicate dispatch by the 30s outbox poll regardless of state.
    expired = _outbox_expire_pending(db, c.id, reason="inline_ack_sent")
    svc = _build_service(db, user_id=user_id)
    job = db.query(Job).filter_by(id=c.job_id).first() if c.job_id else None
    action = await svc.analyze_chat(c, messages=[], job=job)
    if action.type != body.action_type:
        # BUG-052: state drifted between collect-chat and ack. Previously we
        # returned 200 with state_drift=True which advanced the candidate
        # opaquely and let the extension keep going. Reject 409 so the
        # extension re-pulls fresh state via collect-chat instead.
        _audit_safe(
            "f4_ack_drift", "state_drift_reject", c.id,
            {"client_action": body.action_type, "server_action": action.type},
            reviewer_id=user_id,
        )
        raise HTTPException(409, detail={
            "error": "state_drift",
            "client_action_type": body.action_type,
            "server_action_type": action.type,
            "outbox_expired": expired,
        })
    svc.record_asked(c, action)
    return {"ok": True, "outbox_expired": expired}


@router.get("/daily-cap")
def get_daily_cap(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Today's new-candidate usage vs. configured daily cap."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    used = (
        db.query(func.count(IntakeCandidate.id))
        .filter(IntakeCandidate.user_id == user_id)
        .filter(IntakeCandidate.created_at >= today_start)
        .scalar() or 0
    )
    cap = getattr(settings, "f4_daily_cap", 200)
    return {"date": today_start.date().isoformat(), "used": int(used), "cap": int(cap),
            "remaining": max(0, int(cap) - int(used))}


# ---- F5 Task 6: settings HTTP API ----

def _settings_response(db: Session, user_id: int) -> IntakeSettingsOut:
    s = _settings_get_or_create(db, user_id)
    return IntakeSettingsOut(
        enabled=s.enabled,
        target_count=s.target_count,
        complete_count=_settings_complete_count(db, user_id),
        is_running=_settings_is_running(db, user_id),
    )


@router.get("/settings", response_model=IntakeSettingsOut)
def get_intake_settings(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    return _settings_response(db, user_id)


@router.put("/settings", response_model=IntakeSettingsOut)
def put_intake_settings(
    body: IntakeSettingsIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Update HR-facing master switch.

    Defense-in-depth: when the new settings make ``is_running`` False
    (paused, or target_count lowered below complete_count), expire all
    pending+claimed outbox rows for this user. Without this, dormant rows
    sit until the user re-enables the intake — at which point a 2-day-old
    "ask arrival_date" question can suddenly fire against a candidate who
    has long since answered manually or been promoted by another flow.
    """
    was_running = _settings_is_running(db, user_id)
    _settings_update(db, user_id,
                     enabled=body.enabled,
                     target_count=body.target_count)
    is_now_running = _settings_is_running(db, user_id)
    if was_running and not is_now_running:
        # Bulk-expire user's live outbox to prevent stale replay on resume.
        rows = (db.query(IntakeOutbox)
                .filter(IntakeOutbox.user_id == user_id)
                .filter(IntakeOutbox.status.in_(("pending", "claimed")))
                .all())
        for r in rows:
            r.status = "expired"
            r.last_error = ((r.last_error or "")
                            + "[expired: intake paused/target reached]")[:2000]
        if rows:
            db.commit()
    return _settings_response(db, user_id)


@router.get("/autoscan/rank")
def autoscan_rank(
    limit: int = Query(10, ge=1, le=9999),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Rank candidates most in need of an autoscan tick.

    Strategy: collecting first then awaiting_reply, oldest updated_at first.
    """
    if not _settings_is_running(db, user_id):
        return {"items": [], "limit": limit}
    # BUG-B2: demote candidates whose chat_snapshot is non-empty but at least
    # one hard slot is still empty after we already asked. Those are extractor
    # blind spots — re-picking them ahead of fresh candidates fuels the
    # "ask → answer → can't extract → ask again" loop. Sort them to the back.
    from sqlalchemy import select, and_
    blind_hard_count_subq = (
        select(func.count(IntakeSlot.slot_key))
        .where(IntakeSlot.candidate_id == IntakeCandidate.id)
        .where(IntakeSlot.slot_key.in_(list(HARD_SLOT_KEYS)))
        .where(IntakeSlot.value == "")
        .where(IntakeSlot.ask_count > 0)
        .correlate(IntakeCandidate)
        .scalar_subquery()
    )
    rows = (
        db.query(IntakeCandidate)
        .filter(IntakeCandidate.user_id == user_id)
        .filter(IntakeCandidate.intake_status.in_(["collecting", "awaiting_reply"]))
        .order_by(
            # collecting (0) before awaiting_reply (1)
            case((IntakeCandidate.intake_status == "collecting", 0), else_=1),
            # blind-extract suspects (chat snapshot exists + asked at least once
            # but slot still empty) → demote to back of queue
            case(
                (and_(IntakeCandidate.chat_snapshot.isnot(None),
                      blind_hard_count_subq > 0), 1),
                else_=0,
            ),
            IntakeCandidate.updated_at.asc(),
        )
        .limit(limit)
        .all()
    )
    items = [
        {"candidate_id": c.id, "boss_id": c.boss_id, "name": c.name,
         "intake_status": c.intake_status,
         "last_activity_at": c.updated_at.isoformat() if c.updated_at else None}
        for c in rows
    ]
    return {"items": items, "limit": limit}


@router.post("/autoscan/tick")
def autoscan_tick(
    body: AutoScanTickIn = Body(default_factory=AutoScanTickIn),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Plugin reports tick results; backend writes F4_autoscan_tick audit + returns day stats."""
    # BUG-045 / BUG-051: typed schema rejects non-numeric / null at validation
    # layer; downstream code can rely on int values without int() conversion crash.
    processed = body.processed
    skipped = body.skipped
    total_seen = body.total
    # BUG-017: entity_id 从硬编码 0 改为 user_id，使审计记录可区分不同用户的 tick
    _audit_safe(
        "f4_autoscan_tick", "tick", user_id,
        {"processed": processed, "skipped": skipped, "total_seen": total_seen,
         "ts": body.ts},
        reviewer_id=user_id,
    )
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    tick_count = (
        db.query(func.count(AuditEvent.event_id))
        .filter(AuditEvent.f_stage == "f4_autoscan_tick")
        .filter(AuditEvent.reviewer_id == user_id)
        .filter(AuditEvent.created_at >= today_start)
        .scalar() or 0
    )
    return {"ok": True, "ticks_today": int(tick_count),
            "processed": processed, "skipped": skipped, "total_seen": total_seen}


@router.post("/candidates/{candidate_id}/start-conversation", response_model=StartConversationOut)
def start_conversation(
    candidate_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    c = db.query(IntakeCandidate).filter_by(id=candidate_id, user_id=user_id).first()
    if not c:
        raise HTTPException(404, "candidate not found")
    # BUG-046: URL-encode boss_id so attacker-controlled '&'/'?'/'#' cannot
    # inject extra query params into the deep link.
    safe_boss_id = _url_quote(c.boss_id or "", safe="")
    base = settings.boss_chat_url_template.format(boss_id=safe_boss_id)
    sep = "&" if "?" in base else "?"
    deep_link = f"{base}{sep}intake_candidate_id={c.id}"
    return StartConversationOut(candidate_id=c.id, boss_id=c.boss_id, deep_link=deep_link)


# ---- F4 Task 9: outbox HTTP API ----

@router.post("/outbox/claim", response_model=OutboxClaimOut)
def outbox_claim(
    body: OutboxClaimIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    if not _settings_is_running(db, user_id):
        return OutboxClaimOut(items=[])
    rows = _outbox_claim_batch(db, user_id=user_id, limit=body.limit)
    cand_ids = {r.candidate_id for r in rows}
    boss_by_cand: dict[int, str] = {}
    if cand_ids:
        boss_by_cand = dict(
            db.query(IntakeCandidate.id, IntakeCandidate.boss_id)
            .filter(IntakeCandidate.id.in_(cand_ids)).all()
        )
    return OutboxClaimOut(items=[
        OutboxClaimItem(
            id=r.id, candidate_id=r.candidate_id,
            boss_id=boss_by_cand.get(r.candidate_id, ""),
            action_type=r.action_type,
            text=r.text or "", slot_keys=r.slot_keys or [], attempts=r.attempts,
        ) for r in rows
    ])


@router.post("/outbox/{outbox_id}/ack")
def outbox_ack(
    outbox_id: int,
    body: OutboxAckIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    row = db.query(IntakeOutbox).filter_by(id=outbox_id, user_id=user_id).first()
    if row is None:
        raise HTTPException(404, "outbox not found")
    if body.success:
        _outbox_ack_sent(db, outbox_id)
    else:
        _outbox_ack_failed(db, outbox_id, error=body.error)
    return {"ok": True}


@router.post("/candidates/batch-classify")
async def batch_classify_candidates(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """对当前 user 名下所有 job_id IS NULL 的候选人跑分类.

    串行执行 (LLM 调用相对慢, 但 124 个候选人量级可控). 返计数明细供前端展示.
    """
    from app.modules.im_intake.job_classifier import classify_candidate_to_job

    pending = (
        db.query(IntakeCandidate)
        .filter(IntakeCandidate.user_id == user_id, IntakeCandidate.job_id.is_(None))
        .all()
    )
    total = len(pending)
    exact_matched = 0
    llm_matched = 0
    no_match = 0
    errors = 0

    for c in pending:
        try:
            jid, reason = await classify_candidate_to_job(db, c, user_id=user_id)
            if jid is not None:
                if reason == "exact_match":
                    exact_matched += 1
                else:
                    llm_matched += 1
            else:
                no_match += 1
        except Exception as e:
            _log.warning("classify failed cid=%s: %s", c.id, e)
            errors += 1

    db.commit()
    return {
        "total": total,
        "exact_matched": exact_matched,
        "llm_matched": llm_matched,
        "no_match": no_match,
        "errors": errors,
    }
