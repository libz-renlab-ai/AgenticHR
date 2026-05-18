"""evaluate_and_record — 核心决策."""
import pytest

from app.modules.recruit_bot.education_check import EducationFilter


def _mk_job(db, user_id=1, threshold=60, with_competency=True):
    from app.modules.screening.models import Job
    comp = {
        "schema_version": 1,
        "hard_skills": [
            {"name": "Python", "weight": 9, "must_have": True},
            {"name": "Redis", "weight": 5, "must_have": False},
        ],
        "soft_skills": [],
        "experience": {"years_min": 2, "years_max": 5, "industries": []},
        "education": {"min_level": "本科"},
        "job_level": "",
        "bonus_items": [], "exclusions": [], "assessment_dimensions": [],
        "source_jd_hash": "h", "extracted_at": "2026-04-21T00:00:00Z",
    } if with_competency else None
    j = Job(
        user_id=user_id, title="后端", jd_text="招 Python",
        competency_model=comp,
        competency_model_status="approved" if with_competency else "none",
        greet_threshold=threshold,
    )
    db.add(j); db.commit(); db.refresh(j)
    return j


def _mk_candidate(boss_id="b1", name="张三", skills=None, work_years=3, education="本科",
                  school_tier_tags=None):
    from app.modules.recruit_bot.schemas import ScrapedCandidate
    return ScrapedCandidate(
        name=name, boss_id=boss_id, age=28,
        education=education, school="XX 大学", major="CS",
        intended_job="后端", work_years=work_years,
        skill_tags=skills or ["Python", "Redis"],
        school_tier_tags=school_tier_tags or [],
    )


def _mk_user(db, user_id=1, daily_cap=1000):
    from app.modules.auth.models import User
    u = User(id=user_id, username=f"hr{user_id}", password_hash="x", daily_cap=daily_cap)
    db.add(u); db.commit()
    return u


@pytest.mark.asyncio
async def test_evaluate_should_greet_education_pass(db):
    """学历达标 → should_greet (Task 4 改写: 旧 high_score → education_pass)."""
    from app.modules.recruit_bot.service import evaluate_and_record
    _mk_user(db)
    job = _mk_job(db, with_competency=False)
    c = _mk_candidate(education="硕士")
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    assert dec.decision == "should_greet"
    assert dec.resume_id is not None


@pytest.mark.asyncio
async def test_evaluate_rejected_low_education(db):
    """学历低于门槛 → rejected_low_education + reject_reason 带 education_only: 前缀."""
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.modules.resume.models import Resume
    _mk_user(db)
    job = _mk_job(db, with_competency=False)
    c = _mk_candidate(education="大专")
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    assert dec.decision == "rejected_low_education"
    r = db.query(Resume).filter_by(id=dec.resume_id).first()
    assert r.status == "rejected"
    assert r.reject_reason.startswith("education_only:")


@pytest.mark.asyncio
async def test_evaluate_skipped_already_greeted(db):
    from app.modules.recruit_bot.service import evaluate_and_record, upsert_resume_by_boss_id
    _mk_user(db)
    job = _mk_job(db, with_competency=False)
    c = _mk_candidate()
    r = upsert_resume_by_boss_id(db, user_id=1, candidate=c)
    r.greet_status = "greeted"
    db.commit()
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    assert dec.decision == "skipped_already_greeted"
    assert dec.resume_id == r.id


@pytest.mark.asyncio
async def test_evaluate_blocked_daily_cap(db):
    """cap=1 且已打过 1 次 → 返 blocked_daily_cap."""
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.modules.resume.models import Resume
    from datetime import datetime, timezone
    _mk_user(db, daily_cap=1)
    prev = Resume(
        user_id=1, name="prev", boss_id="other",
        greet_status="greeted",
        greeted_at=datetime.now(timezone.utc),
        source="boss_zhipin",
    )
    db.add(prev); db.commit()

    job = _mk_job(db, with_competency=False)
    c = _mk_candidate(boss_id="new_cand")
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    assert dec.decision == "blocked_daily_cap"


