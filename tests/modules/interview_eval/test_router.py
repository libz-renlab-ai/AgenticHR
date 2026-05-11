"""F-interview-eval Router 端点 + auth/ServiceError 映射.

按项目模式：
- auth dep 用 app.dependency_overrides[get_current_user_id] 注入 user_id=1
  （不是 plan 草稿里的 monkeypatch.setattr —— 那个不会被 FastAPI Depends 解析层看见）
- main.py 的条件挂载（开关 + 凭证齐才挂）在 fixture 里手动 include_router 绕开，
  这样无论 .env 凭证是否齐全测试都能跑（plan T10 Step 4 的条件挂载逻辑保持不变）
- negative case 用 "interview_eval_enabled=False 时 service.create_job 抛 503,
  router 透传 503"，比 plan 草稿里 401/403（auth bypass 下根本测不到）更有意义
"""
import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine


@pytest.fixture(autouse=True)
def setup():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client(monkeypatch):
    """挂上 router、override auth dep 返 user_id=1。"""
    from app.main import app
    from app.modules.auth.deps import get_current_user_id
    from app.modules.interview_eval.router import router as ie_router

    # main.py 的条件挂载在 .env 凭证空时不挂，测试里强行挂上一次
    # （重复 include_router 会注册同名前缀两次，得先判断是否已挂）
    already_mounted = any(
        getattr(r, "path", "").startswith("/api/interview-eval")
        for r in app.routes
    )
    if not already_mounted:
        # 防 catch-all `/{full_path:path}` 截胡：fixture 后挂时 include_router
        # 会把路由 append 到 catch-all 后面，所有 GET 都先被 catch-all 吃 → 假 404。
        # 把 catch-all 暂时摘掉，挂完 ie_router 再 append 回去。
        catch_all = next(
            (r for r in app.routes if getattr(r, "path", "") == "/{full_path:path}"),
            None,
        )
        if catch_all is not None:
            app.routes.remove(catch_all)
        app.include_router(ie_router)
        if catch_all is not None:
            app.routes.append(catch_all)

    app.dependency_overrides[get_current_user_id] = lambda: 1
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)


def test_start_disabled_returns_503(monkeypatch, client):
    """开关关闭时 service.create_job 抛 503，router 透传。
    （替代 plan 草稿的 401/403 — auth bypass 下永远过不去）
    """
    from app.config import settings
    monkeypatch.setattr(settings, "interview_eval_enabled", False)
    r = client.post("/api/interview-eval/start", json={"interview_id": 1})
    assert r.status_code == 503


def test_start_happy_returns_200(monkeypatch, client):
    """create_job 成功 → router 返 200 + job_id。"""
    from app.modules.interview_eval import service
    monkeypatch.setattr(service, "create_job", lambda **kw: 42)

    r = client.post("/api/interview-eval/start", json={"interview_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == 42
    assert body["status"] == "pending"


def test_get_job_not_found(monkeypatch, client):
    """service.get_job 抛 ServiceError(404,...) → router 返 404。"""
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError

    def _raise(*a, **kw):
        raise ServiceError(404, "not found")
    monkeypatch.setattr(service, "get_job", _raise)
    r = client.get("/api/interview-eval/9999")
    assert r.status_code == 404


def test_cancel_409_when_done(monkeypatch, client):
    """service.cancel_job 抛 ServiceError(409,...) → router 返 409。"""
    from app.modules.interview_eval import service
    from app.modules.interview_eval.service import ServiceError

    def _raise(*a, **kw):
        raise ServiceError(409, "已完成")
    monkeypatch.setattr(service, "cancel_job", _raise)
    r = client.post("/api/interview-eval/1/cancel")
    assert r.status_code == 409


# IE-025: router 录像路径用 job.recording_path 字段，与 IE-013 retention 修复对齐
def test_get_recording_uses_job_recording_path(client, tmp_path, monkeypatch):
    """RECORDING_DIR 配置变化场景：job.recording_path 是绝对路径而非默认 'data/recordings/{id}.mp4'."""
    import os
    from datetime import datetime, timezone, timedelta
    from app.config import settings
    from app.database import SessionLocal
    from app.modules.interview_eval.models import InterviewEvalJob
    from app.modules.resume.models import Resume
    from app.modules.scheduling.models import Interview, Interviewer
    from app.modules.screening.models import Job

    monkeypatch.setattr(settings, "interview_eval_enabled", True)

    # 准备一个真 mp4 文件到非默认路径
    custom_dir = tmp_path / "nfs_recordings"
    custom_dir.mkdir()
    mp4_path = custom_dir / "demo.mp4"
    mp4_path.write_bytes(b"FAKE_MP4")

    db = SessionLocal()
    try:
        # FK 上游
        db.merge(Resume(id=1, name="r"))
        db.merge(Interviewer(id=1, name="i"))
        db.merge(Job(id=8001, user_id=1, title="t",
                     competency_model={}, competency_model_status="approved"))
        db.commit()
        db.merge(Interview(
            id=8001, user_id=1, resume_id=1, interviewer_id=1, job_id=8001,
            meeting_id="m", meeting_account="default",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()
        # 清旧
        db.query(InterviewEvalJob).filter_by(interview_id=8001).delete()
        db.commit()
        # 创建 job，recording_path 指向自定义路径
        job = InterviewEvalJob(
            interview_id=8001, user_id=1, status="done",
            recording_path=str(mp4_path),
            retention_until=datetime.now(timezone.utc) + timedelta(days=180),
        )
        db.add(job); db.commit(); db.refresh(job)

        # 默认路径不存在
        default_path = f"data/recordings/{job.id}.mp4"
        if os.path.exists(default_path):
            os.remove(default_path)

        r = client.get(f"/api/interview-eval/{job.id}/recording")
        # IE-025: 必须能从 job.recording_path 读到，不是 404
        assert r.status_code == 200, f"router 没用 job.recording_path，可能仍在硬编码 data/recordings/{{id}}.mp4: {r.text}"
        assert r.content == b"FAKE_MP4"
    finally:
        db.close()
