"""Regression: POST /api/scheduling/interviews 必须校验 job_id 与 interviewer_id
的归属, 否则用户 A 可创建一条 Interview 把自己 user_id 关联到用户 B 的 job/interviewer。

下游影响:
- 用户 A 通过 Interview.job_id (跨账户) 触发 AI 面评, 读 user B 的 competency_model
- 用户 A 通过 Interview.interviewer_id (跨账户) 让 user B 的面试官出现在自己面试列表里

同源 leak 系列: b867297 / 6ede54e / e115bab / ea1de2a。
"""
from datetime import datetime, timezone, timedelta

from app.modules.scheduling.models import Interviewer
from app.modules.screening.models import Job
from app.modules.resume.models import Resume


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed_other_user_interviewer(db_session) -> int:
    iv = Interviewer(
        name="他人面试官", phone="", email="other@x.com", department="",
        feishu_user_id="ou_other", user_id=2,
    )
    db_session.add(iv); db_session.commit()
    return iv.id


def _seed_other_user_job(db_session) -> int:
    job = Job(
        title="他人岗位", user_id=2, is_active=True, required_skills="Python",
        competency_model={"hard_skills": []}, competency_model_status="approved",
        education_min="本科", jd_text="",
    )
    db_session.add(job); db_session.commit()
    return job.id


def _create_own_resume(client) -> int:
    resp = client.post("/api/resumes/", json={
        "name": "归属测试候选人", "phone": "13900000001",
    })
    return resp.json()["id"]


def _create_own_interviewer(client) -> int:
    resp = client.post("/api/scheduling/interviewers", json={
        "name": "自家面试官", "feishu_user_id": "ou_self",
    })
    return resp.json()["id"]


def _future_slot():
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    start = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    end = tomorrow.replace(hour=11, minute=0, second=0, microsecond=0)
    return start, end


def test_create_interview_blocks_cross_user_interviewer(client, db_session):
    """user 1 不能创建 Interview 把 user 2 的 interviewer 关联进来 → 404"""
    resume_id = _create_own_resume(client)
    other_interviewer_id = _seed_other_user_interviewer(db_session)
    start, end = _future_slot()

    resp = client.post("/api/scheduling/interviews", json={
        "resume_id": resume_id,
        "interviewer_id": other_interviewer_id,
        "start_time": _iso(start),
        "end_time": _iso(end),
    })
    assert resp.status_code == 404, f"应 404 拒绝跨账户面试官, 实得 {resp.status_code}: {resp.text}"


def test_create_interview_blocks_cross_user_job(client, db_session):
    """user 1 不能创建 Interview 把 user 2 的 job_id 关联进来 → 404"""
    resume_id = _create_own_resume(client)
    own_interviewer_id = _create_own_interviewer(client)
    other_job_id = _seed_other_user_job(db_session)
    start, end = _future_slot()

    resp = client.post("/api/scheduling/interviews", json={
        "resume_id": resume_id,
        "interviewer_id": own_interviewer_id,
        "job_id": other_job_id,
        "start_time": _iso(start),
        "end_time": _iso(end),
    })
    assert resp.status_code == 404, f"应 404 拒绝跨账户岗位, 实得 {resp.status_code}: {resp.text}"


def test_create_interview_allows_own_job_and_interviewer(client, db_session):
    """同 user 的 job + interviewer 应正常 201 (确保 fix 没把合法路径误杀)"""
    resume_id = _create_own_resume(client)
    own_interviewer_id = _create_own_interviewer(client)
    # 用户 1 自家 job
    own_job = Job(
        title="自家岗位", user_id=1, is_active=True, required_skills="Python",
        competency_model={"hard_skills": []}, competency_model_status="approved",
        education_min="本科", jd_text="",
    )
    db_session.add(own_job); db_session.commit()

    start, end = _future_slot()
    resp = client.post("/api/scheduling/interviews", json={
        "resume_id": resume_id,
        "interviewer_id": own_interviewer_id,
        "job_id": own_job.id,
        "start_time": _iso(start),
        "end_time": _iso(end),
    })
    assert resp.status_code == 201, f"自家资源应允许, 实得 {resp.status_code}: {resp.text}"
