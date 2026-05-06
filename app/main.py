"""FastAPI 应用入口"""
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.llm.provider import LLMProvider
from app.database import create_tables

# Global LLM client; used by im_intake router + any module that imports app.main.llm_client.
# Value is None if AI_API_KEY / AI_BASE_URL / AI_MODEL is not configured.
_llm = LLMProvider()
llm_client: LLMProvider | None = _llm if _llm.is_configured() else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    # BUG-089: 启动时把上次进程崩溃残留的 status='running' ScreeningJob 标 failed,
    # 否则用户永远 already_running 阻塞。
    try:
        from app.database import SessionLocal
        from app.modules.ai_screening.models import ScreeningJob
        from datetime import datetime, timezone
        _db = SessionLocal()
        try:
            stuck = (
                _db.query(ScreeningJob)
                .filter(ScreeningJob.status == "running")
                .all()
            )
            now = datetime.now(timezone.utc)
            for sj in stuck:
                sj.status = "failed"
                sj.error_msg = "server restart while running"
                if sj.finished_at is None:
                    sj.finished_at = now
            if stuck:
                _db.commit()
                import logging
                logging.getLogger(__name__).info(
                    "ai_screening reaper: marked %d stuck running -> failed", len(stuck),
                )
        finally:
            _db.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"ai_screening startup reaper failed: {e}")
    # Start Feishu WebSocket client if configured
    if settings.feishu_app_id and settings.feishu_app_secret:
        try:
            from app.adapters.feishu_ws import start_feishu_ws
            start_feishu_ws(settings.feishu_app_id, settings.feishu_app_secret)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Feishu WS client failed to start: {e}")
    # 启动时若有未解析简历，自动续跑 AI 解析任务
    try:
        from app.modules.resume._ai_parse_worker import maybe_start_worker_thread
        maybe_start_worker_thread()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"AI parse worker failed to auto-start: {e}")
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
# 浏览器扩展 popup 的 origin 是 chrome-extension://<32 [a-p] 字符 ext id>，
# 装在 BOSS 直聘内的 content script 的 fetch origin 是 https://www.zhipin.com。
# 这些 origin 不能枚举（ext id 重装会变；zhipin 子域不固定），所以用 regex 放行。
_cors_origin_regex = (
    r"^(chrome-extension://[a-p]{32}"
    r"|https?://([\w-]+\.)*zhipin\.com(:\d+)?"
    r"|https?://(localhost|127\.0\.0\.1)(:\d+)?)$"
)
_cors_origin_re = re.compile(_cors_origin_regex)


