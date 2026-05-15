"""Regression: ScreeningService.screen_resumes 必须按 user_id 隔离,
不能扫到其他 user 的简历。
"""
from app.modules.resume.models import Resume
from app.modules.screening.models import Job
from app.modules.screening.service import ScreeningService


def test_screen_resumes_excludes_other_user_resumes(db_session):
    """user_id=1 的 job 只筛 user_id=1 的简历, 跨账号简历不出现在结果里."""
    # user 2 的简历 (不应被 user 1 的筛选扫到)
    db_session.add(Resume(
        name="他人A", phone="13800000001", education="本科", work_years=3,
        skills="Python", ai_parsed="yes", source="manual", seniority="中级",
        user_id=2,
    ))
    db_session.add(Resume(
        name="他人B", phone="13800000002", education="本科", work_years=5,
        skills="Python", ai_parsed="yes", source="manual", seniority="高级",
        user_id=2,
    ))
    # user 1 自己的简历
    db_session.add(Resume(
        name="自己A", phone="13800000003", education="本科", work_years=3,
        skills="Python", ai_parsed="yes", source="manual", seniority="中级",
        user_id=1,
    ))
    db_session.flush()

    job = Job(
        title="后端", user_id=1, is_active=True,
        education_min="本科", work_years_min=0, work_years_max=99,
        required_skills="Python", jd_text="",
    )
    db_session.add(job); db_session.commit()

    result = ScreeningService(db_session).screen_resumes(job.id, user_id=1)
    names = {row["resume_name"] for row in result["results"]}
    assert "自己A" in names
    assert "他人A" not in names, "user 2 的简历不应在 user 1 的筛选结果里"
    assert "他人B" not in names
    assert result["total"] == 1
