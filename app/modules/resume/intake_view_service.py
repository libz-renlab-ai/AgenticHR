"""简历库与岗位匹配候选人视图：从 IntakeCandidate 构建（四项齐全谓词）

PR4 新增。简历库 == 匹配候选人母集；匹配候选人额外按岗位学历门槛过滤。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.school_tier import meets_education, meets_school_tier
from app.modules.im_intake.templates import HARD_SLOT_KEYS
from app.modules.screening.models import Job


def _slot_complete_subquery(db: Session):
    """子查询: 每个候选人已填(value 非空)的 hard slot 数量"""
    return (
        db.query(
            IntakeSlot.candidate_id.label("cid"),
            func.count(IntakeSlot.id).label("filled_count"),
        )
        .filter(IntakeSlot.slot_key.in_(HARD_SLOT_KEYS))
        .filter(IntakeSlot.value.isnot(None))
        .filter(IntakeSlot.value != "")
        .group_by(IntakeSlot.candidate_id)
        .subquery()
    )


def _complete_query(db: Session, user_id: int):
    """简历库基础查询: 四项齐全(三 hard slot + PDF) 且属于该用户"""
    sub = _slot_complete_subquery(db)
    return (
        db.query(IntakeCandidate)
        .outerjoin(sub, IntakeCandidate.id == sub.c.cid)
        .filter(IntakeCandidate.user_id == user_id)
        .filter(IntakeCandidate.pdf_path.isnot(None))
        .filter(IntakeCandidate.pdf_path != "")
        .filter(sub.c.filled_count == len(HARD_SLOT_KEYS))
    )


def candidate_to_resume_dict(c: IntakeCandidate, db: Session | None = None) -> dict[str, Any]:
    """IntakeCandidate -> ResumeResponse-shape dict (前端零改动)

    spec 0429 阶段 A: status / reject_reason 直接读 candidate 列, 不再反查 Resume。
    历史兼容: 如果 candidate.status 还是默认 'pending' 但 intake_status 已 abandoned/
    timed_out, 仍按 intake_status 映射到 rejected (老数据没跑过 0022 backfill 时兜底)。
    """
    # 优先用 candidate 自己的 status (0022 已回填)
    status = (c.status or "").strip() or "pending"
    intake_st = c.intake_status or ""
    if status == "pending":
        if intake_st in ("abandoned", "timed_out"):
            status = "rejected"
        elif intake_st == "complete":
            status = "passed"

    reject_reason = (c.reject_reason or "")

    return {
        "id": c.id,
        "name": c.name or "",
        "phone": c.phone or "",
        "email": c.email or "",
        "education": c.education or "",
        "bachelor_school": c.bachelor_school or "",
        "master_school": c.master_school or "",
        "phd_school": c.phd_school or "",
        "qr_code_path": c.qr_code_path or "",
        "work_years": c.work_years or 0,
        "expected_salary_min": c.expected_salary_min or 0.0,
        "expected_salary_max": c.expected_salary_max or 0.0,
        "job_intention": c.job_intention or "",
        "skills": c.skills or "",
        "work_experience": c.work_experience or "",
        "project_experience": c.project_experience or "",
        "self_evaluation": c.self_evaluation or "",
        "source": c.source or "",
        "raw_text": c.raw_text or "",
        "pdf_path": c.pdf_path or "",
        "status": status,
        "ai_parsed": c.ai_parsed or "no",
        "ai_score": c.ai_score,
        "ai_summary": c.ai_summary or "",
        "reject_reason": reject_reason,
        "seniority": c.seniority or "",
        "intake_status": c.intake_status or "complete",
        "boss_id": c.boss_id or "",
        "school_tier": c.school_tier or "",
        "created_at": c.created_at or datetime.utcnow(),
        "updated_at": c.updated_at or datetime.utcnow(),
    }


def list_resume_library(
    db: Session,
    user_id: int,
    page: int = 1,
    page_size: int = 10,
    keyword: str | None = None,
    source: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """简历库列表: 四项齐全的候选人

    spec 0429 阶段 A 修复: 加 status 过滤 (passed/rejected/pending), 之前 router
    接受 status 参数但没传下来, 导致 dashboard 总数/通过/淘汰三计数全相同。
    """
    query = _complete_query(db, user_id)

    if source:
        query = query.filter(IntakeCandidate.source == source)

    # spec 0429: candidate.status 直接过滤; 渲染层已用 candidate.status 兜底
    if status:
        # 历史兼容: status='rejected' 时把 abandoned/timed_out 的也算进去
        if status == "rejected":
            query = query.filter(
                (IntakeCandidate.status == "rejected") |
                (IntakeCandidate.intake_status.in_(("abandoned", "timed_out")))
            )
        else:
            query = query.filter(IntakeCandidate.status == status)

    if keyword:
        _kw = keyword.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{_kw}%"
        query = query.filter(
            or_(
                IntakeCandidate.name.like(pattern, escape="\\"),
                IntakeCandidate.skills.like(pattern, escape="\\"),
                IntakeCandidate.job_intention.like(pattern, escape="\\"),
                IntakeCandidate.work_experience.like(pattern, escape="\\"),
                IntakeCandidate.raw_text.like(pattern, escape="\\"),
            )
        )

    total = query.count()
    items = (
        query.order_by(IntakeCandidate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [candidate_to_resume_dict(c, db) for c in items],
    }


def list_matched_for_job(
    db: Session,
    user_id: int,
    job_id: int,
    action_filter: str | None = None,
) -> list[dict[str, Any]]:
    """岗位匹配候选人: 简历库 ∩ 岗位学历门槛。

    返项追加 `job_action` 字段 (passed/rejected/None), 由 spec 0429-D 决策表注入。

    action_filter:
      None       → 不过滤 (默认)
      'passed'   → 仅返 job_action='passed'
      'rejected' → 仅返 job_action='rejected'
      'undecided'→ 仅返 job_action=None
    """
    job = db.query(Job).filter_by(id=job_id, user_id=user_id).first()
    if not job:
        return []

    edu_min = job.education_min or ""
    tier_min = (getattr(job, "school_tier_min", "") or "")

    # spec 0429-D edge case 2: 已 abandoned/timed_out candidate 不应出现在岗位匹配列表
    # (即便决策表残留 passed 行, candidate 已诈尸 → 不可被约面)
    candidates = (
        _complete_query(db, user_id)
        .filter(~IntakeCandidate.intake_status.in_(["abandoned", "timed_out"]))
        .all()
    )
    matched = [
        c for c in candidates
        if meets_education(c.education or "", edu_min)
        and meets_school_tier(c.school_tier or "", tier_min)
    ]
    matched.sort(key=lambda c: c.created_at or datetime.min, reverse=True)

    # spec 0429-D: 注入 job_action
    from app.modules.matching.decision_service import get_decisions_map_for_job
    decisions = get_decisions_map_for_job(db, user_id, job_id)

    out: list[dict[str, Any]] = []
    for c in matched:
        d = candidate_to_resume_dict(c, db)
        d["job_action"] = decisions.get(c.id)
        if action_filter == "passed" and d["job_action"] != "passed":
            continue
        if action_filter == "rejected" and d["job_action"] != "rejected":
            continue
        if action_filter == "undecided" and d["job_action"] is not None:
            continue
        out.append(d)
    return out
