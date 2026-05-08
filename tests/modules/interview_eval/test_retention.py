"""T9 retention cron：180 天清理 mp4 + transcript + soft-delete job."""
import os
import pytest
from datetime import datetime, timezone, timedelta

from app.database import Base, engine, SessionLocal


@pytest.fixture(autouse=True)
def setup_tables():
    """建表 + seed FK 链上游 (Resume/Interviewer/Interview id=1) + 清理 interview_id=1 残留.

    test_retention 的 _make_job 复用 interview_id=1。多次调用会建多个相同 interview_id
    的 InterviewEvalJob 行——必须每次 setup 清理避免污染（参考 T8 audit 清理模式）。
    """
    Base.metadata.create_all(bind=engine)
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interviewer, Interview
    from app.modules.interview_eval.models import (
        InterviewEvalJob, InterviewEvalScorecard,
    )
    db = SessionLocal()
    try:
        db.merge(Resume(id=1, name="dummy_resume_t9"))
        db.merge(Interviewer(id=1, name="dummy_interviewer_t9"))
        db.commit()
        # Interview(id=1) 在 conftest 链不一定存在；显式 seed
        existing = db.query(Interview).filter_by(id=1).first()
        if existing is None:
            db.add(Interview(
                id=1, user_id=1, resume_id=1, interviewer_id=1,
                meeting_id="m-t9", meeting_account="default",
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            db.commit()
        # 清理 interview_id=1 残留
        db.query(InterviewEvalScorecard).filter(
            InterviewEvalScorecard.interview_id == 1
        ).delete(synchronize_session=False)
        db.query(InterviewEvalJob).filter(
            InterviewEvalJob.interview_id == 1
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield


def _make_job(db, *, retention_until, recording_path=""):
    from app.modules.interview_eval.models import InterviewEvalJob
    job = InterviewEvalJob(
        interview_id=1, user_id=1, status="done",
        recording_path=recording_path, retention_until=retention_until,
    )
    db.add(job); db.commit(); db.refresh(job)
    return job.id


def test_purge_expired_deletes_files(tmp_path):
    from app.modules.interview_eval import retention
    from app.modules.interview_eval.models import InterviewEvalJob

    db = SessionLocal()
    try:
        mp4 = tmp_path / "expired.mp4"; mp4.write_bytes(b"x")
        ts = tmp_path / "expired.json"; ts.write_text("[]")
        # 测试里 monkeypatch 路径常量
        retention.RECORDING_DIR = str(tmp_path)
        retention.TRANSCRIPT_DIR = str(tmp_path)

        job_id = _make_job(
            db,
            retention_until=datetime.now(timezone.utc) - timedelta(days=1),
            recording_path=str(mp4),
        )
        # rename 让默认路径生效
        os.rename(str(mp4), str(tmp_path / f"{job_id}.mp4"))
        os.rename(str(ts), str(tmp_path / f"{job_id}.json"))

        retention.purge_expired()
        assert not (tmp_path / f"{job_id}.mp4").exists()
        assert not (tmp_path / f"{job_id}.json").exists()
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.deleted_at is not None
        assert job.recording_path == ""
    finally:
        db.close()


def test_purge_does_not_touch_unexpired():
    from app.modules.interview_eval import retention
    from app.modules.interview_eval.models import InterviewEvalJob
    db = SessionLocal()
    try:
        job_id = _make_job(
            db, retention_until=datetime.now(timezone.utc) + timedelta(days=10),
        )
        retention.purge_expired()
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.deleted_at is None
    finally:
        db.close()


def test_purge_missing_files_no_error():
    from app.modules.interview_eval import retention
    db = SessionLocal()
    try:
        # 文件不存在但 retention 到期 → 不应抛
        _make_job(
            db, retention_until=datetime.now(timezone.utc) - timedelta(days=1),
            recording_path="/nonexistent/x.mp4",
        )
        retention.purge_expired()  # 不抛即通过
    finally:
        db.close()
