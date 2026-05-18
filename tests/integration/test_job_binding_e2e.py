"""端到端验证: F3 + F4 两个职位 × 多候选人 — spec-2026-05-15-job-binding 验收.

模拟真实场景:
    HR 同时招产品经理 + 开发, 各招 2 个人 一键打招呼, F4 register
    完成 intake 走完一遍, 然后用 ?job_id= 拉某岗位的人 — 必须看到只属于该
    岗位的人(命中 user 最初的担心: "我在系统中进行分岗位的简历筛选时, 能把
    这两批人分开吗?")。

per feedback_e2e_verification (memory): 单 happy-path 不足, N≥2 压测共享资源。
"""
import pytest


@pytest.fixture
def two_jobs(db_session):
    from app.modules.screening.models import Job
    pm = Job(
        user_id=1, title="产品经理", jd_text="招产品",
        competency_model={
            "schema_version": 1,
            "hard_skills": [{"name": "需求分析", "weight": 9, "must_have": False}],
            "soft_skills": [],
            "experience": {"years_min": 0, "years_max": 99, "industries": []},
            "education": {"min_level": "本科"},
            "job_level": "",
            "bonus_items": [], "exclusions": [], "assessment_dimensions": [],
            "source_jd_hash": "h1", "extracted_at": "2026-04-21T00:00:00Z",
        },
        competency_model_status="approved",
        greet_threshold=0,
    )
    dev = Job(
        user_id=1, title="开发", jd_text="招开发",
        competency_model={
            "schema_version": 1,
            "hard_skills": [{"name": "Python", "weight": 9, "must_have": False}],
            "soft_skills": [],
            "experience": {"years_min": 0, "years_max": 99, "industries": []},
            "education": {"min_level": "本科"},
            "job_level": "",
            "bonus_items": [], "exclusions": [], "assessment_dimensions": [],
            "source_jd_hash": "h2", "extracted_at": "2026-04-21T00:00:00Z",
        },
        competency_model_status="approved",
        greet_threshold=0,
    )
    db_session.add(pm); db_session.add(dev); db_session.commit()
    db_session.refresh(pm); db_session.refresh(dev)
    return pm, dev


def _scraped(boss_id: str, name: str, intended: str = ""):
    from app.modules.recruit_bot.schemas import ScrapedCandidate
    return ScrapedCandidate(
        name=name, boss_id=boss_id, age=28,
        education="本科", school="清华大学", major="CS",
        intended_job=intended or "通用",
        work_years=3, skill_tags=["Python"],
    )


def _default_filter():
    """spec 2026-05-15-education-only-filter: evaluate_and_record 必填参数;
    job_binding E2E 测试只验证 job_id 绑定行为，用最低门槛避免学历检查干扰。"""
    from app.modules.recruit_bot.education_check import EducationFilter
    return EducationFilter(min_level="大专")


@pytest.mark.asyncio
async def test_two_jobs_separated_after_f3_greet(db_session, two_jobs, monkeypatch):
    """N=2 候选人 × 2 岗位, F3 打招呼后, candidate.job_id 必须按 HR 选定的岗位分."""
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.resume.models import Resume

    # 短路 F2 内部 LLM/embedding 依赖(端到端走的是真实 service,需要 stub 才能在 unit
    # DB 上跑通;但 job_id 透传不依赖这些)
    monkeypatch.setattr(
        "app.modules.matching.scorers.skill._max_vector_similarity",
        lambda name, resume_names, db_session=None, _resume_emb_cache=None: 0.9,
    )
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "app.modules.matching.service.enhance_evidence_with_llm",
        AsyncMock(side_effect=lambda ev, *a, **kw: ev),
    )

    pm, dev = two_jobs
    # 产品经理岗 打招呼 2 个
    await evaluate_and_record(
        db_session, user_id=1, job_id=pm.id,
        candidate=_scraped("b_pm_1", "产品张"),
        education_filter=_default_filter(),
    )
    await evaluate_and_record(
        db_session, user_id=1, job_id=pm.id,
        candidate=_scraped("b_pm_2", "产品李"),
        education_filter=_default_filter(),
    )
    # 开发岗 打招呼 2 个
    await evaluate_and_record(
        db_session, user_id=1, job_id=dev.id,
        candidate=_scraped("b_dev_1", "开发王"),
        education_filter=_default_filter(),
    )
    await evaluate_and_record(
        db_session, user_id=1, job_id=dev.id,
        candidate=_scraped("b_dev_2", "开发陈"),
        education_filter=_default_filter(),
    )

    # 按 job_id 拉人, 必须只看到属于该岗位的
    pm_candidates = (
        db_session.query(IntakeCandidate)
        .filter_by(user_id=1, job_id=pm.id)
        .all()
    )
    dev_candidates = (
        db_session.query(IntakeCandidate)
        .filter_by(user_id=1, job_id=dev.id)
        .all()
    )
    assert {c.boss_id for c in pm_candidates} == {"b_pm_1", "b_pm_2"}, (
        f"产品岗候选人应只有 b_pm_1, b_pm_2; 实际 {[c.boss_id for c in pm_candidates]}"
    )
    assert {c.boss_id for c in dev_candidates} == {"b_dev_1", "b_dev_2"}, (
        f"开发岗候选人应只有 b_dev_1, b_dev_2; 实际 {[c.boss_id for c in dev_candidates]}"
    )

    # Resume 也同步带 job_id
    pm_resumes = db_session.query(Resume).filter_by(user_id=1, job_id=pm.id).all()
    dev_resumes = db_session.query(Resume).filter_by(user_id=1, job_id=dev.id).all()
    assert len(pm_resumes) == 2 and len(dev_resumes) == 2
    assert {r.boss_id for r in pm_resumes} == {"b_pm_1", "b_pm_2"}
    assert {r.boss_id for r in dev_resumes} == {"b_dev_1", "b_dev_2"}


