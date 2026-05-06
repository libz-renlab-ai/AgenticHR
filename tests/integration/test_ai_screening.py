"""AI 智能筛选 集成测 — full HTTP + worker 流。worker 用 mock cli 跑。"""
import asyncio
from unittest.mock import patch

import pytest

from app.modules.ai_screening.models import ScreeningJob, ScreeningJobItem
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.matching.decision_model import JobCandidateDecision
from app.modules.matching.models import MatchingResult
from app.modules.resume.models import Resume
from app.modules.screening.models import Job


def _seed_pool(db_session, n=3, jd="后端"):
    job = Job(user_id=1, title="后端", jd_text=jd)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    cands = []
    for i in range(n):
        r = Resume(user_id=1, name=f"r{i}", pdf_path=f"data/r{i}.pdf")
        db_session.add(r)
        db_session.commit()
        c = IntakeCandidate(
            user_id=1, boss_id=f"b{i}", name=f"候选{i}",
            pdf_path=f"data/r{i}.pdf", promoted_resume_id=r.id,
        )
        db_session.add(c)
        db_session.commit()
        mr = MatchingResult(
            resume_id=r.id, job_id=job.id,
            total_score=70, skill_score=70, experience_score=70,
            seniority_score=70, education_score=70, industry_score=70,
            hard_gate_passed=1, competency_hash="h", weights_hash="w",
        )
        db_session.add(mr)
        db_session.commit()
        cands.append(c)
    return job, cands


class TestPreview:
    def test_preview_returns_eligible(self, client, db_session):
        job, cands = _seed_pool(db_session, n=3)
        r = client.get(f"/api/jobs/{job.id}/ai-screening/preview")
        assert r.status_code == 200
        body = r.json()
        assert body["eligible_count"] == 3
        assert body["has_running"] is False

    def test_preview_other_user_404(self, client, db_session):
        # job 属于 user 2
        job = Job(user_id=2, title="x")
        db_session.add(job)
        db_session.commit()
        r = client.get(f"/api/jobs/{job.id}/ai-screening/preview")
        assert r.status_code == 404


