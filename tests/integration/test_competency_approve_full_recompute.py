"""F2 强触发: approve_competency 后全量重算 matching_results.

验证 _recompute_with_purge_for_competency_change 能把 job 下所有
matching_results 行的 competency_hash 刷新为新 cm 对应的 hash,
而不是停留在旧 cm 的 hash (即不再有 stale 残留)。
"""
import pytest
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, AsyncMock

from app.modules.matching.hashing import compute_competency_hash
from app.modules.matching.models import MatchingResult
from app.modules.matching.triggers import on_competency_approved
from app.modules.resume.models import Resume
from app.modules.screening.models import Job
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot


def _seed_user(db_engine, uid: int) -> None:
    """补一条 users 行使 IntakeCandidate.user_id FK 通过."""
    from sqlalchemy import text
    with db_engine.begin() as conn:
        conn.execute(text(
            "INSERT OR IGNORE INTO users (id, username, password_hash, display_name, is_active, daily_cap) "
            "VALUES (:uid, :uname, 'x', 'Tester', 1, 1000)"
        ), {"uid": uid, "uname": f"tester{uid}"})


def _make_complete_candidate(db, user_id, name, skills, education="本科"):
    """造一个四项齐全 + promoted 的 candidate-resume 对。"""
    r = Resume(
        name=name, phone="", skills=skills, work_years=3,
        education=education, ai_parsed="yes", source="manual",
        seniority="中级", user_id=user_id,
    )
    db.add(r); db.flush()
    c = IntakeCandidate(
        user_id=user_id, boss_id=f"boss-{name}",
        name=name, phone="", education=education,
        skills=skills, source="manual", pdf_path=f"/tmp/{name}.pdf",
        intake_status="complete", promoted_resume_id=r.id,
        status="passed",
    )
    db.add(c); db.flush()
    for k in ("arrival_date", "free_slots", "intern_duration"):
        db.add(IntakeSlot(
            candidate_id=c.id, slot_key=k, slot_category="hard", value="filled",
        ))
    db.commit()
    return c, r


@pytest.mark.asyncio
async def test_full_recompute_refreshes_all_hashes(db_session, db_engine, monkeypatch):
    """approve_competency 后所有 matching_results 都使用新 competency_hash."""
    from app.modules.screening import router as screening_router
    from app.modules.screening.competency_service import apply_competency_to_job

    # SessionLocal / _session_factory 重定向到测试引擎, 让 apply_competency_to_job
    # 和 _recompute_with_purge_for_competency_change 看到同一份数据。
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr("app.database.engine", db_engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    monkeypatch.setattr(
        "app.modules.screening.competency_service._session_factory", factory,
    )

    uid = 999
    _seed_user(db_engine, uid)

    cm_old = {
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": True, "canonical_id": 1}],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }
    job = Job(
        title="后端", user_id=uid, is_active=True, required_skills="",
        competency_model=cm_old, competency_model_status="approved",
        education_min="本科",
    )
    db_session.add(job); db_session.commit()

    cands = []
    for i, sk in enumerate(["Python", "Java", "Go"]):
        c, r = _make_complete_candidate(db_session, uid, f"u{i}", sk)
        cands.append((c, r))

    with patch("app.modules.matching.service.enhance_evidence_with_llm",
               new=AsyncMock(side_effect=lambda ev, *a, **kw: ev)):
        await on_competency_approved(db_session, job.id)

    db_session.expire_all()
    rows_before = db_session.query(MatchingResult).filter_by(job_id=job.id).all()
    assert len(rows_before) == 3
    old_hash = compute_competency_hash(cm_old)
    assert all(r.competency_hash == old_hash for r in rows_before)

    cm_new = {
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": False, "canonical_id": 1}],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }
    apply_competency_to_job(job.id, cm_new)
    new_hash = compute_competency_hash(cm_new)

    with patch("app.modules.matching.service.enhance_evidence_with_llm",
               new=AsyncMock(side_effect=lambda ev, *a, **kw: ev)):
        await screening_router._recompute_with_purge_for_competency_change(
            job.id, uid,
        )

    db_session.expire_all()
    rows_after = db_session.query(MatchingResult).filter_by(job_id=job.id).all()
    assert len(rows_after) == 3
    assert all(r.competency_hash == new_hash for r in rows_after), \
        f"stale hash 未刷新: {[r.competency_hash for r in rows_after]}"