@pytest.mark.asyncio
async def test_evaluate_writes_audit_events(db):
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.core.audit.models import AuditEvent
    _mk_user(db)
    job = _mk_job(db, with_competency=False)
    c = _mk_candidate(education="硕士")
    await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    events = db.query(AuditEvent).filter(AuditEvent.f_stage == "F3_evaluate").all()
    assert len(events) >= 1


@pytest.mark.asyncio
async def test_evaluate_foreign_job_raises(db):
    from app.modules.recruit_bot.service import evaluate_and_record
    _mk_user(db, user_id=1)
    _mk_user(db, user_id=999)
    job = _mk_job(db, user_id=999, with_competency=False)
    c = _mk_candidate()
    with pytest.raises(ValueError, match="not found"):
        await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="本科"),
        )


@pytest.mark.asyncio
async def test_evaluate_no_competency_no_error(db):
    """取消 competency_model_status='approved' 前置后, 无能力模型也能正常筛."""
    from app.modules.recruit_bot.service import evaluate_and_record
    _mk_user(db)
    job = _mk_job(db, with_competency=False)
    c = _mk_candidate(education="硕士")
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    assert dec.decision == "should_greet"


class TestEducationOnlyMatchingResultWrite:
    """Task 3.1 设计的新测试组: 验证 MatchingResult 一行 upsert 行为."""

    @pytest.mark.asyncio
    async def test_should_greet_writes_matching_result(self, db):
        import json
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.matching.models import MatchingResult
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="硕士")
        dec = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="本科"),
        )
        assert dec.decision == "should_greet"
        row = db.query(MatchingResult).filter_by(
            resume_id=dec.resume_id, job_id=job.id
        ).first()
        assert row is not None
        assert row.total_score == 100.0
        assert row.education_score == 100.0
        assert row.skill_score == 0.0
        assert row.experience_score == 0.0
        assert row.seniority_score == 0.0
        assert row.industry_score == 0.0
        assert row.hard_gate_passed == 1
        assert "education_only" in json.loads(row.tags)
        ev = json.loads(row.evidence)
        assert "education_only" in ev
        assert ev["education_only"]["candidate_level"] == "硕士"

    @pytest.mark.asyncio
    async def test_rejected_low_education_writes_zero_score(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.matching.models import MatchingResult
        from app.modules.resume.models import Resume
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="大专")
        dec = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="本科"),
        )
        assert dec.decision == "rejected_low_education"
        row = db.query(MatchingResult).filter_by(
            resume_id=dec.resume_id, job_id=job.id
        ).first()
        assert row.total_score == 0.0 and row.education_score == 0.0
        assert row.hard_gate_passed == 0
        r = db.query(Resume).filter_by(id=dec.resume_id).first()
        assert r.reject_reason.startswith("education_only:")

    @pytest.mark.asyncio
    async def test_no_competency_no_error(self, db):
        """没 competency_model 也能正常筛."""
        from app.modules.recruit_bot.service import evaluate_and_record
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="硕士")
        dec = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="本科"),
        )
        assert dec.decision == "should_greet"

    @pytest.mark.asyncio
    async def test_matching_result_upsert_overwrites(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.matching.models import MatchingResult
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="本科")
        dec1 = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="本科"),
        )
        assert dec1.decision == "should_greet"
        before = db.query(MatchingResult).filter_by(
            resume_id=dec1.resume_id, job_id=job.id
        ).count()
        # 再评一次, 改门槛到 硕士, 重置 greet_status 避免短路
        from app.modules.resume.models import Resume
        r = db.query(Resume).filter_by(id=dec1.resume_id).first()
        r.greet_status = "none"
        db.commit()
        dec2 = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="硕士"),
        )
        assert dec2.decision == "rejected_low_education"
        after = db.query(MatchingResult).filter_by(
            resume_id=dec1.resume_id, job_id=job.id
        ).count()
        assert before == after == 1
        row = db.query(MatchingResult).filter_by(
            resume_id=dec1.resume_id, job_id=job.id
        ).first()
        assert row.total_score == 0.0
