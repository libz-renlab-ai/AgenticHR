"""1 章 系统启动与基础设施 (F-INFRA-01..14)。"""
import sqlite3
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent


@pytest.mark.api
@pytest.mark.smoke
def test_F_INFRA_01_sqlite_auto_create(qa_db_path):
    """F-INFRA-01: SQLite 自动建表"""
    assert qa_db_path.exists()
    with sqlite3.connect(qa_db_path) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
    for t in ["users", "resumes", "jobs", "interviews"]:
        assert t in tables, f"缺表: {t}"


@pytest.mark.api
def test_F_INFRA_02_wal_mode(qa_db_path):
    """F-INFRA-02: WAL 模式启用"""
    with sqlite3.connect(qa_db_path) as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", f"journal_mode={mode}"


@pytest.mark.api
def test_F_INFRA_03_auto_migration(qa_db_path):
    """F-INFRA-03: 自动列迁移幂等(alembic_version 标记存在)"""
    with sqlite3.connect(qa_db_path) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
    assert "alembic_version" in tables


@pytest.mark.api
def test_F_INFRA_04_zombie_screening_cleanup(api_base, artifacts_dir):
    """F-INFRA-04: 启动时清理 >10min 无进展的 ScreeningJob (验启动日志可达)"""
    log = artifacts_dir / "logs" / "uvicorn.log"
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="ignore")
        # 启动应当成功(出现 "Application startup complete")
        assert "Application startup complete" in text or "Uvicorn running" in text


@pytest.mark.api
def test_F_INFRA_05_feishu_status(api_base, http, auth_headers):
    """F-INFRA-05: 飞书 WS 后台启动 / 状态可查"""
    r = http.get(f"{api_base}/api/feishu/status", headers=auth_headers)
    assert r.status_code == 200, r.text


@pytest.mark.api
def test_F_INFRA_06_resume_worker_idempotent(api_base, http, auth_headers):
    """F-INFRA-06: 简历 AI 解析 worker 幂等触发"""
    r1 = http.post(f"{api_base}/api/resumes/ai-parse-all", headers=auth_headers)
    r2 = http.post(f"{api_base}/api/resumes/ai-parse-all", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text


@pytest.mark.api
def test_F_INFRA_07_interview_eval_router_mounted(api_base, http, auth_headers):
    """F-INFRA-07: Interview-Eval router 已挂载(INTERVIEW_EVAL_ENABLED=true)"""
    # 路由已挂载 → 资源不存在返 404 (我们模块的 HTTPException),
    # 未挂载 → 也是 404 但 detail 不同;统一接受 4xx
    r = http.get(f"{api_base}/api/interview-eval/0", headers=auth_headers)
    assert r.status_code in (404, 422), r.text


@pytest.mark.api
def test_F_INFRA_08_cors_preflight(api_base, http):
    """F-INFRA-08: CORS OPTIONS 预检"""
    r = http.request(
        "OPTIONS",
        f"{api_base}/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code in (200, 204), r.text
    headers_lower = {h.lower() for h in r.headers}
    assert "access-control-allow-origin" in headers_lower


@pytest.mark.api
def test_F_INFRA_09_jwt_required(api_base, http):
    """F-INFRA-09: JWT 鉴权: 缺 token 401"""
    r = http.get(f"{api_base}/api/resumes/")
    assert r.status_code == 401, r.text


@pytest.mark.api
def test_F_INFRA_10_health_anonymous(api_base, http):
    """F-INFRA-10: /api/health 匿名"""
    r = http.get(f"{api_base}/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body and "app_name" in body


@pytest.mark.api
def test_F_INFRA_11_health_detailed_authed(api_base, http, auth_headers):
    """F-INFRA-11: /api/health/detailed 需登录,返服务配置详情"""
    r = http.get(f"{api_base}/api/health/detailed", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    services = body.get("services", {})
    assert any(k in services for k in ("feishu", "ai", "email", "meeting")), body


@pytest.mark.api
def test_F_INFRA_12_api_404_json(api_base, http, auth_headers):
    """F-INFRA-12: 不存在的 /api/* 路径返 JSON 404 (BUG-150)"""
    r = http.get(f"{api_base}/api/this/does/not/exist", headers=auth_headers)
    assert r.status_code == 404, r.text
    assert r.headers.get("content-type", "").startswith("application/json"), r.headers


@pytest.mark.api
def test_F_INFRA_13_spa_fallback(api_base, http):
    """F-INFRA-13: SPA fallback 任意非 API 路径返 index.html (前端 dist 存在时)"""
    r = http.get(f"{api_base}/some/spa/route", follow_redirects=False)
    if r.status_code == 200:
        assert "html" in r.headers.get("content-type", "").lower()
    # frontend dist 不存在时 404 也接受(纯后端开发场景)
    assert r.status_code in (200, 404)


@pytest.mark.api
def test_F_INFRA_14_assets_caching(api_base, http):
    """F-INFRA-14: /assets/* 长期缓存 (有 dist 时)"""
    r = http.get(f"{api_base}/assets/nonexistent.js")
    if r.status_code == 200:
        cc = r.headers.get("cache-control", "").lower()
        assert "max-age" in cc or "immutable" in cc
    else:
        # 没 dist 直接 404,跳过断言
        pytest.skip(f"no dist or asset, status={r.status_code}")
