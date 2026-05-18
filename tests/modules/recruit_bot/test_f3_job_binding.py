"""F3 job_id 确定性绑定 — spec-2026-05-15-job-binding.

T1: F3 路径透传 job_id → IntakeCandidate.job_id + Resume.job_id
T3: 同 boss_id 二次 greet 不同岗位时 first-write wins + 审计
"""
import pytest


def _mk_user(db, user_id=1, daily_cap=1000):
    from app.modules.auth.models import User
    u = User(id=user_id, username=f"hr{user_id}", password_hash="x", daily_cap=daily_cap)
    db.add(u); db.commit()
    return u


def _mk_job(db, *, user_id=1, title="后端", threshold=30, with_competency=True):
    from app.modules.screening.models import Job
    comp = {
        "schema_version": 1,
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": True}],
        "soft_skills": [],
        "experience": {"years_min": 0, "years_max": 99, "industries": []},
        "education": {"min_level": "本科"},
        "job_level": "",
        "bonus_items": [], "exclusions": [], "assessment_dimensions": [],
        "source_jd_hash": "h", "extracted_at": "2026-04-21T00:00:00Z",
    } if with_competency else None
    j = Job(
        user_id=user_id, title=title, jd_text=f"招 {title}",
        competency_model=comp,
        competency_model_status="approved" if with_competency else "none",
        greet_threshold=threshold,
    )
    db.add(j); db.commit(); db.refresh(j)
    return j


def _mk_candidate(boss_id="b1", name="张三"):
    from app.modules.recruit_bot.schemas import ScrapedCandidate
    return ScrapedCandidate(
        name=name, boss_id=boss_id, age=28,
        education="本科", school="清华大学", major="CS",
        intended_job="后端", work_years=3,
        skill_tags=["Python", "Redis"],
    )


def _default_filter():
    """spec 2026-05-15-education-only-filter: evaluate_and_record 必填参数;
    job_binding 测试只关心 job_id 绑定行为，用最低门槛避免学历检查干扰。"""
    from app.modules.recruit_bot.education_check import EducationFilter
    return EducationFilter(min_level="大专")