def _cors_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    if origin in _cors_origins:
        return True
    return bool(_cors_origin_re.fullmatch(origin))


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT 认证中间件：保护所有 /api/* 路由（白名单除外）
_AUTH_WHITELIST = {"/api/health", "/api/auth/status", "/api/auth/register", "/api/auth/login", "/api/feishu/event"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # OPTIONS 预检请求：返回CORS头并放行
    if request.method == "OPTIONS":
        from starlette.responses import Response
        response = Response(status_code=200)
        origin = request.headers.get("origin", "")
        if _cors_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        return response
    # 测试环境：允许通过环境变量绕过 JWT 认证
    import os as _os
    _bypass = _os.environ.get("AGENTICHR_TEST_BYPASS_AUTH") == "1"
    _is_testing = bool(_os.environ.get("PYTEST_CURRENT_TEST"))
    if _bypass and _is_testing:
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api/") and path not in _AUTH_WHITELIST:
        # 优先从 Authorization header 取 token
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        # URL query param token removed (BUG-037: leaks JWT in server logs / browser history)
        if not token:
            return JSONResponse(status_code=401, content={"detail": "未登录，请先登录"})
        from app.modules.auth.service import decode_token
        payload = decode_token(token)
        if not payload:
            return JSONResponse(status_code=401, content={"detail": "登录已过期，请重新登录"})
        # BUG-118: payload 缺 sub / sub 非数字时, 不应 500 抛栈; 一律转 401。
        sub = payload.get("sub")
        if sub is None:
            return JSONResponse(status_code=401, content={"detail": "登录信息无效，请重新登录"})
        try:
            request.state.user_id = int(sub)
        except (TypeError, ValueError):
            return JSONResponse(status_code=401, content={"detail": "登录信息无效，请重新登录"})
        request.state.username = payload.get("username", "")
    return await call_next(request)


def _build_health_payload(detailed: bool) -> dict:
    """BUG-119: 拆 public health vs detailed status。
    detailed=False (匿名) 仅返 status:ok + app_name; detailed=True (登录后) 返服务清单。
    """
    from app.config import settings as _s
    payload: dict = {"status": "ok", "app_name": _s.app_name}
    if not detailed:
        return payload
    feishu_configured = bool(_s.feishu_app_id and _s.feishu_app_secret)
    ai_configured = bool(_s.ai_enabled and _s.ai_api_key)
    smtp_configured = bool(getattr(_s, 'smtp_host', '') and getattr(_s, 'smtp_user', ''))
    meeting_accounts_str = getattr(_s, 'tencent_meeting_accounts', '') or ''
    meeting_accounts = [a.strip() for a in meeting_accounts_str.split(",") if a.strip()] if meeting_accounts_str else []
    payload["services"] = {
        "feishu": {"configured": feishu_configured},
        "ai": {
            "enabled": getattr(_s, 'ai_enabled', False),
            "configured": ai_configured,
            "model": getattr(_s, 'ai_model', '') if ai_configured else "",
        },
        "email": {"configured": smtp_configured},
        "meeting": {
            "configured": len(meeting_accounts) > 0,
            "account_count": len(meeting_accounts),
        },
    }
    return payload


@app.get("/api/health")
def health_check(request: Request):
    """匿名访问只返 status:ok; 已登录用户返服务详情。BUG-119 信息泄露修复。"""
    is_authed = bool(getattr(request.state, "user_id", None))
    return _build_health_payload(detailed=is_authed)


@app.get("/api/health/detailed")
def health_check_detailed(request: Request):
    """已登录后才能拿到的详细健康检查 (供 dashboard / settings 页用)。
    走 _AUTH_WHITELIST 之外, 中间件会强制鉴权。"""
    return _build_health_payload(detailed=True)


# Register routers
from app.modules.auth.router import router as auth_router
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

from app.modules.resume.router import router as resume_router
app.include_router(resume_router, prefix="/api/resumes", tags=["resumes"])

from app.modules.screening.router import router as screening_router
app.include_router(screening_router, prefix="/api/screening", tags=["screening"])

from app.modules.ai_evaluation.router import router as ai_router
app.include_router(ai_router, prefix="/api/ai", tags=["ai_evaluation"])

from app.modules.scheduling.router import router as scheduling_router
app.include_router(scheduling_router, prefix="/api/scheduling", tags=["scheduling"])

from app.modules.meeting.router import router as meeting_router
app.include_router(meeting_router, prefix="/api/meeting", tags=["meeting"])

from app.modules.notification.router import router as notification_router
app.include_router(notification_router, prefix="/api/notification", tags=["notification"])

from app.modules.boss_automation.router import router as boss_router
app.include_router(boss_router, prefix="/api/boss", tags=["boss_automation"])

from app.modules.feishu_bot.router import router as feishu_bot_router
app.include_router(feishu_bot_router, prefix="/api/feishu", tags=["feishu_bot"])

from app.core.hitl.router import router as hitl_router
app.include_router(hitl_router)

from app.modules.matching.router import router as matching_router
app.include_router(matching_router)

from app.modules.matching.decision_router import router as decision_router
app.include_router(decision_router)

from app.modules.ai_screening.router import router as ai_screening_router
app.include_router(ai_screening_router)

from app.core.competency.router import router as skills_router
app.include_router(skills_router)

from app.core.settings.router import router as settings_router
app.include_router(settings_router)

from app.modules.recruit_bot.router import router as recruit_router
app.include_router(recruit_router)

from app.modules.im_intake.router import router as intake_router
app.include_router(intake_router)

# F1 HITL wiring: F1_competency_review approve → apply competency_model to jobs
from app.core.hitl.service import register_approve_callback as _register_hitl_cb
from app.modules.screening.competency_service import apply_competency_to_job as _apply_comp


def _on_competency_approved(task: dict) -> None:
    """HITL F1_competency_review approve → 写 jobs.competency_model + 双写扁平字段."""
    if task["entity_type"] != "job":
        return
    payload = task.get("edited_payload") or task.get("payload")
    if payload is None:
        return
    _apply_comp(task["entity_id"], payload)


_register_hitl_cb("F1_competency_review", _on_competency_approved)

# Serve Vue frontend static files
import os
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Look for frontend dist in several locations
_frontend_dirs = [
    Path(__file__).parent.parent / "frontend" / "dist",  # dev: project root
    Path(__file__).parent / "frontend_dist",  # packaged: bundled alongside app
    Path(os.getcwd()) / "frontend" / "dist",  # cwd-relative
]

_frontend_dir = None
for d in _frontend_dirs:
    if d.exists():
        _frontend_dir = d
        break

if _frontend_dir:
    app.mount("/assets", StaticFiles(directory=str(_frontend_dir / "assets")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA fallback: serve index.html for all non-API routes.

        index.html 必须 no-cache, 否则前端发布新版本时浏览器仍用旧 chunk hash。
        /assets/* 由 StaticFiles 处理 (chunk hash 内含 → 可长期 cache)。

        BUG-116: 路径校验从 startswith(string-prefix) 改为 Path.is_relative_to,
        防止 `dist-attacker/` 等同前缀目录绕过 (resolved 后 startswith 仍命中)。
        """
        resolved_root = _frontend_dir.resolve()
        try:
            file_path = (_frontend_dir / full_path).resolve()
        except (OSError, ValueError):
            file_path = None
        if file_path is not None:
            try:
                # is_relative_to (3.9+) 严格检查目录边界, 不会被 dist-attacker 绕过
                in_root = file_path.is_relative_to(resolved_root)
            except AttributeError:
                # 兜底: 比较时强制加分隔符防 prefix-confusion
                root_str = str(resolved_root)
                if not root_str.endswith(os.sep):
                    root_str += os.sep
                in_root = (
                    str(file_path) == str(resolved_root)
                    or str(file_path).startswith(root_str)
                )
            if in_root and file_path.is_file():
                return FileResponse(str(file_path))
        return FileResponse(
            str(_frontend_dir / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
