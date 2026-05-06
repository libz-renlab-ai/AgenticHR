"""ai_screening service 单测。"""
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
from app.modules.ai_screening.models import ScreeningJob, ScreeningJobItem
from app.modules.ai_screening.service import ScreeningError
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.matching.decision_model import JobCandidateDecision
from app.modules.matching.models import MatchingResult
from app.modules.resume.models import Resume
from app.modules.screening.models import Job


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(text(
        "INSERT INTO users (id, username, password_hash, display_name, is_active, daily_cap) "
        "VALUES (1,'u1','x','U1',1,1000), (2,'u2','x','U2',1,1000)"
    ))
    s.commit()
    yield s
    s.close()


def _seed_job(db, user_id=1, title="后端") -> Job:
    j = Job(user_id=user_id, title=title)
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _seed_resume(db, user_id=1, name="r1") -> Resume:
    r = Resume(user_id=user_id, name=name, pdf_path=f"data/{name}.pdf")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _seed_candidate_with_resume(db, user_id=1, name="张三", pdf="x.pdf") -> tuple[IntakeCandidate, Resume]:
    r = _seed_resume(db, user_id=user_id, name=name)
    c = IntakeCandidate(
        user_id=user_id, boss_id=f"b_{name}", name=name,
        pdf_path=pdf, promoted_resume_id=r.id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c, r


def _seed_match(db, resume_id, job_id, hard_pass=1, total=80.0) -> MatchingResult:
    mr = MatchingResult(
        resume_id=resume_id, job_id=job_id,
        total_score=total, skill_score=80, experience_score=80,
        seniority_score=80, education_score=80, industry_score=80,
        hard_gate_passed=hard_pass,
        competency_hash="h1", weights_hash="w1",
    )
    db.add(mr)
    db.commit()
    return mr


# --- preview ---

class TestPreview:
    def test_empty_pool_zero(self, db):
        job = _seed_job(db)
        r = svc.preview(db, user_id=1, job_id=job.id)
        assert r["eligible_count"] == 0
        assert r["has_running"] is False

    def test_counts_hard_pass_only(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="pass1")
        c2, r2 = _seed_candidate_with_resume(db, name="pass2")
        c3, r3 = _seed_candidate_with_resume(db, name="fail3")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        _seed_match(db, r2.id, job.id, hard_pass=1)
        _seed_match(db, r3.id, job.id, hard_pass=0)
        r = svc.preview(db, user_id=1, job_id=job.id)
        assert r["eligible_count"] == 2

    def test_excludes_rejected(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="p1")
        c2, r2 = _seed_candidate_with_resume(db, name="p2")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        _seed_match(db, r2.id, job.id, hard_pass=1)
        db.add(JobCandidateDecision(
            user_id=1, job_id=job.id, candidate_id=c2.id, action="rejected"
        ))
        db.commit()
        r = svc.preview(db, user_id=1, job_id=job.id)
        assert r["eligible_count"] == 1

    def test_unknown_job_404(self, db):
        with pytest.raises(ScreeningError) as e:
            svc.preview(db, user_id=1, job_id=999)
        assert e.value.code == "job_not_found"

    def test_other_user_job_404(self, db):
        job = _seed_job(db, user_id=2)
        with pytest.raises(ScreeningError) as e:
            svc.preview(db, user_id=1, job_id=job.id)
        assert e.value.code == "job_not_found"


# --- start ---

class TestStart:
    def test_creates_running_job_with_items(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        c2, r2 = _seed_candidate_with_resume(db, name="b")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        _seed_match(db, r2.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        assert sj.status == "running"
        assert sj.total == 2
        assert sj.processed == 0
        items = db.query(ScreeningJobItem).filter_by(screening_job_id=sj.id).all()
        assert len(items) == 2
        assert {it.candidate_id for it in items} == {c1.id, c2.id}

    def test_empty_pool_raises(self, db):
        job = _seed_job(db)
        with pytest.raises(ScreeningError) as e:
            svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        assert e.value.code == "empty_pool"

    def test_already_running_raises(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        with pytest.raises(ScreeningError) as e:
            svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        assert e.value.code == "already_running"

    def test_count_too_large_raises(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        with pytest.raises(ScreeningError) as e:
            svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=10)
        assert e.value.code == "invalid_threshold"

    def test_ratio_over_100_raises(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        with pytest.raises(ScreeningError) as e:
            svc.start(db, user_id=1, job_id=job.id, mode="ratio", threshold=200)
        assert e.value.code == "invalid_threshold"


# --- cancel ---

class TestCancel:
    def test_sets_cancel_requested(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        result = svc.cancel(db, user_id=1, screening_job_id=sj.id)
        assert result.cancel_requested == 1

    def test_other_user_404(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        with pytest.raises(ScreeningError) as e:
            svc.cancel(db, user_id=2, screening_job_id=sj.id)
        assert e.value.code == "not_found"

    def test_not_running_raises(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        sj.status = "done"
        db.commit()
        with pytest.raises(ScreeningError) as e:
            svc.cancel(db, user_id=1, screening_job_id=sj.id)
        assert e.value.code == "not_running"


# --- current ---

class TestCurrent:
    def test_returns_none_when_no_history(self, db):
        job = _seed_job(db)
        assert svc.current(db, user_id=1, job_id=job.id) is None

    def test_returns_running_priority(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        result = svc.current(db, user_id=1, job_id=job.id)
        assert result.id == sj.id
        assert result.status == "running"

    def test_returns_latest_finished_when_no_running(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        sj.status = "done"
        db.commit()
        result = svc.current(db, user_id=1, job_id=job.id)
        assert result.id == sj.id
        assert result.status == "done"


# --- list_items ---

class TestListItems:
    def test_returns_items_with_decisions(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        c2, r2 = _seed_candidate_with_resume(db, name="b")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        _seed_match(db, r2.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        # 写分数模拟 worker 跑完
        items = db.query(ScreeningJobItem).filter_by(screening_job_id=sj.id).order_by(ScreeningJobItem.candidate_id).all()
        items[0].score = 90
        items[0].reason = "好"
        items[0].pass_flag = 1
        items[1].score = 70
        items[1].reason = "一般"
        db.commit()
        # decision: c1 已 pass
        db.add(JobCandidateDecision(user_id=1, job_id=job.id, candidate_id=c1.id, action="passed"))
        db.commit()

        sj2, out = svc.list_items(db, user_id=1, screening_job_id=sj.id)
        assert sj2.id == sj.id
        # 排序: score DESC
        assert out[0]["candidate_id"] == c1.id
        assert out[0]["score"] == 90
        assert out[0]["pass_flag"] == 1
        assert out[0]["decision_action"] == "passed"
        assert out[1]["candidate_id"] == c2.id
        assert out[1]["score"] == 70
        assert out[1]["decision_action"] is None

    def test_other_user_404(self, db):
        job = _seed_job(db)
        c1, r1 = _seed_candidate_with_resume(db, name="a")
        _seed_match(db, r1.id, job.id, hard_pass=1)
        sj = svc.start(db, user_id=1, job_id=job.id, mode="count", threshold=1)
        with pytest.raises(ScreeningError) as e:
            svc.list_items(db, user_id=2, screening_job_id=sj.id)
        assert e.value.code == "not_found"
