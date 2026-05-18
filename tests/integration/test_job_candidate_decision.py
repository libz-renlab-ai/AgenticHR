"""spec 0429-D — PATCH /api/jobs/{job_id}/candidates/{candidate_id}/decision
+ GET /api/matching/passed-resumes/{job_id}?action=... 集成测试"""
import pytest
from sqlalchemy import text

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.templates import HARD_SLOT_KEYS
from app.modules.matching.decision_model import JobCandidateDecision
from app.modules.screening.models import Job


def _mk_candidate(session, user_id=1, boss_id="b1", name="候选人A",
                  phone="13800000001", education="本科",
                  school_tier="985", bachelor_school="清华大学"):
    c = IntakeCandidate(
        user_id=user_id, boss_id=boss_id, name=name,
        pdf_path="data/x.pdf", intake_status="collecting",
        education=education, school_tier=school_tier,
        bachelor_school=bachelor_school, phone=phone,
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


def _mk_job(session, **kw):
    defaults = dict(title="岗位", is_active=True, user_id=1, required_skills="")
    defaults.update(kw)
    j = Job(**defaults)
    session.add(j)
    session.commit()
    session.refresh(j)
    return j


# ── PATCH decision endpoint ─────────────────────────────────────────────────

class TestPatchDecision:
    def test_set_passed_creates_decision(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "passed"
        assert body["job_id"] == job.id
        assert body["candidate_id"] == cand.id
        # row 落库
        row = db_session.query(JobCandidateDecision).filter_by(
            job_id=job.id, candidate_id=cand.id,
        ).first()
        assert row is not None and row.action == "passed"

    def test_set_rejected(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "rejected"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "rejected"

    def test_set_null_clears_decision(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": None},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] is None
        assert db_session.query(JobCandidateDecision).count() == 0

    def test_upsert_replaces_action(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "rejected"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "rejected"
        # 仍只一行
        cnt = db_session.query(JobCandidateDecision).filter_by(
            job_id=job.id, candidate_id=cand.id
        ).count()
        assert cnt == 1

    def test_invalid_action_400(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "approved"},
        )
        assert resp.status_code == 400

    def test_other_user_job_404(self, client, db_session):
        cand = _mk_candidate(db_session, user_id=1)
        job = _mk_job(db_session, user_id=2)  # 别人岗位
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        assert resp.status_code == 404

    def test_other_user_candidate_404(self, client, db_session):
        cand = _mk_candidate(db_session, user_id=2)
        job = _mk_job(db_session, user_id=1)
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        assert resp.status_code == 404

    def test_nonexistent_job_404(self, client, db_session):
        cand = _mk_candidate(db_session)
        resp = client.patch(
            f"/api/jobs/999999/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        assert resp.status_code == 404

    def test_nonexistent_candidate_404(self, client, db_session):
        job = _mk_job(db_session)
        resp = client.patch(
            f"/api/jobs/{job.id}/candidates/999999/decision",
            json={"action": "passed"},
        )
        assert resp.status_code == 404


# ── list_matched_for_job 注入 job_action + 闸门过滤 ─────────────────────────

class TestPassedResumesGate:
    def test_default_returns_all_with_job_action(self, client, db_session):
        c1 = _mk_candidate(db_session, boss_id="b1", name="通过", phone="13800000001")
        c2 = _mk_candidate(db_session, boss_id="b2", name="淘汰", phone="13800000002")
        c3 = _mk_candidate(db_session, boss_id="b3", name="未决", phone="13800000003")
        job = _mk_job(db_session)
        client.patch(f"/api/jobs/{job.id}/candidates/{c1.id}/decision", json={"action": "passed"})
        client.patch(f"/api/jobs/{job.id}/candidates/{c2.id}/decision", json={"action": "rejected"})

        # spec 2026-05-15 Round 2: 路由默认 strict=True 仅返本岗位绑定的候选人;
        # 本测试 _mk_candidate 未绑 job_id → 用 show_all=true 走旧语义。
        resp = client.get(f"/api/matching/passed-resumes/{job.id}?show_all=true")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 3
        by_name = {i["name"]: i["job_action"] for i in items}
        assert by_name["通过"] == "passed"
        assert by_name["淘汰"] == "rejected"
        assert by_name["未决"] is None

    def test_action_passed_filter(self, client, db_session):
        c1 = _mk_candidate(db_session, boss_id="b1", name="通过", phone="13800000001")
        c2 = _mk_candidate(db_session, boss_id="b2", name="淘汰", phone="13800000002")
        c3 = _mk_candidate(db_session, boss_id="b3", name="未决", phone="13800000003")
        job = _mk_job(db_session)
        client.patch(f"/api/jobs/{job.id}/candidates/{c1.id}/decision", json={"action": "passed"})
        client.patch(f"/api/jobs/{job.id}/candidates/{c2.id}/decision", json={"action": "rejected"})

        resp = client.get(f"/api/matching/passed-resumes/{job.id}?action=passed&show_all=true")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["name"] == "通过"

    def test_action_undecided_filter(self, client, db_session):
        c1 = _mk_candidate(db_session, boss_id="b1", name="通过", phone="13800000001")
        c2 = _mk_candidate(db_session, boss_id="b2", name="未决", phone="13800000002")
        job = _mk_job(db_session)
        client.patch(f"/api/jobs/{job.id}/candidates/{c1.id}/decision", json={"action": "passed"})

        resp = client.get(f"/api/matching/passed-resumes/{job.id}?action=undecided&show_all=true")
        items = resp.json()
        assert len(items) == 1
        assert items[0]["name"] == "未决"

    def test_invalid_action_param_400(self, client, db_session):
        job = _mk_job(db_session)
        resp = client.get(f"/api/matching/passed-resumes/{job.id}?action=foo")
        assert resp.status_code == 400

    def test_decision_independent_per_job(self, client, db_session):
        cand = _mk_candidate(db_session)
        job_a = _mk_job(db_session, title="A")
        job_b = _mk_job(db_session, title="B")
        client.patch(f"/api/jobs/{job_a.id}/candidates/{cand.id}/decision", json={"action": "passed"})
        client.patch(f"/api/jobs/{job_b.id}/candidates/{cand.id}/decision", json={"action": "rejected"})

        # 同 candidate 跨多岗位 → 必走 show_all (strict 下一个 candidate 只能绑一个岗位)
        ra = client.get(f"/api/matching/passed-resumes/{job_a.id}?show_all=true")
        rb = client.get(f"/api/matching/passed-resumes/{job_b.id}?show_all=true")
        assert ra.json()[0]["job_action"] == "passed"
        assert rb.json()[0]["job_action"] == "rejected"


# ── 旧 PATCH /api/matching/results/{id}/action 同步写新表 ────────────────────

class TestLegacyMatchingResultPatchSyncs:
    def test_legacy_set_action_writes_decision_table(self, client, db_session):
        from app.modules.matching.models import MatchingResult
        from app.modules.resume.models import Resume
        from datetime import datetime, timezone

        # 1) candidate + promote 到 resume
        cand = _mk_candidate(db_session)
        resume = Resume(
            user_id=1, name=cand.name, phone=cand.phone, education=cand.education,
            ai_parsed="yes",
        )
        db_session.add(resume)
        db_session.commit()
        cand.promoted_resume_id = resume.id
        db_session.commit()

        job = _mk_job(db_session, competency_model={"hard_skills":[],"experience":{},"education":{},"job_level":""}, competency_model_status="approved")
        # 2) 已存 matching_result
        mr = MatchingResult(
            resume_id=resume.id, job_id=job.id, total_score=80,
            skill_score=80, experience_score=80, seniority_score=80,
            education_score=80, industry_score=80,
            hard_gate_passed=1, missing_must_haves="[]", evidence="{}", tags="[]",
            competency_hash="x", weights_hash="y",
            scored_at=datetime.now(timezone.utc),
        )
        db_session.add(mr)
        db_session.commit()

        # 3) 旧端点 PATCH passed
        resp = client.patch(f"/api/matching/results/{mr.id}/action", json={"action": "passed"})
        assert resp.status_code == 200

        # 4) 决策表也应有行
        d = db_session.query(JobCandidateDecision).filter_by(
            job_id=job.id, candidate_id=cand.id,
        ).first()
        assert d is not None and d.action == "passed"

        # 5) /passed-resumes 返 job_action (show_all 走旧语义, candidate 未绑 job_id)
        resp2 = client.get(f"/api/matching/passed-resumes/{job.id}?show_all=true")
        items = resp2.json()
        assert any(i["name"] == cand.name and i["job_action"] == "passed" for i in items)


# ── P1-a 级联删除: ON DELETE CASCADE 验证 ───────────────────────────────────

class TestCascadeDelete:
    def test_delete_candidate_cascades_decision(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        cand_id, job_id = cand.id, job.id
        client.patch(
            f"/api/jobs/{job_id}/candidates/{cand_id}/decision",
            json={"action": "passed"},
        )
        assert db_session.query(JobCandidateDecision).count() == 1
        # 直接 DB 删 candidate, FK CASCADE 应清 decision
        db_session.execute(
            text("DELETE FROM intake_candidates WHERE id=:id"), {"id": cand_id}
        )
        db_session.commit()
        assert db_session.query(JobCandidateDecision).filter_by(
            candidate_id=cand_id
        ).count() == 0

    def test_delete_job_cascades_decision(self, client, db_session):
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        cand_id, job_id = cand.id, job.id
        client.patch(
            f"/api/jobs/{job_id}/candidates/{cand_id}/decision",
            json={"action": "rejected"},
        )
        assert db_session.query(JobCandidateDecision).count() == 1
        db_session.execute(
            text("DELETE FROM jobs WHERE id=:id"), {"id": job_id}
        )
        db_session.commit()
        assert db_session.query(JobCandidateDecision).filter_by(
            job_id=job_id
        ).count() == 0

    def test_delete_user_cascades_decision(self, client, db_session):
        # 用 user_id=2 (conftest 已 seed) 避免影响 user_id=1 默认 client
        cand = _mk_candidate(db_session, user_id=2)
        job = _mk_job(db_session, user_id=2)
        # 直接落库决策行 (绕过 client 因 client 用 user_id=1)
        d = JobCandidateDecision(
            user_id=2, job_id=job.id, candidate_id=cand.id, action="passed",
        )
        db_session.add(d)
        db_session.commit()
        assert db_session.query(JobCandidateDecision).filter_by(user_id=2).count() == 1
        # 删 user 2; FK CASCADE 应同步清 candidate + decision
        db_session.execute(text("DELETE FROM users WHERE id=2"))
        db_session.commit()
        assert db_session.query(JobCandidateDecision).filter_by(user_id=2).count() == 0


# ── P1-b spec 0429-D Edge cases e2e ─────────────────────────────────────────

class TestSpecEdgeCases:
    def test_passed_then_promote_then_visible(self, client, db_session):
        """spec edge case 1: 候选人未 promote 被标 passed → 后续 promote → 仍可被约面试。"""
        from app.modules.resume.models import Resume
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        # 未 promote 时标 passed
        client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        # 现在做 promote (手工模拟 promote_to_resume 的关键副作用)
        resume = Resume(
            user_id=1, name=cand.name, phone=cand.phone, education=cand.education,
            ai_parsed="yes",
        )
        db_session.add(resume)
        db_session.commit()
        cand.promoted_resume_id = resume.id
        db_session.commit()
        # /passed-resumes?action=passed 仍返该候选人 (candidate 未绑 job_id, 加 show_all)
        resp = client.get(f"/api/matching/passed-resumes/{job.id}?action=passed&show_all=true")
        items = resp.json()
        assert any(i["id"] == cand.id and i["job_action"] == "passed" for i in items)

    def test_passed_then_abandoned_disappears(self, client, db_session):
        """spec edge case 2: passed 后 candidate intake_status 变 abandoned → 从下拉消失。"""
        cand = _mk_candidate(db_session)
        job = _mk_job(db_session)
        client.patch(
            f"/api/jobs/{job.id}/candidates/{cand.id}/decision",
            json={"action": "passed"},
        )
        # 标 abandoned
        cand.intake_status = "abandoned"
        db_session.commit()
        # passed 列表不应再含该 candidate (_complete_query 在 abandoned 状态下不会返)
        resp = client.get(f"/api/matching/passed-resumes/{job.id}?action=passed")
        items = resp.json()
        assert all(i["id"] != cand.id for i in items)

    def test_school_tier_tighten_then_loosen(self, client, db_session):
        """spec edge case 3: 学校等级门槛调严, passed 残留无害; 放宽后状态复活。"""
        # candidate=211 (低于 985), 用以测试调严到 985 时被滤出
        cand = _mk_candidate(db_session, school_tier="211", bachelor_school="苏州大学")
        job = _mk_job(db_session, school_tier_min="")
        cand_id, job_id = cand.id, job.id
        client.patch(
            f"/api/jobs/{job_id}/candidates/{cand_id}/decision",
            json={"action": "passed"},
        )
        # 当前门槛空, 候选人应在列表 (show_all 走旧语义, candidate 未绑 job_id)
        items = client.get(f"/api/matching/passed-resumes/{job_id}?show_all=true").json()
        assert any(i["id"] == cand_id for i in items)
        # 调严到 985 (211 不达标)
        job.school_tier_min = "985"
        db_session.commit()
        items_strict = client.get(f"/api/matching/passed-resumes/{job_id}?show_all=true").json()
        assert all(i["id"] != cand_id for i in items_strict)
        # decision 行仍在 (无害残留)
        d = db_session.query(JobCandidateDecision).filter_by(
            job_id=job_id, candidate_id=cand_id
        ).first()
        assert d is not None and d.action == "passed"
        # 放宽回去, candidate 复活带原 passed 状态
        job.school_tier_min = ""
        db_session.commit()
        items_back = client.get(f"/api/matching/passed-resumes/{job_id}?action=passed&show_all=true").json()
        assert any(i["id"] == cand_id and i["job_action"] == "passed" for i in items_back)

    def test_same_candidate_different_jobs_isolated(self, client, db_session):
        """spec edge case 4: 同 candidate 不同 job 决策互不冲突 (UNIQUE per pair)."""
        cand = _mk_candidate(db_session)
        job_a = _mk_job(db_session, title="A")
        job_b = _mk_job(db_session, title="B")
        client.patch(f"/api/jobs/{job_a.id}/candidates/{cand.id}/decision", json={"action": "passed"})
        client.patch(f"/api/jobs/{job_b.id}/candidates/{cand.id}/decision", json={"action": "rejected"})
        # 互不污染, 两行独立
        rows = db_session.query(JobCandidateDecision).filter_by(candidate_id=cand.id).all()
        assert len(rows) == 2
        assert {r.job_id: r.action for r in rows} == {job_a.id: "passed", job_b.id: "rejected"}
