"""worker 集成测 — mock cli_runner.run_claude_batch。"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.modules.auth.models  # noqa: F401
import app.modules.resume.models  # noqa: F401
import app.modules.screening.models  # noqa: F401
import app.modules.im_intake.candidate_model  # noqa: F401
import app.modules.im_intake.models  # noqa: F401
import app.modules.matching.models  # noqa: F401
import app.modules.matching.decision_model  # noqa: F401
import app.modules.ai_screening.models  # noqa: F401

from app.modules.ai_screening import service as svc
from app.modules.ai_screening import worker as wk
from app.modules.ai_screening.cli_runner import CliError
from app.modules.ai_screening.models import ScreeningJob, ScreeningJobItem
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.matching.decision_model import JobCandidateDecision
from app.modules.matching.models import MatchingResult
from app.modules.resume.models import Resume
from app.modules.screening.models import Job


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = Session()
    s.execute(text(
        "INSERT INTO users (id, username, password_hash, display_name, is_active, daily_cap) "
        "VALUES (1,'u1','x','U1',1,1000)"
    ))
    s.commit()
    s.close()
    yield Session


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


def _seed(db, n_pass=3, jd="后端工程师 5 年 Java"):
    job = Job(user_id=1, title="后端", jd_text=jd)
    db.add(job)
    db.commit()
    db.refresh(job)
    cands = []
    for i in range(n_pass):
        r = Resume(user_id=1, name=f"r{i}", pdf_path=f"data/r{i}.pdf")
        db.add(r)
        db.commit()
        c = IntakeCandidate(
            user_id=1, boss_id=f"b{i}", name=f"候选{i}",
            pdf_path=f"data/r{i}.pdf", promoted_resume_id=r.id,
        )
        db.add(c)
        db.commit()
        mr = MatchingResult(
            resume_id=r.id, job_id=job.id, total_score=70,
            skill_score=70, experience_score=70, seniority_score=70,
            education_score=70, industry_score=70, hard_gate_passed=1,
            competency_hash="h", weights_hash="w",
        )
        db.add(mr)
        db.commit()
        cands.append(c)
    return job, cands


@pytest.mark.asyncio
async def test_single_batch_finalize_writes_decisions(session_factory, db):
    job, cands = _seed(db, n_pass=3)
    sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=2)
    db.close()

    async def fake_batch(jd_text, batch, *, timeout, handle=None, binary_path=None):
        return [
            {"candidate_id": c["candidate_id"], "score": 90 - i * 10, "reason": f"r{i}"}
            for i, c in enumerate(batch)
        ]

    with patch("app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch):
        await wk.run_screening(sj.id, session_factory=session_factory)

    s2 = session_factory()
    sj2 = s2.query(ScreeningJob).filter_by(id=sj.id).first()
    assert sj2.status == "done"
    assert sj2.processed == 3

    items = s2.query(ScreeningJobItem).filter_by(screening_job_id=sj.id).order_by(
        ScreeningJobItem.score.desc()
    ).all()
    assert items[0].score == 90
    assert items[0].pass_flag == 1
    assert items[1].pass_flag == 1  # threshold=2
    assert items[2].pass_flag == 0

    decisions = s2.query(JobCandidateDecision).filter_by(job_id=job.id, action="passed").all()
    assert len(decisions) == 2
    s2.close()


@pytest.mark.asyncio
async def test_ratio_mode(session_factory, db):
    job, cands = _seed(db, n_pass=4)
    sj = svc.start(db, user_id=1, job_id=job.id, mode="ratio", threshold=50)  # 50% of 4 = 2
    db.close()

    async def fake_batch(jd_text, batch, *, timeout, handle=None, binary_path=None):
        return [
            {"candidate_id": c["candidate_id"], "score": 80 - i * 10, "reason": "x"}
            for i, c in enumerate(batch)
        ]

    with patch("app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch):
        await wk.run_screening(sj.id, session_factory=session_factory)

    s2 = session_factory()
    decisions = s2.query(JobCandidateDecision).filter_by(job_id=job.id, action="passed").all()
    assert len(decisions) == 2
    s2.close()


@pytest.mark.asyncio
async def test_multi_batch_runs_finalist_round(session_factory, db):
    """11 candidates → 2 batches Stage1 + finalist round."""
    job, cands = _seed(db, n_pass=11)
    sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=3)
    db.close()

    call_log = []

    async def fake_batch(jd_text, batch, *, timeout, handle=None, binary_path=None):
        call_log.append(len(batch))
        # Stage 1 各批返 80~70; 决赛批返 95~85 让 score 升, 验证决赛能覆盖
        return [
            {"candidate_id": c["candidate_id"], "score": 80 - i, "reason": "x"}
            for i, c in enumerate(batch)
        ]

    with patch("app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch):
        await wk.run_screening(sj.id, batch_size=10, session_factory=session_factory)

    # Stage1: 10 + 1; 决赛: min(3+5, 10) = 8 进决赛, 1 批
    assert call_log[0] == 10
    assert call_log[1] == 1
    assert len(call_log) == 3
    assert call_log[2] == 8

    s2 = session_factory()
    sj2 = s2.query(ScreeningJob).filter_by(id=sj.id).first()
    assert sj2.status == "done"
    decisions = s2.query(JobCandidateDecision).filter_by(job_id=job.id, action="passed").all()
    assert len(decisions) == 3
    s2.close()


@pytest.mark.asyncio
async def test_cancel_mid_run(session_factory, db):
    job, cands = _seed(db, n_pass=15)
    sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=5)
    db.close()

    call_count = [0]

    async def fake_batch(jd_text, batch, *, timeout, handle=None, binary_path=None):
        call_count[0] += 1
        # 第一批跑完后 HR 取消
        if call_count[0] == 1:
            s = session_factory()
            sj2 = s.query(ScreeningJob).filter_by(id=sj.id).first()
            sj2.cancel_requested = 1
            s.commit()
            s.close()
        return [
            {"candidate_id": c["candidate_id"], "score": 80, "reason": "x"}
            for c in batch
        ]

    with patch("app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch):
        await wk.run_screening(sj.id, batch_size=10, session_factory=session_factory)

    s2 = session_factory()
    sj2 = s2.query(ScreeningJob).filter_by(id=sj.id).first()
    assert sj2.status == "cancelled"
    # 不应有决策表落地 (取消前未 finalize)
    decisions = s2.query(JobCandidateDecision).filter_by(job_id=job.id).all()
    assert len(decisions) == 0
    s2.close()


@pytest.mark.asyncio
async def test_cli_error_marks_batch_error_continues(session_factory, db):
    job, cands = _seed(db, n_pass=3)
    sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
    db.close()

    async def fake_batch(jd_text, batch, *, timeout, handle=None, binary_path=None):
        raise CliError("simulated failure")

    with patch("app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch):
        await wk.run_screening(sj.id, session_factory=session_factory)

    s2 = session_factory()
    sj2 = s2.query(ScreeningJob).filter_by(id=sj.id).first()
    # 全失败仍走 finalize → done (但所有 item 都 score=0 + error, pass=0)
    assert sj2.status == "done"
    items = s2.query(ScreeningJobItem).filter_by(screening_job_id=sj.id).all()
    for it in items:
        assert it.error == "simulated failure"
        assert it.score == 0
        assert it.pass_flag == 0
    decisions = s2.query(JobCandidateDecision).filter_by(job_id=job.id).all()
    assert len(decisions) == 0
    s2.close()


@pytest.mark.asyncio
async def test_finalize_tie_breaker_lower_id(session_factory, db):
    job, cands = _seed(db, n_pass=3)
    sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=2)
    db.close()

    async def fake_batch(jd_text, batch, *, timeout, handle=None, binary_path=None):
        # 全 80 分 → tie-break 用 candidate_id ASC
        return [
            {"candidate_id": c["candidate_id"], "score": 80, "reason": "x"}
            for c in batch
        ]

    with patch("app.modules.ai_screening.worker.run_claude_batch", side_effect=fake_batch):
        await wk.run_screening(sj.id, session_factory=session_factory)

    s2 = session_factory()
    cand_ids_sorted = sorted(c.id for c in cands)
    decisions = s2.query(JobCandidateDecision).filter_by(job_id=job.id, action="passed").all()
    decided = sorted(d.candidate_id for d in decisions)
    assert decided == cand_ids_sorted[:2]
    s2.close()
