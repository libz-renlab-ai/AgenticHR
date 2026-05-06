"""spec 0429-D — 岗位 × 候选人 人工决策 service。

set/clear/list 决策, 校验 job + candidate 归属, candidate × job 维度。
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from sqlalchemy.orm import Session

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.matching.decision_model import JobCandidateDecision
from app.modules.screening.models import Job


logger = logging.getLogger(__name__)

ALLOWED_ACTIONS = ("passed", "rejected")


def _audit_decision(
    user_id: int,
    job_id: int,
    candidate_id: int,
    prev_action: str | None,
    new_action: str | None,
) -> None:
    """spec 0429-D 收尾 P3-b: 写 worm 审计日志, 失败不阻断主流程。"""
    try:
        from app.core.audit.logger import log_event
        log_event(
            f_stage="0429-D",
            action="set" if new_action else "clear",
            entity_type="job_candidate_decision",
            entity_id=candidate_id,
            input_payload={
                "user_id": user_id, "job_id": job_id,
                "candidate_id": candidate_id,
                "prev_action": prev_action, "new_action": new_action,
            },
            reviewer_id=user_id,
        )
    except Exception as e:
        logger.warning(f"audit log for decision failed (non-fatal): {e}")


class DecisionError(Exception):
    """业务异常: not_found / invalid_action。code 字段供 router 转 HTTP 状态。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _check_job_owner(db: Session, job_id: int, user_id: int) -> Job:
    job = db.query(Job).filter_by(id=job_id, user_id=user_id).first()
    if not job:
        raise DecisionError("job_not_found")
    return job


def _check_candidate_owner(
    db: Session, candidate_id: int, user_id: int
) -> IntakeCandidate:
    cand = (
        db.query(IntakeCandidate)
        .filter_by(id=candidate_id, user_id=user_id)
        .first()
    )
    if not cand:
        raise DecisionError("candidate_not_found")
    return cand


def _warn_if_unrelated(
    db: Session, job_id: int, candidate_id: int
) -> None:
    """BUG-112 软校验: 决策写入时若 (job, candidate) 在 matching_results 与
    list_matched_for_job 母集都无关联, log warning 但不阻挡, 让审计可发现污染。
    """
    try:
        from app.modules.matching.models import MatchingResult
        cand = db.query(IntakeCandidate).filter_by(id=candidate_id).first()
        if not cand:
            return
        promoted_id = getattr(cand, "promoted_resume_id", None)
        if promoted_id is not None:
            mr = (
                db.query(MatchingResult)
                .filter_by(job_id=job_id, resume_id=promoted_id)
                .first()
            )
            if mr is not None:
                return
        # 候选人可能在 list_matched_for_job (四项齐全 + 学历门槛) 母集里, 仍属合法决策
        from app.modules.im_intake.models import IntakeSlot
        from app.modules.im_intake.templates import HARD_SLOT_KEYS
        filled = (
            db.query(IntakeSlot)
            .filter(IntakeSlot.candidate_id == candidate_id)
            .filter(IntakeSlot.slot_key.in_(HARD_SLOT_KEYS))
            .filter(IntakeSlot.value.isnot(None))
            .filter(IntakeSlot.value != "")
            .count()
        )
        if filled >= len(HARD_SLOT_KEYS) and (cand.pdf_path or "").strip():
            return
        logger.warning(
            "decision write for unrelated (job=%s, candidate=%s): "
            "no matching_results row and not in complete pool",
            job_id, candidate_id,
        )
    except Exception as e:
        logger.debug("warn_if_unrelated check skipped: %s", e)


def set_decision(
    db: Session,
    user_id: int,
    job_id: int,
    candidate_id: int,
    action: Optional[str],
    force: bool = True,
) -> Optional[JobCandidateDecision]:
    """设/改/清决策。

    action='passed'|'rejected' → upsert 行。
    action=None → 删除已有行 (无行也 OK, 幂等)。

    BUG-087: force=False 时, 已有 'rejected' 行不被自动 'passed' 覆盖
    (例如 AI 智能筛选 finalize, HR 主动操作仍走 force=True 默认)。

    raise DecisionError('job_not_found' | 'candidate_not_found' | 'invalid_action')
    """
    _check_job_owner(db, job_id, user_id)
    _check_candidate_owner(db, candidate_id, user_id)
    if action is not None:
        _warn_if_unrelated(db, job_id, candidate_id)

    existing = (
        db.query(JobCandidateDecision)
        .filter_by(job_id=job_id, candidate_id=candidate_id)
        .first()
    )

    prev_action = existing.action if existing else None

    if action is None:
        if existing:
            db.delete(existing)
            db.commit()
            _audit_decision(user_id, job_id, candidate_id, prev_action, None)
        return None

    if action not in ALLOWED_ACTIONS:
        raise DecisionError("invalid_action")

    if existing:
        # BUG-087: 非强制写入时, 不覆盖 HR 已显式 reject 的决策
        if not force and existing.action == "rejected" and action == "passed":
            logger.info(
                "skip overwrite rejected decision: job=%s cand=%s (force=False)",
                job_id, candidate_id,
            )
            return existing
        if existing.action == action:
            return existing
        existing.action = action
        db.commit()
        db.refresh(existing)
        _audit_decision(user_id, job_id, candidate_id, prev_action, action)
        return existing

    row = JobCandidateDecision(
        user_id=user_id,
        job_id=job_id,
        candidate_id=candidate_id,
        action=action,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _audit_decision(user_id, job_id, candidate_id, prev_action, action)
    return row


def get_decisions_map_for_job(
    db: Session, user_id: int, job_id: int
) -> dict[int, str]:
    """返 {candidate_id: action} for 该 job + user。"""
    rows = (
        db.query(JobCandidateDecision)
        .filter_by(user_id=user_id, job_id=job_id)
        .all()
    )
    return {r.candidate_id: r.action for r in rows}


def get_decision(
    db: Session, user_id: int, job_id: int, candidate_id: int
) -> Optional[JobCandidateDecision]:
    return (
        db.query(JobCandidateDecision)
        .filter_by(user_id=user_id, job_id=job_id, candidate_id=candidate_id)
        .first()
    )