@pytest.mark.asyncio
async def test_list_candidates_filter_by_job_id_works_end_to_end(
    db_session, client, two_jobs, monkeypatch,
):
    """HTTP 端到端: F3 打完招呼 → GET /api/intake/candidates?job_id=X 只看到该岗位的人."""
    from app.modules.recruit_bot.service import evaluate_and_record
    monkeypatch.setattr(
        "app.modules.matching.scorers.skill._max_vector_similarity",
        lambda name, resume_names, db_session=None, _resume_emb_cache=None: 0.9,
    )
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "app.modules.matching.service.enhance_evidence_with_llm",
        AsyncMock(side_effect=lambda ev, *a, **kw: ev),
    )

    pm, dev = two_jobs
    await evaluate_and_record(db_session, user_id=1, job_id=pm.id, candidate=_scraped("b_e2e_pm_a", "产A"), education_filter=_default_filter())
    await evaluate_and_record(db_session, user_id=1, job_id=pm.id, candidate=_scraped("b_e2e_pm_b", "产B"), education_filter=_default_filter())
    await evaluate_and_record(db_session, user_id=1, job_id=dev.id, candidate=_scraped("b_e2e_dev_a", "开A"), education_filter=_default_filter())
    db_session.commit()

    resp_pm = client.get(f"/api/intake/candidates?job_id={pm.id}&size=200")
    assert resp_pm.status_code == 200
    body_pm = resp_pm.json()
    pm_boss_ids = {it["boss_id"] for it in body_pm["items"]}
    assert pm_boss_ids == {"b_e2e_pm_a", "b_e2e_pm_b"}, (
        f"GET /api/intake/candidates?job_id={pm.id} 应只列产品岗的人, 实际 {pm_boss_ids}"
    )

    resp_dev = client.get(f"/api/intake/candidates?job_id={dev.id}&size=200")
    assert resp_dev.status_code == 200
    body_dev = resp_dev.json()
    dev_boss_ids = {it["boss_id"] for it in body_dev["items"]}
    assert dev_boss_ids == {"b_e2e_dev_a"}, (
        f"GET /api/intake/candidates?job_id={dev.id} 应只列开发岗的人, 实际 {dev_boss_ids}"
    )


@pytest.mark.asyncio
async def test_cross_job_greet_does_not_leak_into_other_job_view(
    db_session, two_jobs, monkeypatch,
):
    """同一个人被同时招呼到 2 个岗位时, primary 应保持第一次 (产品),
    不应漏到第二个岗位 (开发) 的视图里."""
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.modules.im_intake.candidate_model import IntakeCandidate
    monkeypatch.setattr(
        "app.modules.matching.scorers.skill._max_vector_similarity",
        lambda name, resume_names, db_session=None, _resume_emb_cache=None: 0.9,
    )
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "app.modules.matching.service.enhance_evidence_with_llm",
        AsyncMock(side_effect=lambda ev, *a, **kw: ev),
    )

    pm, dev = two_jobs
    # 第一次: 产品岗打招呼
    await evaluate_and_record(
        db_session, user_id=1, job_id=pm.id,
        candidate=_scraped("b_cross", "跨岗张"),
        education_filter=_default_filter(),
    )
    # 第二次: 开发岗 (HR 改了主意, 同一个人改投开发)
    await evaluate_and_record(
        db_session, user_id=1, job_id=dev.id,
        candidate=_scraped("b_cross", "跨岗张"),
        education_filter=_default_filter(),
    )

    c = db_session.query(IntakeCandidate).filter_by(boss_id="b_cross").first()
    assert c.job_id == pm.id, (
        "first-write wins: primary 岗位应保持产品 (pm.id), "
        f"实际 {c.job_id} (dev.id={dev.id}, pm.id={pm.id})"
    )

    # 在开发岗视图下应该看不到这个人
    dev_view = (
        db_session.query(IntakeCandidate)
        .filter_by(user_id=1, job_id=dev.id)
        .all()
    )
    assert "b_cross" not in {c.boss_id for c in dev_view}, (
        "跨岗 greet 候选人不应漏入第二个岗位的视图"
    )
