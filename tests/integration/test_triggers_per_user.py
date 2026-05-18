"""Regression: F2 后台触发器必须按 user_id 隔离, 不能跨账户写 MatchingResult.

复盘:
- on_resume_parsed 之前用全局 Job 查询, 用户 A 简历会被打分到用户 B 的岗位
- on_competency_approved 之前用全局 Resume 查询, 用户 A 岗位审批后会扫到用户 B 的简历
两个 leak 与 b867297 / 6ede54e / e115bab 同源, 但发生在后台触发器, 影响面更广。
"""
import pytest
from unittest.mock import patch, AsyncMock

from app.modules.matching.models import MatchingResult
from app.modules.matching.triggers import on_resume_parsed, on_competency_approved
from app.modules.resume.models import Resume
from app.modules.screening.models import Job


def _seed_cm():
    return {
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": False, "canonical_id": 1}],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }


def _mk_job(user_id: int, title: str = "Job") -> Job:
    return Job(
        title=title, user_id=user_id, is_active=True, required_skills="Python",
        competency_model=_seed_cm(), competency_model_status="approved",
        education_min="本科", jd_text="",
    )


def _mk_resume(user_id: int, name: str, phone: str) -> Resume:
    return Resume(
        name=name, phone=phone, skills="Python", work_years=3,
        education="本科", ai_parsed="yes", source="manual", seniority="中级",
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_on_resume_parsed_only_scores_user_owned_jobs(db_session):
    """user 1 的简历进 T1 触发器, 只该给 user 1 的 Job 写 MatchingResult,
    绝不能写到 user 2 的 Job."""
    job_other = _mk_job(user_id=2, title="他人后端")
    job_self = _mk_job(user_id=1, title="自家后端")
    db_session.add_all([job_other, job_self])
    db_session.flush()

    r = _mk_resume(user_id=1, name="user1求职者", phone="13900000099")
    db_session.add(r); db_session.commit()

    with patch(
        "app.modules.matching.service.enhance_evidence_with_llm",
        new=AsyncMock(side_effect=lambda ev, *a, **kw: ev),
    ):
        await on_resume_parsed(db_session, r.id)

    db_session.expire_all()
    rows = db_session.query(MatchingResult).filter_by(resume_id=r.id).all()
    job_ids_written = {row.job_id for row in rows}
    assert job_self.id in job_ids_written, "应给自家 Job 写分"
    assert job_other.id not in job_ids_written, \
        f"绝不能给 user 2 的 Job 写分, 但写到了: {job_ids_written}"


@pytest.mark.asyncio
async def test_on_competency_approved_only_scores_user_owned_resumes(db_session):
    """user 1 的岗位 approve 后触发 T2, 只该扫 user 1 的简历,
    绝不能给 user 2 的简历写 MatchingResult."""
    job = _mk_job(user_id=1, title="后端")
    db_session.add(job); db_session.flush()

    r_self = _mk_resume(user_id=1, name="自家求职者", phone="13900000001")
    r_other = _mk_resume(user_id=2, name="他人求职者", phone="13900000002")
    db_session.add_all([r_self, r_other])
    db_session.commit()

    with patch(
        "app.modules.matching.service.enhance_evidence_with_llm",
        new=AsyncMock(side_effect=lambda ev, *a, **kw: ev),
    ):
        await on_competency_approved(db_session, job.id)

    db_session.expire_all()
    rows = db_session.query(MatchingResult).filter_by(job_id=job.id).all()
    resume_ids_written = {row.resume_id for row in rows}
    assert r_self.id in resume_ids_written, "应给自家简历写分"
    assert r_other.id not in resume_ids_written, \
        f"绝不能给 user 2 的简历写分, 但写到了: {resume_ids_written}"
