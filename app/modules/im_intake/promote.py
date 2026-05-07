import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.resume.models import Resume

_log = logging.getLogger(__name__)


# BUG-123: 把 candidate 上抽出/沉淀的结构化字段在 promote 时一并复制到 Resume,
# 否则 Resume 只剩 name/raw_text/pdf_path, 五维 score_skill/experience/industry
# 等 scorer 拿到的全部是空值, 打分恒等于占位数; 也让 list_matched_for_job 与
# screening.screen_resumes 在 candidate vs resume 两套口径上彻底一致.
_CAND_TO_RESUME_FIELDS: tuple[str, ...] = (
    "phone", "email", "education",
    "bachelor_school", "master_school", "phd_school",
    "skills", "work_experience", "project_experience", "self_evaluation",
    "job_intention", "work_years", "seniority",
    "expected_salary_min", "expected_salary_max",
    "qr_code_path", "ai_parsed", "ai_score", "ai_summary",
)


def _copy_fields(candidate: IntakeCandidate, resume: Resume, *, only_if_empty: bool) -> None:
    """把 candidate 的结构化字段复制到 resume.

    only_if_empty=True 表示 merge 路径, 不覆盖 Resume 已有的非空数据 (F3 抓取的优先);
    only_if_empty=False 表示新建路径, 直接覆盖.

    BUG-128: 数值 0 是合法值 (应届生 work_years=0 / 薪资不限 expected_salary_min=0 /
    AI 评分 0 分), 不视为"未填". 仅 None 与字符串空串视为缺失.
    merge 路径判断 Resume 已有数据时, 仍把数值 0 视为"可被覆盖的占位" (因为新建 Resume
    时 ORM 默认填 0, 与 candidate 的真实 0 无法区分; 让 candidate 真值优先).
    """
    for f in _CAND_TO_RESUME_FIELDS:
        cand_val = getattr(candidate, f, None)
        if cand_val is None:
            continue
        if isinstance(cand_val, str) and not cand_val:
            continue
        if only_if_empty:
            cur = getattr(resume, f, None)
            empty = (
                cur is None
                or (isinstance(cur, (int, float)) and cur == 0)
                or (isinstance(cur, str) and not cur)
            )
            if not empty:
                continue
        setattr(resume, f, cand_val)


def promote_to_resume(db: Session, candidate: IntakeCandidate, user_id: int = 0) -> Resume:
    # BUG-016 / BUG-047: user_id<=0 creates orphan Resume rows (no real user
    # owns user_id 0; they cannot be queried by any /api/resumes endpoint
    # because every endpoint filters by Resume.user_id == calling_user_id).
    # Hard-reject so the misuse surfaces at the call site instead of leaving
    # dangling DB rows that confuse later debugging.
    if user_id is None or int(user_id) <= 0:
        raise ValueError(
            f"promote_to_resume requires user_id > 0, got {user_id!r} "
            f"(candidate {candidate.id}, boss_id={candidate.boss_id!r})"
        )
    # Local import to break a circular dependency: outbox_service imports
    # IntakeService → service.py imports promote.py.
    from app.modules.im_intake.outbox_service import expire_pending_for_candidate

    if candidate.promoted_resume_id:
        existing = db.query(Resume).filter_by(id=candidate.promoted_resume_id).first()
        if existing:
            # Idempotent re-promote: still flush any zombie outbox so a stale
            # row from before the original promotion cannot fire later.
            expire_pending_for_candidate(db, candidate.id, reason="promote_idempotent")
            return existing

    # Merge semantics: if a Resume already exists with the same boss_id (e.g. from
    # F3 greet flow), update it in-place instead of creating a duplicate row.
    existing_by_boss = None
    if candidate.boss_id:
        q = db.query(Resume).filter(Resume.boss_id == candidate.boss_id)
        q = q.filter(Resume.user_id == user_id)
        existing_by_boss = q.first()

    if existing_by_boss is not None:
        r = existing_by_boss
        # Only fill fields that are empty on the existing row so we don't clobber
        # richer F3-sourced data; always upgrade intake_status to complete.
        if not r.name and candidate.name:
            r.name = candidate.name
        if not r.job_id and candidate.job_id:
            r.job_id = candidate.job_id
        if not r.pdf_path and candidate.pdf_path:
            r.pdf_path = candidate.pdf_path
        if not r.raw_text and candidate.raw_text:
            r.raw_text = candidate.raw_text
        # BUG-123: 复制结构化字段; merge 路径不覆盖 Resume 已有数据
        _copy_fields(candidate, r, only_if_empty=True)
        r.intake_status = "complete"
        if not r.intake_started_at and candidate.intake_started_at:
            r.intake_started_at = candidate.intake_started_at
        r.intake_completed_at = datetime.now(timezone.utc)
        # spec 0429 阶段 C: 反向键 1:1
        r.intake_candidate_id = candidate.id
        db.flush()

        candidate.promoted_resume_id = r.id
        candidate.intake_status = "complete"
        candidate.intake_completed_at = datetime.now(timezone.utc)
        # spec 0429 阶段 A: candidate.status 同步为 passed (Resume.status 已是 passed)
        if not candidate.status or candidate.status == "pending":
            candidate.status = r.status or "passed"
        # Expire any pending/claimed outbox so a stale scheduler row can't
        # fire after the candidate is already promoted.
        expire_pending_for_candidate(db, candidate.id, reason="promote_merge")
        return r

    r = Resume(
        user_id=user_id,
        name=candidate.name,
        boss_id=candidate.boss_id,
        job_id=candidate.job_id,
        pdf_path=candidate.pdf_path,
        raw_text=candidate.raw_text,
        status="passed",
        source="boss_zhipin",
        intake_status="complete",
        intake_started_at=candidate.intake_started_at,
        intake_completed_at=datetime.now(timezone.utc),
        # spec 0429 阶段 C: 反向键 1:1
        intake_candidate_id=candidate.id,
    )
    # BUG-123: 复制结构化字段, 让五维 scorer 能拿到真实数据
    _copy_fields(candidate, r, only_if_empty=False)
    db.add(r)
    db.flush()

    candidate.promoted_resume_id = r.id
    candidate.intake_status = "complete"
    candidate.intake_completed_at = datetime.now(timezone.utc)
    # spec 0429 阶段 A: candidate.status 同步为 passed
    candidate.status = "passed"
    expire_pending_for_candidate(db, candidate.id, reason="promote_new")
    return r
