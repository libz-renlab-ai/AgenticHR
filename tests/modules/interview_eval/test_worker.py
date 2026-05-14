"""Worker 状态机所有路径 + cancel + 失败兜底."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from app.database import Base, engine, SessionLocal


@pytest.fixture(autouse=True)
def setup_tables():
    """建表 + seed FK 链上游行（resumes/interviewers id=1）+ 清理 InterviewEvalJob 残留.

    备注：Interview.resume_id / interviewer_id 是 NOT NULL FK；sqlite
    PRAGMA foreign_keys=ON 下需要上游行存在。T4 fixture 用 interview_id 2001-2005
    （T3 用 1001-1006），这里清理 2000-2099 范围以保险。
    """
    Base.metadata.create_all(bind=engine)
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interviewer
    from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard
    db = SessionLocal()
    try:
        db.merge(Resume(id=1, name="dummy_resume_t4"))
        db.merge(Interviewer(id=1, name="dummy_interviewer_t4"))
        # 清理上一次 run 残留（test.db 持久）
        db.query(InterviewEvalScorecard).filter(
            InterviewEvalScorecard.interview_id.between(2000, 2099)
        ).delete(synchronize_session=False)
        db.query(InterviewEvalJob).filter(
            InterviewEvalJob.interview_id.between(2000, 2099)
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _path_b_unavailable_by_default(monkeypatch):
    """默认让 Path B 不可用 → 现存测试走 Path A（monkeypatched _download_recording/_transcribe）。

    需要测 Path B 的用例自行覆盖 worker._scrape_transcript。
    """
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.tencent_meeting_recording import TranscriptUnavailable

    def _unavailable(interview):
        raise TranscriptUnavailable("test default: Path B disabled")

    monkeypatch.setattr(worker, "_scrape_transcript", _unavailable)
    yield


def _make_pending_job(db, *, job_id_hint=None, interview_id=2001):
    from app.modules.interview_eval.models import InterviewEvalJob
    from app.modules.scheduling.models import Interview
    from app.modules.screening.models import Job

    db.merge(Job(
        id=3001, user_id=1, title="x",
        competency_model={
            "hard_skills": [],
            "assessment_dimensions": [
                {"name": "技术深度", "description": "...", "question_types": []},
                {"name": "沟通能力", "description": "...", "question_types": []},
            ],
        },
        competency_model_status="approved",
    ))
    db.commit()
    db.merge(Interview(
        id=interview_id, user_id=1, resume_id=1, interviewer_id=1,
        job_id=3001, meeting_id="m-1", meeting_account="default",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()
    job = InterviewEvalJob(
        interview_id=interview_id, user_id=1, status="pending",
        meeting_account="default",
        retention_until=datetime.now(timezone.utc) + timedelta(days=180),
    )
    db.add(job); db.commit(); db.refresh(job)
    return job.id


def test_worker_happy_path_done(monkeypatch, tmp_path):
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard

    # 注入 mock IO
    monkeypatch.setattr(worker, "_download_recording", lambda iv, dest: (str(dest), 1024, 60))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 1000, "speaker": "interviewer", "text": "你好"},
        {"start_ms": 1100, "end_ms": 3000, "speaker": "candidate", "text": "我用过 Spring"},
    ])
    monkeypatch.setattr(worker, "_score_with_llm", lambda iv, transcript: {
        "dimensions": [
            {"name": "技术深度", "score": 8, "reasoning": "证据充分",
             "evidence": [{"start_ms": 1100, "end_ms": 3000, "speaker": "candidate", "text": "我用过 Spring"}]},
            {"name": "沟通能力", "score": 7, "reasoning": "清晰",
             "evidence": [{"start_ms": 0, "end_ms": 1000, "speaker": "interviewer", "text": "你好"}]},
        ],
        "hire_recommendation": "hire",
        "strengths": ["扎实"], "risks": [], "followups": [],
    })
    monkeypatch.setattr(worker, "_publish_feishu", MagicMock())
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "done"
        sc = db.query(InterviewEvalScorecard).filter_by(job_id=job_id).first()
        assert sc is not None
        assert sc.hire_recommendation == "hire"
        assert len(sc.dimensions_json) == 2
    finally:
        db.close()


def test_worker_cancel_before_download(monkeypatch):
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2002)
        # 启动前先 set cancel_requested
        db.query(InterviewEvalJob).filter_by(id=job_id).update({"cancel_requested": 1})
        db.commit()

        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "cancelled"
    finally:
        db.close()


def test_worker_download_failure(monkeypatch):
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    def _fail(*a, **kw):
        raise RuntimeError("录像未生成")

    monkeypatch.setattr(worker, "_download_recording", _fail)
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2003)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "failed"
        assert "录像未生成" in job.error_msg
    finally:
        db.close()


def test_worker_asr_failure(monkeypatch, tmp_path):
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    monkeypatch.setattr(worker, "_download_recording", lambda iv, dest: (str(dest), 100, 60))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: (_ for _ in ()).throw(RuntimeError("ASR 鉴权错")))
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2004)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "failed"
        assert "ASR" in job.error_msg
    finally:
        db.close()


def test_worker_llm_schema_failure_after_retries(monkeypatch, tmp_path):
    """LLM 输出 schema 不合法 → 3 次后 failed."""
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    monkeypatch.setattr(worker, "_download_recording", lambda iv, dest: (str(dest), 100, 60))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "x"}
    ])
    monkeypatch.setattr(worker, "_score_with_llm", lambda iv, t: {"dimensions": []})  # 永远不合法
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "LLM_MAX_RETRY", 3)

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2005)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "failed"
        assert "schema" in job.error_msg.lower() or "validation" in job.error_msg.lower()
    finally:
        db.close()


def test_worker_terminate_active_handle(monkeypatch):
    """worker.terminate_active 设置 cancel_requested 并把 handle 标记中断."""
    from app.modules.interview_eval import worker
    # 不真跑，仅验 API 存在
    assert callable(worker.terminate_active)


def test_worker_path_b_scrape_success(monkeypatch, tmp_path):
    """Path B 成功：用 scrape 的转写稿，不调 _download_recording。"""
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard

    download_called = {"n": 0}

    def _spy_download(iv, dest):
        download_called["n"] += 1
        return (str(dest), 1, 1)

    monkeypatch.setattr(worker, "_scrape_transcript", lambda iv: [
        {"start_ms": 0, "end_ms": 1000, "speaker": "interviewer", "text": "请自我介绍"},
        {"start_ms": 1100, "end_ms": 3000, "speaker": "candidate", "text": "我做后端三年"},
    ])
    monkeypatch.setattr(worker, "_download_recording", _spy_download)
    monkeypatch.setattr(worker, "_score_with_llm", lambda iv, t: {
        "dimensions": [
            {"name": "技术深度", "score": 8, "reasoning": "ok",
             "evidence": [{"start_ms": 1100, "end_ms": 3000, "speaker": "candidate", "text": "我做后端三年"}]},
            {"name": "沟通能力", "score": 7, "reasoning": "ok",
             "evidence": [{"start_ms": 0, "end_ms": 1000, "speaker": "interviewer", "text": "请自我介绍"}]},
        ],
        "hire_recommendation": "hire", "strengths": ["扎实"], "risks": [], "followups": [],
    })
    monkeypatch.setattr(worker, "_publish_feishu", MagicMock())
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2060)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "done", f"got {job.status}: {job.error_msg}"
        assert download_called["n"] == 0, "Path B 成功时不应调用 download"
        assert (job.recording_path or "") == ""  # Path B 无 mp4
        sc = db.query(InterviewEvalScorecard).filter_by(job_id=job_id).first()
        assert sc is not None and len(sc.dimensions_json) == 2
    finally:
        db.close()


def test_worker_path_b_falls_back_to_path_a(monkeypatch, tmp_path):
    """Path B 抛 TranscriptUnavailable → 回退 Path A download + ASR。"""
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob
    from app.modules.interview_eval.tencent_meeting_recording import TranscriptUnavailable

    def _unavailable(iv):
        raise TranscriptUnavailable("no 逐字稿")

    download_called = {"n": 0}

    def _spy_download(iv, dest):
        download_called["n"] += 1
        open(dest, "wb").write(b"\x00" * 100)
        return (str(dest), 100, 60)

    monkeypatch.setattr(worker, "_scrape_transcript", _unavailable)
    monkeypatch.setattr(worker, "_download_recording", _spy_download)
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 1000, "speaker": "candidate", "text": "兜底转写"},
    ])
    monkeypatch.setattr(worker, "_score_with_llm", lambda iv, t: {
        "dimensions": [
            {"name": "技术深度", "score": 6, "reasoning": "ok",
             "evidence": [{"start_ms": 0, "end_ms": 1000, "speaker": "candidate", "text": "兜底转写"}]},
            {"name": "沟通能力", "score": 6, "reasoning": "ok",
             "evidence": [{"start_ms": 0, "end_ms": 1000, "speaker": "candidate", "text": "兜底转写"}]},
        ],
        "hire_recommendation": "hold", "strengths": [], "risks": [], "followups": [],
    })
    monkeypatch.setattr(worker, "_publish_feishu", MagicMock())
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2061)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "done", f"got {job.status}: {job.error_msg}"
        assert download_called["n"] == 1, "Path B 不可用时应回退调用 download"
        assert job.recording_path  # Path A 有 mp4
    finally:
        db.close()


# ===== Round 11 chaos QA 回归测试 =====

def test_chat_complete_sync_fail_fast_on_empty_config(monkeypatch):
    """IE-003: ai_api_key/ai_base_url/ai_model 任一为空时立即抛 RuntimeError，
    不进入 httpx 调用避免外层 retry 3 次浪费。"""
    from app.modules.interview_eval import worker
    from app.config import settings
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_base_url", "https://api.test")
    monkeypatch.setattr(settings, "ai_model", "test-model")
    with pytest.raises(RuntimeError) as exc:
        worker._chat_complete_sync(system="s", user="u")
    assert "未配置" in str(exc.value)
    assert "ai_api_key" in str(exc.value)


def test_score_with_llm_strips_markdown_fence_variants(monkeypatch):
    """IE-004: ```json\\n...\\n``` 与裸 ``` 包裹 + 多换行变体均能解析."""
    from app.modules.interview_eval import worker

    valid_json = '{"dimensions":[],"hire_recommendation":"hire","strengths":[],"risks":[],"followups":[]}'
    cases = [
        valid_json,
        f"```json\n{valid_json}\n```",
        f"```\n{valid_json}\n```",
        f"  ```json\n{valid_json}\n```  ",
    ]
    for raw in cases:
        monkeypatch.setattr(worker, "_chat_complete_sync", lambda **kw: raw)
        # _score_with_llm 还要 db.query Job/Resume，用 mock interview
        class _Iv:
            id = 1; resume_id = 1; job_id = 3001
        result = worker._score_with_llm(_Iv(), [])
        assert result["hire_recommendation"] == "hire", f"failed for raw={raw!r}"


def test_llm_retry_on_transient_error(monkeypatch, tmp_path):
    """IE-014: 瞬时错误（httpx.ConnectError）应触发 retry，第 3 次成功仍 done."""
    import httpx
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard

    monkeypatch.setattr(worker, "_download_recording", lambda iv, dest: (str(dest), 100, 60))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "x"}
    ])

    call_count = {"n": 0}
    valid_payload = {
        "dimensions": [
            {"name": "技术深度", "score": 8, "reasoning": "ok",
             "evidence": [{"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "x"}]},
            {"name": "沟通能力", "score": 7, "reasoning": "ok",
             "evidence": [{"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "x"}]},
        ],
        "hire_recommendation": "hire",
        "strengths": [], "risks": [], "followups": [],
    }

    def _flaky(iv, t):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.ConnectError("conn refused")
        return valid_payload

    monkeypatch.setattr(worker, "_score_with_llm", _flaky)
    monkeypatch.setattr(worker, "_publish_feishu", MagicMock())
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "LLM_MAX_RETRY", 3)

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2010)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "done", f"expected done, got {job.status}: {job.error_msg}"
        assert call_count["n"] == 3, f"expected 3 retries on transient, got {call_count['n']}"
    finally:
        db.close()


def test_llm_no_retry_on_permanent_error(monkeypatch, tmp_path):
    """IE-014: 永久错误（ValidationError，schema 不合法）应 fail-fast，仅 1 次调用."""
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    monkeypatch.setattr(worker, "_download_recording", lambda iv, dest: (str(dest), 100, 60))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "x"}
    ])

    call_count = {"n": 0}

    def _bad_schema(iv, t):
        call_count["n"] += 1
        return {"dimensions": []}  # 缺 hire_recommendation 等 → ValidationError

    monkeypatch.setattr(worker, "_score_with_llm", _bad_schema)
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "LLM_MAX_RETRY", 3)

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2011)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "failed"
        assert call_count["n"] == 1, f"permanent error should not retry, got {call_count['n']} calls"
    finally:
        db.close()


def test_llm_no_retry_on_runtime_error(monkeypatch, tmp_path):
    """IE-014: RuntimeError（如 IE-003 fail-fast '未配置'）不应 retry，仅 1 次调用."""
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    monkeypatch.setattr(worker, "_download_recording", lambda iv, dest: (str(dest), 100, 60))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "x"}
    ])

    call_count = {"n": 0}

    def _runtime_fail(iv, t):
        call_count["n"] += 1
        raise RuntimeError("AI 服务未配置：ai_api_key 为空")

    monkeypatch.setattr(worker, "_score_with_llm", _runtime_fail)
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "LLM_MAX_RETRY", 3)

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2012)
        worker.run(job_id)
        job = db.query(InterviewEvalJob).filter_by(id=job_id).first()
        assert job.status == "failed"
        assert call_count["n"] == 1, f"RuntimeError should not retry, got {call_count['n']} calls"
        assert "未配置" in job.error_msg
    finally:
        db.close()


def test_check_cancel_sees_external_update(monkeypatch, tmp_path):
    """IE-002: worker._check_cancel 必须能看到外部 session 设置的 cancel_requested.
    用两个 SessionLocal 模拟 worker session vs cancel session."""
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    db_worker = SessionLocal()
    try:
        # 先建 pending job
        job_id = _make_pending_job(db_worker, interview_id=2099)
        # worker 第一次 _check_cancel：cancel_requested=0，应返回 False
        assert worker._check_cancel(db_worker, job_id) is False

        # 外部 session（模拟 service.cancel_job）改 cancel_requested=1
        db_external = SessionLocal()
        try:
            db_external.query(InterviewEvalJob).filter_by(id=job_id).update(
                {"cancel_requested": 1}
            )
            db_external.commit()
        finally:
            db_external.close()

        # worker 第二次 _check_cancel：必须看到 cancel_requested=1
        # （没修前因 identity map 缓存返回 False，回归保护）
        monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
        assert worker._check_cancel(db_worker, job_id) is True
        # 验证 status 已更新
        db_external = SessionLocal()
        try:
            j = db_external.query(InterviewEvalJob).filter_by(id=job_id).first()
            assert j.status == "cancelled"
        finally:
            db_external.close()
    finally:
        db_worker.close()


# IE-016: worker.run 在 scoring 阶段 LLM 调用前后必须 bump heartbeat
def test_worker_scoring_bumps_heartbeat_around_llm(monkeypatch, tmp_path):
    """LLM 调用慢 (>stale_threshold) 时 worker 必须打心跳避免被 reconcile 误杀."""
    from datetime import datetime, timezone
    from app.modules.interview_eval import worker
    from app.modules.interview_eval.models import InterviewEvalJob

    bumps = []

    def _spy_bump(db, jid):
        # 真正改 db 让效果可观察
        db.query(InterviewEvalJob).filter_by(id=jid).update(
            {"last_heartbeat": datetime.now(timezone.utc)}
        )
        db.commit()
        bumps.append(datetime.now(timezone.utc))

    monkeypatch.setattr(worker, "_bump_heartbeat", _spy_bump)
    monkeypatch.setattr(worker, "_download_recording", lambda iv, d: (str(d), 1, 1))
    monkeypatch.setattr(worker, "_transcribe", lambda mp4: [
        {"start_ms": 0, "end_ms": 100, "speaker": "candidate", "text": "x"},
    ])
    monkeypatch.setattr(worker, "_score_with_llm", lambda iv, t: {
        "dimensions": [{"name": "技术深度", "score": 8, "reasoning": "x",
                        "evidence": [{"start_ms": 0, "end_ms": 100,
                                      "speaker": "candidate", "text": "x"}]},
                       {"name": "沟通能力", "score": 7, "reasoning": "y",
                        "evidence": [{"start_ms": 0, "end_ms": 100,
                                      "speaker": "candidate", "text": "x"}]}],
        "hire_recommendation": "hire", "strengths": [], "risks": [], "followups": [],
    })
    monkeypatch.setattr(worker, "_publish_feishu", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "RECORDING_DIR", str(tmp_path))

    db = SessionLocal()
    try:
        job_id = _make_pending_job(db, interview_id=2050)
        worker.run(job_id)
        # IE-016: 至少 2 次 bump（LLM 调用前 + 后），实际为 retry 循环每次都 bump
        assert len(bumps) >= 2, f"expected >=2 heartbeat bumps in scoring phase, got {len(bumps)}"
    finally:
        db.close()
