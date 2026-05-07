"""岗位管理与硬性条件筛选测试"""
from app.modules.screening.service import ScreeningService
from app.modules.screening.schemas import JobCreate, JobUpdate
from app.modules.resume.service import ResumeService
from app.modules.resume.schemas import ResumeCreate


def _create_test_resumes(db_session):
    rs = ResumeService(db_session)
    rs.create(ResumeCreate(
        name="候选人A", phone="13165338580", education="硕士",
        work_years=5, expected_salary_min=20000, expected_salary_max=30000,
        skills="Python,FastAPI,Docker", source="boss_zhipin",
    ))
    rs.create(ResumeCreate(
        name="候选人B", phone="13165338581", education="大专",
        work_years=1, expected_salary_min=8000, expected_salary_max=12000,
        skills="Python", source="boss_zhipin",
    ))
    rs.create(ResumeCreate(
        name="候选人C", phone="13165338582", education="本科",
        work_years=3, expected_salary_min=15000, expected_salary_max=20000,
        skills="Java,Spring,MySQL", source="boss_zhipin",
    ))


def test_create_job(db_session):
    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(
        title="Python开发", education_min="本科",
        work_years_min=2, required_skills="Python",
    ))
    assert job.id is not None
    assert job.title == "Python开发"


def test_list_jobs(db_session):
    service = ScreeningService(db_session)
    service.create_job(JobCreate(title="岗位1"))
    service.create_job(JobCreate(title="岗位2"))
    result = service.list_jobs()
    assert result["total"] == 2


def test_update_job(db_session):
    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(title="旧标题"))
    updated = service.update_job(job.id, JobUpdate(title="新标题"))
    assert updated.title == "新标题"


def test_screen_by_education(db_session):
    _create_test_resumes(db_session)
    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(title="测试岗", education_min="本科"))
    result = service.screen_resumes(job.id)
    assert result["passed"] == 2
    assert result["rejected"] == 1


def test_screen_by_work_years(db_session):
    _create_test_resumes(db_session)
    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(title="测试岗", work_years_min=3))
    result = service.screen_resumes(job.id)
    assert result["passed"] == 2
    assert result["rejected"] == 1


def test_screen_by_skills(db_session):
    _create_test_resumes(db_session)
    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(title="测试岗", required_skills="Python"))
    result = service.screen_resumes(job.id)
    assert result["passed"] == 2
    assert result["rejected"] == 1


def test_screen_combined(db_session):
    _create_test_resumes(db_session)
    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(
        title="测试岗", education_min="本科",
        work_years_min=3, required_skills="Python",
    ))
    result = service.screen_resumes(job.id)
    assert result["passed"] == 1


def test_screen_does_not_mutate_resume_status(db_session):
    """screening 是只读操作：不得修改简历的全局 status（per-job 状态由 matching_results 管理）"""
    from app.modules.resume.models import Resume
    _create_test_resumes(db_session)
    service = ScreeningService(db_session)

    # 记录筛选前的所有状态
    before = {r.id: r.status for r in db_session.query(Resume).all()}

    job = service.create_job(JobCreate(
        title="只读筛选测试岗", education_min="本科",
        work_years_min=3, required_skills="Python",
    ))
    result = service.screen_resumes(job.id)
    assert result["passed"] == 1
    assert result["rejected"] == 2

    # 筛选后状态必须与筛选前完全一致
    db_session.expire_all()
    after = {r.id: r.status for r in db_session.query(Resume).all()}
    assert before == after, f"screening 修改了简历状态：{before} → {after}"


def test_multi_job_screening_isolation(db_session):
    """Job A 的筛选结果不影响 Job B 可见的候选人（核心多岗位 bug 回归）"""
    _create_test_resumes(db_session)
    service = ScreeningService(db_session)

    # Job A：要求 Python 技能（候选人C 没有，应该被排除）
    job_a = service.create_job(JobCreate(title="岗位A", required_skills="Python"))
    result_a = service.screen_resumes(job_a.id)
    assert result_a["passed"] == 2
    assert result_a["rejected"] == 1

    # Job B：要求 Java 技能（候选人A、B 没有，应该被排除）
    # 如果 Job A 的筛选把候选人C 标记为 rejected，这里就只有 0 人了（旧 bug）
    job_b = service.create_job(JobCreate(title="岗位B", required_skills="Java"))
    result_b = service.screen_resumes(job_b.id)
    assert result_b["passed"] == 1, (
        f"多岗位隔离 bug：Job A 筛选后 Job B 只看到 {result_b['passed']} 人，应为 1 人"
    )
    assert result_b["rejected"] == 2


def test_screen_resumes_handles_null_work_years_and_salary_bug145(db_session):
    """BUG-145: 历史脏数据 / 老 schema 让 work_years/expected_salary_min 为 NULL,
    `None < int` 会抛 TypeError 整个 job 筛选 500. 修后应用 0 兜底, 走正常路径。"""
    from app.modules.resume.models import Resume
    # 直接用 ORM 创建带 NULL 字段的简历, 绕过 ResumeCreate schema 默认 0
    r1 = Resume(name="脏数据A", education="本科", work_years=None,
                expected_salary_min=None, expected_salary_max=None,
                skills="Python", source="boss_zhipin", user_id=1)
    db_session.add(r1)
    db_session.commit()

    service = ScreeningService(db_session)
    job = service.create_job(JobCreate(
        title="测试岗", education_min="本科",
        work_years_min=2, required_skills="Python",
    ))

    # 不应抛 TypeError; work_years=None → 0 → < years_min=2 → reject_reasons 包含工作年限
    result = service.screen_resumes(job.id, resume_ids=[r1.id])
    assert result["total"] == 1
    assert result["rejected"] == 1
    reasons = result["results"][0]["reject_reasons"]
    # 0 年 < 2 年要求
    assert any("工作年限" in r for r in reasons)
