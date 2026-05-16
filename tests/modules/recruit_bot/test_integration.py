"""F3 端到端后端路径集成测试 (education-only filter)."""
import pytest
from datetime import datetime, timezone

from app.modules.recruit_bot.education_check import EducationFilter


def _ef(min_level="本科", tags=None, require=False):
    return EducationFilter(
        min_level=min_level,
        prestigious_tags=tags or [],
        require_prestigious=require,
    )


def _mk_user(db, user_id, username, daily_cap=1000):
    from app.modules.auth.models import User
    u = User(id=user_id, username=username, password_hash="x", daily_cap=daily_cap)
    db.add(u); db.commit()


def _mk_job(db, user_id, threshold=30):
    from app.modules.screening.models import Job
    # education-only F3 不再要求 competency_model_status='approved', 但保留字段无害
    j = Job(
        user_id=user_id, title="后端", jd_text="x",
        competency_model=None,
        competency_model_status="none", greet_threshold=threshold,
    )
    db.add(j); db.commit(); db.refresh(j)
    return j


def _mk_cand(boss_id="b1", education="本科"):
    from app.modules.recruit_bot.schemas import ScrapedCandidate
    return ScrapedCandidate(
        name="张三", boss_id=boss_id, age=28, education=education,
        school="X 大", major="CS", intended_job="后端",
        work_years=3, skill_tags=["Python", "Redis"],
    )


@pytest.mark.asyncio
async def test_full_pipeline_should_greet_then_record(db):
    from app.modules.recruit_bot.service import (
        evaluate_and_record, record_greet_sent, get_daily_usage,
    )
    from app.modules.resume.models import Resume
    _mk_user(db, 1, "hr1")
    job = _mk_job(db, user_id=1)

    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=_mk_cand(),
        education_filter=_ef(min_level="本科"),
    )
    assert dec.decision == "should_greet"
    assert dec.resume_id is not None

    record_greet_sent(db, user_id=1, resume_id=dec.resume_id, success=True)
    r = db.query(Resume).filter_by(id=dec.resume_id).first()
    assert r.greet_status == "greeted"
    assert r.greeted_at is not None

    usage = get_daily_usage(db, user_id=1)
    assert usage.used == 1


@pytest.mark.asyncio
async def test_full_pipeline_rejected(db):
    """学历低于门槛 → rejected_low_education."""
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.modules.resume.models import Resume
    _mk_user(db, 1, "hr1")
    job = _mk_job(db, user_id=1)
    c = _mk_cand(boss_id="reject_target", education="大专")
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=_ef(min_level="本科"),
    )
    assert dec.decision == "rejected_low_education"
    r = db.query(Resume).filter_by(id=dec.resume_id).first()
    assert r.status == "rejected"
    assert r.greet_status == "none"
    assert r.reject_reason.startswith("education_only:")


@pytest.mark.asyncio
async def test_idempotent_evaluate(db):
    from app.modules.recruit_bot.service import evaluate_and_record
    _mk_user(db, 1, "hr1")
    job = _mk_job(db, user_id=1)
    d1 = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=_mk_cand(),
        education_filter=_ef(min_level="本科"),
    )
    d2 = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=_mk_cand(),
        education_filter=_ef(min_level="本科"),
    )
    assert d1.resume_id == d2.resume_id
    # 第二次因 greet_status='pending_greet'!='greeted' 仍能跑, 但 d1 已设 pending_greet
    # 所以 d2 走相同路径 → should_greet
    assert d1.decision == "should_greet"


@pytest.mark.asyncio
async def test_idempotent_record_greet_preserves_timestamp(db):
    from app.modules.recruit_bot.service import (
        evaluate_and_record, record_greet_sent,
    )
    from app.modules.resume.models import Resume
    _mk_user(db, 1, "hr1")
    job = _mk_job(db, user_id=1)
    d = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=_mk_cand(),
        education_filter=_ef(min_level="本科"),
    )
    record_greet_sent(db, user_id=1, resume_id=d.resume_id, success=True)
    r1 = db.query(Resume).filter_by(id=d.resume_id).first()
    t1 = r1.greeted_at
    record_greet_sent(db, user_id=1, resume_id=d.resume_id, success=True)
    r2 = db.query(Resume).filter_by(id=d.resume_id).first()
    assert r2.greeted_at == t1


@pytest.mark.asyncio
async def test_cap_across_multi_users(db):
    """user_A 打满 cap → 被 blocked, user_B 不受影响."""
    from app.modules.recruit_bot.service import (
        evaluate_and_record, record_greet_sent,
    )
    from app.modules.auth.models import User
    _mk_user(db, 1, "hr1")
    _mk_user(db, 2, "hr2")

    job_a = _mk_job(db, user_id=1)
    job_b = _mk_job(db, user_id=2)

    ua = db.query(User).filter_by(id=1).first(); ua.daily_cap = 1; db.commit()

    d1 = await evaluate_and_record(
        db, user_id=1, job_id=job_a.id, candidate=_mk_cand(boss_id="x1"),
        education_filter=_ef(min_level="本科"),
    )
    record_greet_sent(db, user_id=1, resume_id=d1.resume_id, success=True)

    d2 = await evaluate_and_record(
        db, user_id=1, job_id=job_a.id, candidate=_mk_cand(boss_id="x2"),
        education_filter=_ef(min_level="本科"),
    )
    assert d2.decision == "blocked_daily_cap"

    d3 = await evaluate_and_record(
        db, user_id=2, job_id=job_b.id, candidate=_mk_cand(boss_id="x3"),
        education_filter=_ef(min_level="本科"),
    )
    assert d3.decision == "should_greet"
