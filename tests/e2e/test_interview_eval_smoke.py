"""F-interview-eval E2E：建岗 → competency_model approve → 安排面试
   → 点 [分析面试] → 看到 scorecard."""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.database import Base, engine, SessionLocal


@pytest.fixture
def setup_world(monkeypatch):
    Base.metadata.create_all(bind=engine)
    from app.config import settings
    settings.interview_eval_enabled = True
    settings.tencent_meeting_accounts = "default"
    settings.tencent_cloud_secret_id = "fake"
    settings.tencent_cloud_secret_key = "fake"

    # 清理 interview_id=1 上的 InterviewEvalJob/Scorecard 历史残留（参考 T9 retention 模式）
    from app.modules.interview_eval.models import (
        InterviewEvalJob, InterviewEvalScorecard,
    )
    db = SessionLocal()
    try:
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


def _seed_world(db, *, recording_dir: Path):
    """造数据：user → job (approved) → resume → interview."""
    from app.modules.auth.models import User
    from app.modules.screening.models import Job
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interview, Interviewer

    db.merge(User(id=1, username="hr1", password_hash="x"))
    db.merge(Job(
        id=1, user_id=1, title="后端", jd_text="",
        competency_model={
            "hard_skills": [{"name": "Python", "must_have": True}],
            "assessment_dimensions": [
                {"name": "技术深度", "description": "Python", "question_types": []},
            ],
        },
        competency_model_status="approved",
    ))
    db.merge(Resume(id=1, user_id=1, name="张三", phone="13800000000"))
    db.merge(Interviewer(id=1, name="李四", feishu_user_id=""))
    db.commit()  # 先 commit 上游 FK 行，避免 Interview merge 时 FK 检查失败
    db.merge(Interview(
        id=1, user_id=1, resume_id=1, interviewer_id=1, job_id=1,
        meeting_id="abc", meeting_account="default",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()


def test_e2e_smoke(setup_world, tmp_path, monkeypatch):
    from app.modules.interview_eval import worker, service
    from app.modules.interview_eval.models import (
        InterviewEvalJob, InterviewEvalScorecard,
    )

    # mock 三外部 IO
    fake_mp4 = tmp_path / "1.mp4"
    fake_mp4.write_bytes(b"\x00" * 1024)
    monkeypatch.setattr(worker, "_download_recording",
                        lambda iv, dest: (str(fake_mp4), 1024, 600))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 500, "speaker": "interviewer", "text": "你好"},
        {"start_ms": 600, "end_ms": 3000, "speaker": "candidate",
         "text": "我用过 Python 三年"},
    ])
    monkeypatch.setattr(worker, "_score_with_llm", lambda iv, t: {
        "dimensions": [{
            "name": "技术深度", "score": 8, "reasoning": "证据充分",
            "evidence": [{
                "start_ms": 600, "end_ms": 3000, "speaker": "candidate",
                "text": "我用过 Python 三年",
            }],
        }],
        "hire_recommendation": "hire",
        "strengths": ["Python 经验扎实"], "risks": [], "followups": [],
    })
    monkeypatch.setattr(worker, "_publish_feishu", lambda iv, sc: None)
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    # 同步执行 worker（不走线程）
    monkeypatch.setattr(service, "_spawn_worker", lambda jid: worker.run(jid))

    db = SessionLocal()
    try:
        _seed_world(db, recording_dir=tmp_path)
        job_id = service.create_job(interview_id=1, user_id=1)

        # 任务应该已 done（同步执行）
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "done", (
            f"unexpected status: {job.status}, err={job.error_msg}"
        )

        sc = db.query(InterviewEvalScorecard).filter_by(job_id=job_id).first()
        assert sc is not None
        assert sc.hire_recommendation == "hire"
        assert len(sc.dimensions_json) == 1
        assert sc.dimensions_json[0]["score"] == 8
    finally:
        db.close()
