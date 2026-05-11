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
            InterviewEvalJob.interview_id.between(1001, 1010)
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


# ===== Round 11 chaos QA 回归测试 =====

def test_create_job_meeting_id_whitespace_rejected(monkeypatch):
    """IE-011: meeting_id='  '（仅空格）应被拒绝，不能放进 worker."""
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.config import settings
    settings.interview_eval_enabled = True
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1007, job_id=2007, user_id=1, meeting_id="   ")
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1007, user_id=1)
        assert exc.value.code == 400
        assert "腾讯会议" in exc.value.message
    finally:
        db.close()


def test_create_job_spawn_failure_marks_job_failed(monkeypatch):
    """IE-005: _spawn_worker 抛错时 job 应 status=failed + error_msg，不留 pending 死锁."""
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.modules.interview_eval.models import InterviewEvalJob
    from app.config import settings
    settings.interview_eval_enabled = True
    monkeypatch.setattr(settings, "tencent_meeting_accounts", "default")
    def _boom(jid): raise RuntimeError("thread pool exhausted")
    monkeypatch.setattr(service, "_spawn_worker", _boom)
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1008, job_id=2008, user_id=1)
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1008, user_id=1)
        assert exc.value.code == 500
        # 确认 job 已 mark failed 而非留 pending
        job = (
            db.query(InterviewEvalJob)
            .filter_by(interview_id=1008).order_by(InterviewEvalJob.id.desc()).first()
        )
        assert job is not None
        assert job.status == "failed"
        assert "thread pool" in job.error_msg
    finally:
        db.close()


def test_create_job_concurrent_race_rolls_back_duplicate(monkeypatch):
    """IE-001: 并发竞争导致同 interview 两个 pending job 时，后者应回滚 + 抛 409."""
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError
    from app.modules.interview_eval.models import InterviewEvalJob
    from datetime import datetime, timezone, timedelta
    from app.config import settings
    settings.interview_eval_enabled = True
    monkeypatch.setattr(settings, "tencent_meeting_accounts", "default")
    monkeypatch.setattr(service, "_spawn_worker", lambda jid: None)
    db = SessionLocal()
    try:
        _make_interview(db, interview_id=1009, job_id=2009, user_id=1)
        # 模拟"前一个并发请求"已落表（在 service 校验 4 之后、INSERT 之前的时间窗插入）
        # 用 fixture 直接预埋一行 pending：第二次 create_job INSERT 后 verify 会发现 active=2
        ghost = InterviewEvalJob(
            interview_id=1009, user_id=1, status="pending",
            meeting_account="default",
            retention_until=datetime.now(timezone.utc) + timedelta(days=180),
        )
        db.add(ghost); db.commit()
        # 但是校验 4 会先看到 ghost 抛 409 — 我们要测的是 verify-after-commit 路径，
        # 所以让校验 4 不看到 ghost：把 ghost 状态改为 "done"，校验 4 通过；
        # 然后用 monkeypatch 在 service 内部的"INSERT 之后"模拟另一个并发 INSERT。
        ghost.status = "done"; db.commit()
        # 用 patch 拦截 db.commit 第一次以模拟 ghost 在并发窗内变 pending
        from app.modules.interview_eval import service as svc_mod
        original_session_local = svc_mod.SessionLocal
        call_count = {"n": 0}
        class _PatchedSession:
            def __init__(self):
                self._real = original_session_local()
            def __getattr__(self, k): return getattr(self._real, k)
            def commit(self):
                call_count["n"] += 1
                self._real.commit()
                if call_count["n"] == 1:
                    # 第一次 commit 是新 job INSERT；模拟竞争对手"刚刚也 INSERT 了"
                    db2 = original_session_local()
                    try:
                        db2.query(InterviewEvalJob).filter_by(id=ghost.id).update(
                            {"status": "pending"}
                        )
                        db2.commit()
                    finally:
                        db2.close()
        monkeypatch.setattr(svc_mod, "SessionLocal", _PatchedSession)
        with pytest.raises(ServiceError) as exc:
            service.create_job(interview_id=1009, user_id=1)
        assert exc.value.code == 409
        assert "并发" in exc.value.message or "已有" in exc.value.message
    finally:
        db.close()


# IE-017: create_job 创建 pending 时必须写 last_heartbeat=now，避免 reconcile 抢杀
def test_create_job_writes_heartbeat_on_pending(monkeypatch):
    from datetime import datetime, timezone
    from app.modules.interview_eval import service
    from app.modules.interview_eval.models import InterviewEvalJob

    monkeypatch.setattr(service.settings, "interview_eval_enabled", True)
    monkeypatch.setattr(service.settings, "tencent_cloud_secret_id", "x")
    monkeypatch.setattr(service.settings, "tencent_meeting_accounts", "default,main")
    monkeypatch.setattr(service, "_spawn_worker", lambda jid: None)  # 不真起 worker

    db = SessionLocal()
    try:
        db.query(InterviewEvalJob).filter(
            InterviewEvalJob.interview_id.between(7000, 7099)
        ).delete(synchronize_session=False)
        db.commit()
        _make_interview(db, interview_id=7001, job_id=7001)
        before = datetime.now(timezone.utc).replace(microsecond=0)
        job_id = service.create_job(interview_id=7001, user_id=1)
        new_job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert new_job.last_heartbeat is not None  # 关键：不能 NULL
        hb = new_job.last_heartbeat
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        assert hb >= before.replace(tzinfo=timezone.utc) - timedelta(seconds=1)
    finally:
        db.close()
