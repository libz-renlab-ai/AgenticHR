"""service.create_job 5 道校验门."""
import pytest
from datetime import datetime, timezone, timedelta

from app.database import Base, engine, SessionLocal


@pytest.fixture(autouse=True)
def setup_tables():
    """建表 + seed FK 链上游行（resumes/interviewers id=1，给所有 Interview 用）.

    备注：Interview.resume_id / interviewer_id 是 NOT NULL FK，sqlite
    PRAGMA foreign_keys=ON 下需要上游行存在。T3 fixture 的 _make_interview
    一律使用 resume_id=1 / interviewer_id=1，这里统一在 setup 里 merge 占位行。
    """
    Base.metadata.create_all(bind=engine)
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interviewer
    from app.modules.interview_eval.models import InterviewEvalJob
    db = SessionLocal()
    try:
        db.merge(Resume(id=1, name="dummy_resume_t3"))
        db.merge(Interviewer(id=1, name="dummy_interviewer_t3"))
        # 清理上一次 run 残留的 InterviewEvalJob（test.db 持久；
        # interview_id 范围 1001-1006 是 T3 用例独占）
        db.query(InterviewEvalJob).filter(
            InterviewEvalJob.interview_id.between(1001, 1006)
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield


def _make_interview(db, *, interview_id, job_id, meeting_id="123-456",
                    meeting_account="default", user_id=1):
    from app.modules.scheduling.models import Interview
    from app.modules.screening.models import Job

    job = Job(
        id=job_id, user_id=user_id, title="后端",
        competency_model={"hard_skills": [{"name": "Python"}]},
        competency_model_status="approved",
    )
    db.merge(job)
    db.commit()  # 先落 jobs 行，避免 sqlite FK 检查在 flush 跨表 INSERT 时拿不到
    interview = Interview(
        id=interview_id, user_id=user_id, resume_id=1, interviewer_id=1,
        job_id=job_id, meeting_id=meeting_id, meeting_account=meeting_account,
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.merge(interview)
    db.commit()
    return interview


def test_create_job_disabled_returns_503():
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.config import settings
    settings.interview_eval_enabled = False
    with pytest.raises(ServiceError) as exc:
        service.create_job(interview_id=1, user_id=1)
    assert exc.value.code == 503


def test_create_job_interview_not_found():
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.config import settings
    settings.interview_eval_enabled = True
    with pytest.raises(ServiceError) as exc:
        service.create_job(interview_id=999999, user_id=1)
    assert exc.value.code == 404


def test_create_job_user_isolation():
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.config import settings
    settings.interview_eval_enabled = True
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1001, job_id=2001, user_id=1)
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1001, user_id=2)
        assert exc.value.code == 404  # 跨用户也按 not found 返回（防 enumerate）
    finally:
        db.close()


def test_create_job_competency_model_not_approved():
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.modules.screening.models import Job
    from app.config import settings
    settings.interview_eval_enabled = True
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1002, job_id=2002, user_id=1)
        # 把 job.competency_model_status 改为 draft
        db.query(Job).filter(Job.id == 2002).update(
            {"competency_model_status": "draft"}
        )
        db.commit()
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1002, user_id=1)
        assert exc.value.code == 400
        assert "能力模型" in exc.value.message
    finally:
        db.close()


def test_create_job_meeting_id_missing():
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.config import settings
    settings.interview_eval_enabled = True
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1003, job_id=2003, user_id=1, meeting_id="")
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1003, user_id=1)
        assert exc.value.code == 400
        assert "腾讯会议" in exc.value.message
    finally:
        db.close()


def test_create_job_account_not_in_pool(monkeypatch):
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.config import settings
    settings.interview_eval_enabled = True
    monkeypatch.setattr(settings, "tencent_meeting_accounts", "alice,bob")
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1004, job_id=2004, user_id=1, meeting_account="charlie")
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1004, user_id=1)
        assert exc.value.code == 400
        assert "账号" in exc.value.message
    finally:
        db.close()


def test_create_job_already_running_returns_409(monkeypatch):
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.modules.interview_eval.models import InterviewEvalJob
    from app.config import settings
    settings.interview_eval_enabled = True
    monkeypatch.setattr(settings, "tencent_meeting_accounts", "default")
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1005, job_id=2005, user_id=1)
        # 先建一个 pending 任务
        existing = InterviewEvalJob(
            interview_id=1005, user_id=1, status="downloading",
            retention_until=datetime.now(timezone.utc) + timedelta(days=180),
        )
        db.add(existing); db.commit()
        # 不让 worker 真跑
        monkeypatch.setattr(service, "_spawn_worker", lambda job_id: None)
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1005, user_id=1)
        assert exc.value.code == 409
    finally:
        db.close()


def test_create_job_happy_path(monkeypatch):
    from app.modules.interview_eval import service
    from app.modules.interview_eval.models import InterviewEvalJob
    from app.config import settings
    settings.interview_eval_enabled = True
    monkeypatch.setattr(settings, "tencent_meeting_accounts", "default")
    monkeypatch.setattr(service, "_spawn_worker", lambda job_id: None)
    db = SessionLocal()
    try:
        # 清理同一 interview_id 上一次跑残留的 job（test.db 持久）
        db.query(InterviewEvalJob).filter(
            InterviewEvalJob.interview_id == 1006
        ).delete()
        db.commit()
        _make_interview(db, interview_id=1006, job_id=2006, user_id=1)
        job_id = service.create_job(interview_id=1006, user_id=1)
        assert isinstance(job_id, int)

        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job is not None
        assert job.status == "pending"
        assert job.user_id == 1
        # SQLite 不保留 tz，读回是 naive；用 naive 阈值做对比
        threshold_naive = (
            datetime.now(timezone.utc) + timedelta(days=179)
        ).replace(tzinfo=None)
        retention_naive = (
            job.retention_until.replace(tzinfo=None)
            if job.retention_until.tzinfo is not None else job.retention_until
        )
        assert retention_naive > threshold_naive
    finally:
        db.close()