class TestF3JobBindingWriteThrough:
    """T1: evaluate_and_record(job_id=A) → candidate.job_id=A & resume.job_id=A."""

    @pytest.mark.asyncio
    async def test_evaluate_and_record_writes_job_id_to_candidate(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.im_intake.candidate_model import IntakeCandidate
        _mk_user(db)
        job = _mk_job(db)
        cand = _mk_candidate(boss_id="b_t1a")
        await evaluate_and_record(db, user_id=1, job_id=job.id, candidate=cand, education_filter=_default_filter())
        c = db.query(IntakeCandidate).filter_by(user_id=1, boss_id="b_t1a").first()
        assert c is not None, "F3 应创建 IntakeCandidate"
        assert c.job_id == job.id, (
            f"F3 evaluate_and_record(job_id={job.id}) 后 candidate.job_id 应为 "
            f"{job.id}, 实际 {c.job_id}"
        )

    @pytest.mark.asyncio
    async def test_evaluate_and_record_writes_job_id_to_resume(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.resume.models import Resume
        _mk_user(db)
        job = _mk_job(db)
        cand = _mk_candidate(boss_id="b_t1b")
        await evaluate_and_record(db, user_id=1, job_id=job.id, candidate=cand, education_filter=_default_filter())
        r = db.query(Resume).filter_by(user_id=1, boss_id="b_t1b").first()
        assert r is not None
        assert r.job_id == job.id, (
            f"F3 evaluate_and_record(job_id={job.id}) 后 resume.job_id 应为 "
            f"{job.id}, 实际 {r.job_id}"
        )

    def test_upsert_resume_by_boss_id_accepts_job_id_kwarg(self, db):
        """直接调 upsert_resume_by_boss_id 也能传 job_id."""
        from app.modules.recruit_bot.service import upsert_resume_by_boss_id
        from app.modules.im_intake.candidate_model import IntakeCandidate
        _mk_user(db)
        job = _mk_job(db)
        cand = _mk_candidate(boss_id="b_t1c")
        r = upsert_resume_by_boss_id(db, user_id=1, candidate=cand, job_id=job.id)
        assert r.job_id == job.id, "Resume.job_id 应被显式 kwarg 写入"
        c = db.query(IntakeCandidate).filter_by(user_id=1, boss_id="b_t1c").first()
        assert c.job_id == job.id, "对应 IntakeCandidate.job_id 也应被透传"

    def test_upsert_existing_candidate_backfills_null_job_id(self, db):
        """模拟历史数据: 老路径先建 job_id=NULL 的 candidate; 再走带 job_id 的新路径,
        应把 NULL 回填(NULL → 实值不算 first-write 覆盖)."""
        from app.modules.recruit_bot.service import upsert_resume_by_boss_id
        from app.modules.im_intake.candidate_model import IntakeCandidate
        _mk_user(db)
        job = _mk_job(db)
        cand = _mk_candidate(boss_id="b_t1d")
        # 第一次: 不传 job_id (模拟历史)
        upsert_resume_by_boss_id(db, user_id=1, candidate=cand)
        c1 = db.query(IntakeCandidate).filter_by(user_id=1, boss_id="b_t1d").first()
        assert c1.job_id is None, "前置: 不传 job_id 时确应为 NULL"
        # 第二次: 传 job_id → 回填
        upsert_resume_by_boss_id(db, user_id=1, candidate=cand, job_id=job.id)
        db.refresh(c1)
        assert c1.job_id == job.id, (
            f"已存在且 job_id=NULL 时,新一次写入应回填,实际 {c1.job_id}"
        )


class TestF3CrossJobRebind:
    """T3: 同 boss_id 二次 greet 不同 job 时 first-write wins + 审计."""

    @pytest.mark.asyncio
    async def test_second_greet_different_job_keeps_first_job_id(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.im_intake.candidate_model import IntakeCandidate
        _mk_user(db)
        job_a = _mk_job(db, title="后端")
        job_b = _mk_job(db, title="产品经理")
        cand = _mk_candidate(boss_id="b_t3a")
        await evaluate_and_record(db, user_id=1, job_id=job_a.id, candidate=cand, education_filter=_default_filter())
        # 同 boss_id 二次 greet 另一岗位
        await evaluate_and_record(db, user_id=1, job_id=job_b.id, candidate=cand, education_filter=_default_filter())
        c = db.query(IntakeCandidate).filter_by(user_id=1, boss_id="b_t3a").first()
        assert c.job_id == job_a.id, (
            f"first-write wins: 应保持第一次 {job_a.id}, 实际 {c.job_id}"
        )

    @pytest.mark.asyncio
    async def test_second_greet_different_job_writes_audit_event(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.im_intake.candidate_model import IntakeCandidate
        from app.core.audit.models import AuditEvent
        _mk_user(db)
        job_a = _mk_job(db, title="后端")
        job_b = _mk_job(db, title="产品经理")
        cand = _mk_candidate(boss_id="b_t3b")
        await evaluate_and_record(db, user_id=1, job_id=job_a.id, candidate=cand, education_filter=_default_filter())
        await evaluate_and_record(db, user_id=1, job_id=job_b.id, candidate=cand, education_filter=_default_filter())
        c = db.query(IntakeCandidate).filter_by(user_id=1, boss_id="b_t3b").first()
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.f_stage == "f3_job_rebind_attempt")
            .filter(AuditEvent.entity_id == c.id)
            .all()
        )
        assert len(events) >= 1, (
            "二次 greet 不同 job 时应有 f3_job_rebind_attempt 审计行,"
            f"实际 {[e.f_stage for e in db.query(AuditEvent).all()]}"
        )

    @pytest.mark.asyncio
    async def test_same_job_twice_no_rebind_audit(self, db):
        """同一岗位二次 greet 不应产生 cross-job 审计."""
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.im_intake.candidate_model import IntakeCandidate
        from app.core.audit.models import AuditEvent
        _mk_user(db)
        job = _mk_job(db, title="后端")
        cand = _mk_candidate(boss_id="b_t3c")
        await evaluate_and_record(db, user_id=1, job_id=job.id, candidate=cand, education_filter=_default_filter())
        await evaluate_and_record(db, user_id=1, job_id=job.id, candidate=cand, education_filter=_default_filter())
        c = db.query(IntakeCandidate).filter_by(user_id=1, boss_id="b_t3c").first()
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.f_stage == "f3_job_rebind_attempt")
            .filter(AuditEvent.entity_id == c.id)
            .all()
        )
        assert len(events) == 0, "同 job 二次 greet 不应触发 cross-job 审计"
