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
        app.include_router(ie_router)

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
