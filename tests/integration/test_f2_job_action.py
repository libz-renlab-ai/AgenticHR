"""F2 per-(resume, job) action 测试.

测试 PATCH /api/matching/results/{id}/action 和
GET /api/matching/passed-resumes/{job_id} 端点。

PR4 起 /passed-resumes/{job_id} 行为改为: 四项齐全 ∩ 学历门槛 ∩ 院校等级门槛
(基于 IntakeCandidate)。本文件最后段使用新逻辑。
"""
import pytest
from datetime import datetime, timezone

from app.modules.matching.models import MatchingResult
from app.modules.resume.models import Resume
from app.modules.screening.models import Job
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.templates import HARD_SLOT_KEYS


def _mk_intake_complete(session, user_id=1, boss_id="b1", name="候选人A",
                        education="本科", school_tier="985",
                        bachelor_school="清华大学", phone="13800000001",
                        email="a@example.com"):
    c = IntakeCandidate(
        user_id=user_id, boss_id=boss_id, name=name,
        pdf_path="data/x.pdf", intake_status="collecting",
        education=education, school_tier=school_tier,
        bachelor_school=bachelor_school,
        phone=phone, email=email,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    for key in HARD_SLOT_KEYS:
        session.add(IntakeSlot(
            candidate_id=c.id, slot_key=key, slot_category="hard",
            value="filled", ask_count=1,
        ))
    session.commit()
    return c


def _mk_resume(session, **kw):
    defaults = dict(
        name="候选人A", phone="13800000001", skills="Python",
        work_years=3, education="本科", seniority="中级",
        ai_parsed="yes", source="manual", user_id=1,
    )
    defaults.update(kw)
    r = Resume(**defaults)
    session.add(r)
    session.commit()
    return r


def _mk_job(session, **kw):
    defaults = dict(
        title="工程师岗位", is_active=True, required_skills="",
        competency_model={
            "hard_skills": [],
            "experience": {"years_min": 0},
            "education": {},
            "job_level": "中级",
        },
        competency_model_status="approved",
        user_id=1,
    )
    defaults.update(kw)
    j = Job(**defaults)
    session.add(j)
    session.commit()
    return j


def _mk_result(session, resume_id, job_id, **kw):
    defaults = dict(
        resume_id=resume_id, job_id=job_id,
        total_score=75.0, skill_score=75.0,
        experience_score=80.0, seniority_score=80.0,
        education_score=80.0, industry_score=80.0,
        hard_gate_passed=1, missing_must_haves="[]",
        evidence="{}", tags='["高匹配"]',
        competency_hash="hash_c", weights_hash="hash_w",
        scored_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    row = MatchingResult(**defaults)
    session.add(row)
    session.commit()
    return row


# ── PATCH action tests ──────────────────────────────────────────────────────

def test_set_action_passed(client, db_session):
    """PATCH passed → job_action='passed'; GET results 中也能看到。"""
    resume = _mk_resume(db_session)
    job = _mk_job(db_session)
    result = _mk_result(db_session, resume.id, job.id)
    assert result.job_action is None

    resp = client.patch(f"/api/matching/results/{result.id}/action", json={"action": "passed"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == result.id
    assert data["job_action"] == "passed"

    # GET results 应包含 job_action
    resp2 = client.get(f"/api/matching/results?job_id={job.id}")
    assert resp2.status_code == 200
    items = resp2.json()["items"]
    assert len(items) == 1
    assert items[0]["job_action"] == "passed"


def test_set_action_rejected(client, db_session):
    """PATCH rejected → job_action='rejected'。"""
    resume = _mk_resume(db_session, name="候选人B")
    job = _mk_job(db_session, title="另一岗位")
    result = _mk_result(db_session, resume.id, job.id)

    resp = client.patch(f"/api/matching/results/{result.id}/action", json={"action": "rejected"})
    assert resp.status_code == 200
    assert resp.json()["job_action"] == "rejected"


def test_set_action_clear_to_null(client, db_session):
    """先设为 passed，再 PATCH null → job_action 清空。"""
    resume = _mk_resume(db_session, name="候选人C")
    job = _mk_job(db_session, title="清空测试岗位")
    result = _mk_result(db_session, resume.id, job.id, job_action="passed")

    resp = client.patch(f"/api/matching/results/{result.id}/action", json={"action": None})
    assert resp.status_code == 200
    assert resp.json()["job_action"] is None

    # GET results 也应看到 null
    resp2 = client.get(f"/api/matching/results?job_id={job.id}")
    assert resp2.json()["items"][0]["job_action"] is None


def test_set_action_invalid_value_returns_400(client, db_session):
    """非法 action 值 → 400 Bad Request。"""
    resume = _mk_resume(db_session, name="候选人D")
    job = _mk_job(db_session, title="无效值测试岗位")
    result = _mk_result(db_session, resume.id, job.id)

    resp = client.patch(f"/api/matching/results/{result.id}/action", json={"action": "approved"})
    assert resp.status_code == 400


def test_set_action_nonexistent_result_returns_404(client, db_session):
    """不存在的 result_id → 404。"""
    resp = client.patch("/api/matching/results/999999/action", json={"action": "passed"})
    assert resp.status_code == 404


def test_set_action_scoped_to_job(client, db_session):
    """一个简历在岗位A标记 passed 不影响岗位B的 job_action。"""
    resume = _mk_resume(db_session, name="跨岗位候选人")
    job_a = _mk_job(db_session, title="岗位A")
    job_b = _mk_job(db_session, title="岗位B")
    result_a = _mk_result(db_session, resume.id, job_a.id)
    result_b = _mk_result(db_session, resume.id, job_b.id)

    # 只标记岗位A
    client.patch(f"/api/matching/results/{result_a.id}/action", json={"action": "passed"})

    # 岗位A的结果应为 passed
    resp_a = client.get(f"/api/matching/results?job_id={job_a.id}")
    assert resp_a.json()["items"][0]["job_action"] == "passed"

    # 岗位B的结果应仍为 null
    resp_b = client.get(f"/api/matching/results?job_id={job_b.id}")
    assert resp_b.json()["items"][0]["job_action"] is None


# ── GET passed-resumes tests ─────────────────────────────────────────────────

def test_list_passed_for_job_returns_only_qualified(client, db_session):
    """GET /passed-resumes/{job_id} 返回简历库 ∩ 学历门槛 ∩ 院校门槛 (PR4)"""
    # 三个候选人均"四项齐全"
    qualified = _mk_intake_complete(
        db_session, boss_id="b1", name="通过候选人", phone="13800000002",
        education="硕士", school_tier="985",
    )
    rej_edu = _mk_intake_complete(
        db_session, boss_id="b2", name="学历不够", phone="13800000003",
        education="本科", school_tier="985",
    )
    rej_tier = _mk_intake_complete(
        db_session, boss_id="b3", name="院校不够", phone="13800000004",
        education="硕士", school_tier="qs_top200",
    )
    job = _mk_job(db_session, title="硕士+985岗位",
                  education_min="硕士", school_tier_min="985")

    # spec 2026-05-15 Round 2: 路由默认 strict=True 仅返本岗位绑定候选人,
    # 这里候选人 job_id=NULL → 加 show_all=true 走旧"硬筛通过即出"行为
    resp = client.get(f"/api/matching/passed-resumes/{job.id}?show_all=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "通过候选人"
    assert data[0]["phone"] == "13800000002"


def test_list_passed_for_job_empty_when_none(client, db_session):
    """无候选人 / 无人达门槛 → 空列表"""
    job = _mk_job(db_session, title="空岗位")
    resp = client.get(f"/api/matching/passed-resumes/{job.id}")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_passed_for_job_includes_email(client, db_session):
    """返回结果包含 email 字段"""
    _mk_intake_complete(db_session, boss_id="b1", name="有邮箱候选人",
                        phone="13800000005", email="test@example.com")
    job = _mk_job(db_session, title="邮箱测试岗位")  # 无门槛

    resp = client.get(f"/api/matching/passed-resumes/{job.id}?show_all=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["email"] == "test@example.com"


def test_list_passed_for_job_excludes_incomplete_intake(client, db_session):
    """未完成四项的候选人不出现"""
    c = IntakeCandidate(
        user_id=1, boss_id="bx", name="未完成", pdf_path="data/x.pdf",
        intake_status="collecting",
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    # 仅填 1 个 hard slot
    db_session.add(IntakeSlot(
        candidate_id=c.id, slot_key="arrival_date", slot_category="hard",
        value="2026-05-01", ask_count=1,
    ))
    db_session.commit()
    job = _mk_job(db_session, title="不完整测试岗位")

    resp = client.get(f"/api/matching/passed-resumes/{job.id}")
    assert resp.status_code == 200
    assert resp.json() == []


# ── job_action persists across score (UPSERT) ────────────────────────────────

def test_job_action_preserved_after_upsert(db_session):
    """手动验证：UPSERT 时不覆盖 job_action 字段（service.score_pair 不写 job_action）。"""
    resume = _mk_resume(db_session, name="UPSERT候选人")
    job = _mk_job(db_session, title="UPSERT岗位")
    result = _mk_result(db_session, resume.id, job.id, job_action="passed")

    # 模拟 UPSERT：只修改分数，不触碰 job_action
    result.total_score = 99.0
    db_session.commit()
    db_session.refresh(result)

    assert result.job_action == "passed", "UPSERT 后 job_action 不应被清除"
