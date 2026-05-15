"""Regression: recompute_resume 必须按 user_id 过滤 Job, 不能给跨账号 Job 写打分."""
import pytest
from unittest.mock import patch, AsyncMock

from app.modules.matching.models import MatchingResult
from app.modules.matching.service import (
    _new_task, recompute_resume,
)
from app.modules.resume.models import Resume
from app.modules.screening.models import Job


def _seed_cm():
    return {
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": False, "canonical_id": 1}],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }


@pytest.mark.asyncio
async def test_recompute_resume_only_scores_user_owned_jobs(db_session):
    """user 1 的 Resume 重算时只跑 user 1 的 Job, 绝不写到 user 2 的 Job."""
    cm = _seed_cm()

    # user 2 — active + approved job, 不应被 user 1 的 recompute 命中
    job_other = Job(
        title="他人后端", user_id=2, is_active=True, required_skills="Python",
        competency_model=cm, competency_model_status="approved",
        education_min="本科", jd_text="",
    )
    # user 1 — active + approved job
    job_self = Job(
        title="自家后端", user_id=1, is_active=True, required_skills="Python",
        competency_model=cm, competency_model_status="approved",
        education_min="本科", jd_text="",
    )
    db_session.add_all([job_other, job_self])
    db_session.flush()

    # user 1 的简历
    r = Resume(
        name="求职者", phone="13900000099", skills="Python", work_years=3,
        education="本科", ai_parsed="yes", source="manual", seniority="中级",
        user_id=1,
    )
    db_session.add(r); db_session.commit()

    task_id = _new_task(0)

    with patch(
        "app.modules.matching.service.enhance_evidence_with_llm",
        new=AsyncMock(side_effect=lambda ev, *a, **kw: ev),
    ):
        await recompute_resume(db_session, r.id, task_id, user_id=1)

    db_session.expire_all()
    rows = db_session.query(MatchingResult).filter_by(resume_id=r.id).all()
    job_ids_written = {row.job_id for row in rows}
    assert job_self.id in job_ids_written, "应给自家 Job 写分"
    assert job_other.id not in job_ids_written, \
        f"绝不能给 user 2 的 Job 写分, 但写到了: {job_ids_written}"
