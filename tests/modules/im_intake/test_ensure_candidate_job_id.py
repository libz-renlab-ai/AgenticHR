"""F4 IntakeService.ensure_candidate job_id 回填 — spec-2026-05-15-job-binding T2.

覆盖三种情形:
1. 新建候选人时,job_intention 模糊匹配岗位写 job_id (现有行为不破坏).
2. 已存在 + job_id 非 NULL: 不覆盖 (first-write wins).
3. 已存在 + job_id 为 NULL (F3 老路径建的行): 用 fuzzy match 回填.
4. 已存在 + job_id 为 NULL + fuzzy match 落空: 保持 NULL.
"""
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.service import IntakeService
from app.modules.screening.models import Job


def _mk_job(db_session, *, title: str, user_id: int = 1) -> Job:
    j = Job(
        user_id=user_id, title=title, jd_text="",
        competency_model_status="none",
    )
    db_session.add(j); db_session.commit(); db_session.refresh(j)
    return j


def test_ensure_candidate_new_with_fuzzy_match_sets_job_id(db_session):
    """已有行为: 新建时 fuzzy match 写 job_id (回归保护)."""
    job = _mk_job(db_session, title="前端开发工程师")
    svc = IntakeService(db=db_session, user_id=1)
    c = svc.ensure_candidate(boss_id="b_t2a", name="A", job_intention="前端开发工程师")
    assert c.job_id == job.id, "exact match 应被 fuzzy matcher 命中"


def test_ensure_candidate_existing_with_job_id_not_overwritten(db_session):
    """first-write wins: 已存在且 job_id 非 NULL 时,二次调不覆盖."""
    job_a = _mk_job(db_session, title="后端")
    job_b = _mk_job(db_session, title="产品经理")
    # 先手工建一个 job_id=A 的 candidate (模拟 F3 路径已经写过)
    c = IntakeCandidate(
        user_id=1, boss_id="b_t2b", name="X",
        job_id=job_a.id, job_intention="后端",
        intake_status="collecting",
    )
    db_session.add(c); db_session.commit()
    svc = IntakeService(db=db_session, user_id=1)
    # 二次 ensure 用另一个 job_intention,不应覆盖
    c2 = svc.ensure_candidate(boss_id="b_t2b", name="X", job_intention="产品经理")
    assert c2.job_id == job_a.id, (
        f"first-write wins: 应保持 {job_a.id}, 实际 {c2.job_id}"
    )


def test_ensure_candidate_existing_with_null_job_id_backfills(db_session):
    """老 F3 数据 (job_id=NULL) 在 F4 register 时被 fuzzy match 回填."""
    job = _mk_job(db_session, title="产品经理")
    # 模拟 F3 老路径建的行: job_id=NULL
    c = IntakeCandidate(
        user_id=1, boss_id="b_t2c", name="Y",
        job_id=None, job_intention="",
        intake_status="collecting",
    )
    db_session.add(c); db_session.commit()
    svc = IntakeService(db=db_session, user_id=1)
    c2 = svc.ensure_candidate(boss_id="b_t2c", name="Y", job_intention="产品经理")
    assert c2.job_id == job.id, (
        f"NULL 应被 fuzzy match 回填为 {job.id}, 实际 {c2.job_id}"
    )


def test_ensure_candidate_existing_null_no_match_stays_null(db_session):
    """fuzzy match 落空时,job_id 保持 NULL,不报错."""
    _mk_job(db_session, title="产品经理")
    c = IntakeCandidate(
        user_id=1, boss_id="b_t2d", name="Z",
        job_id=None, job_intention="",
        intake_status="collecting",
    )
    db_session.add(c); db_session.commit()
    svc = IntakeService(db=db_session, user_id=1)
    # "区块链架构师" 跟 "产品经理" bigram 相似度远低于 0.7
    c2 = svc.ensure_candidate(boss_id="b_t2d", name="Z", job_intention="区块链架构师")
    assert c2.job_id is None, (
        f"fuzzy 落空时 NULL 保持,实际 {c2.job_id}"
    )


def test_ensure_candidate_existing_null_no_job_intention_stays_null(db_session):
    """job_id 为 NULL 但调用者没传 job_intention 时,不动 job_id."""
    _mk_job(db_session, title="产品经理")
    c = IntakeCandidate(
        user_id=1, boss_id="b_t2e", name="W",
        job_id=None, job_intention="",
        intake_status="collecting",
    )
    db_session.add(c); db_session.commit()
    svc = IntakeService(db=db_session, user_id=1)
    c2 = svc.ensure_candidate(boss_id="b_t2e", name="W", job_intention=None)
    assert c2.job_id is None