class TestStartCancel:
    def test_start_empty_pool_422(self, client, db_session):
        job = Job(user_id=1, title="后端", jd_text="x")
        db_session.add(job)
        db_session.commit()
        r = client.post(
            f"/api/jobs/{job.id}/ai-screening/start",
            json={"mode": "count", "threshold": 1},
        )
        assert r.status_code == 422

    def test_start_503_when_no_cli(self, client, db_session):
        job, _ = _seed_pool(db_session)
        with patch("app.modules.ai_screening.router.detect_claude_cli", return_value=False):
            r = client.post(
                f"/api/jobs/{job.id}/ai-screening/start",
                json={"mode": "count", "threshold": 1},
            )
        assert r.status_code == 503

    def test_start_then_run_worker_then_current_done(self, client, db_session, db_engine):
        """start endpoint → run_screening 显式调用 (绑定测试 engine) → done + 决策表落地"""
        from sqlalchemy.orm import sessionmaker

        job, cands = _seed_pool(db_session, n=2)

        async def fake_batch(jd_text, batch, *, timeout, handle=None):
            return [
                {"candidate_id": c["candidate_id"], "score": 80, "reason": "x"}
                for c in batch
            ]

        with patch(
            "app.modules.ai_screening.router.detect_claude_cli", return_value=True
        ), patch(
            "app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch
        ), patch(
            "app.modules.ai_screening.worker.spawn", return_value=None
        ):
            r = client.post(
                f"/api/jobs/{job.id}/ai-screening/start",
                json={"mode": "count", "threshold": 1},
            )
            assert r.status_code == 200
            sj_id = r.json()["screening_job_id"]

            # worker 用测试 engine 显式跑
            from app.modules.ai_screening import worker as wk
            test_factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
            asyncio.new_event_loop().run_until_complete(
                wk.run_screening(sj_id, session_factory=test_factory)
            )

        # 拉 current
        r2 = client.get(f"/api/jobs/{job.id}/ai-screening/current")
        body = r2.json()
        assert body["status"] == "done"
        assert body["total"] == 2

        r3 = client.get(f"/api/ai-screening/{sj_id}/items")
        body = r3.json()
        assert len(body["items"]) == 2
        assert body["items"][0]["score"] == 80

        # 决策表落 1 条 (threshold=1) — 用新 session 查避免缓存
        from sqlalchemy.orm import Session as SessionCls
        s = SessionCls(bind=db_engine)
        decisions = s.query(JobCandidateDecision).filter_by(
            job_id=job.id, action="passed"
        ).all()
        assert len(decisions) == 1
        s.close()

    def test_already_running_409(self, client, db_session):
        job, cands = _seed_pool(db_session, n=2)
        # 先手动建一个 running
        sj = ScreeningJob(
            user_id=1, job_id=job.id, mode="count", threshold=1,
            status="running", total=2, processed=0,
        )
        db_session.add(sj)
        db_session.commit()
        with patch(
            "app.modules.ai_screening.router.detect_claude_cli", return_value=True
        ):
            r = client.post(
                f"/api/jobs/{job.id}/ai-screening/start",
                json={"mode": "count", "threshold": 1},
            )
        assert r.status_code == 409

    def test_cancel_running(self, client, db_session):
        job, _ = _seed_pool(db_session, n=2)
        sj = ScreeningJob(
            user_id=1, job_id=job.id, mode="count", threshold=1,
            status="running", total=2, processed=0,
        )
        db_session.add(sj)
        db_session.commit()
        r = client.post(f"/api/ai-screening/{sj.id}/cancel")
        assert r.status_code == 200
        db_session.expire_all()
        sj2 = db_session.query(ScreeningJob).filter_by(id=sj.id).first()
        assert sj2.cancel_requested == 1

    def test_cancel_other_user_404(self, client, db_session):
        # 创建 user 2 的 job + screening_job
        job = Job(user_id=2, title="x", jd_text="y")
        db_session.add(job)
        db_session.commit()
        sj = ScreeningJob(
            user_id=2, job_id=job.id, mode="count", threshold=1,
            status="running", total=1, processed=0,
        )
        db_session.add(sj)
        db_session.commit()
        r = client.post(f"/api/ai-screening/{sj.id}/cancel")
        assert r.status_code == 404


class TestCurrent:
    def test_idle_when_no_history(self, client, db_session):
        job = Job(user_id=1, title="x", jd_text="y")
        db_session.add(job)
        db_session.commit()
        r = client.get(f"/api/jobs/{job.id}/ai-screening/current")
        assert r.status_code == 200
        assert r.json()["status"] == "idle"


class TestItemsList:
    def test_items_include_decision_action(self, client, db_session):
        job, cands = _seed_pool(db_session, n=2)
        sj = ScreeningJob(
            user_id=1, job_id=job.id, mode="count", threshold=1,
            status="done", total=2, processed=2,
        )
        db_session.add(sj)
        db_session.commit()
        for i, c in enumerate(cands):
            it = ScreeningJobItem(
                screening_job_id=sj.id, candidate_id=c.id,
                pdf_path=c.pdf_path or "", score=90 - i * 10,
                reason=f"r{i}", pass_flag=1 if i == 0 else 0,
            )
            db_session.add(it)
        # 写一个决策
        db_session.add(JobCandidateDecision(
            user_id=1, job_id=job.id, candidate_id=cands[0].id, action="passed",
        ))
        db_session.commit()
        r = client.get(f"/api/ai-screening/{sj.id}/items")
        body = r.json()
        assert body["mode"] == "count"
        assert body["threshold"] == 1
        assert body["items"][0]["candidate_id"] == cands[0].id
        assert body["items"][0]["score"] == 90
        assert body["items"][0]["decision_action"] == "passed"
        assert body["items"][1]["decision_action"] is None
