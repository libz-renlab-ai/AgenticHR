"""Regression: IMIntakeService.ensure_candidate 的 create 分支模糊匹配 Job
必须按 user_id 隔离, 不能配到他人账号的 Job。
"""
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.service import IntakeService
from app.modules.screening.models import Job


def test_create_branch_does_not_match_other_users_job(db_session):
    """user 2 有 Job '数据分析师', user 1 调 ensure_candidate(job_intention=...)
    不应被回填到 user 2 的 Job。
    """
    job_other = Job(
        title="数据分析师", user_id=2, is_active=True,
        required_skills="", jd_text="",
    )
    db_session.add(job_other); db_session.commit()
    other_job_id = job_other.id

    svc = IntakeService(db_session, user_id=1)
    cand = svc.ensure_candidate(
        boss_id="boss-x", name="某", job_intention="数据分析师",
    )

    assert cand.job_id != other_job_id, "create 分支跨账号匹配到他人 Job"
    assert cand.job_id is None, \
        f"user 1 没有对应 Job, 应留空, 实际 job_id={cand.job_id}"


def test_create_branch_matches_own_user_job(db_session):
    """user 1 自己有 Job '数据分析师' 时, 模糊匹配应回填到该 Job。"""
    job_other = Job(
        title="数据分析师", user_id=2, is_active=True,
        required_skills="", jd_text="",
    )
    job_self = Job(
        title="数据分析师", user_id=1, is_active=True,
        required_skills="", jd_text="",
    )
    db_session.add_all([job_other, job_self]); db_session.commit()
    self_id = job_self.id
    other_id = job_other.id

    svc = IntakeService(db_session, user_id=1)
    cand = svc.ensure_candidate(
        boss_id="boss-y", name="某乙", job_intention="数据分析师",
    )
    assert cand.job_id == self_id
    assert cand.job_id != other_id
