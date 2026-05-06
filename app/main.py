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
        request.state.user_id = int(payload["sub"])
        request.state.username = payload["username"]
    return await call_next(request)


@app.get("/api/health")
def health_check():
    from app.config import settings

    feishu_configured = bool(settings.feishu_app_id and settings.feishu_app_secret)
    ai_configured = bool(settings.ai_enabled and settings.ai_api_key)
    smtp_configured = bool(getattr(settings, 'smtp_host', '') and getattr(settings, 'smtp_user', ''))
    meeting_accounts_str = getattr(settings, 'tencent_meeting_accounts', '') or ''
    meeting_accounts = [a.strip() for a in meeting_accounts_str.split(",") if a.strip()] if meeting_accounts_str else []

    return {
        "status": "ok",
        "app_name": settings.app_name,
        "services": {
            "feishu": {"configured": feishu_configured},
            "ai": {
                "enabled": getattr(settings, 'ai_enabled', False),
                "configured": ai_configured,
                "model": getattr(settings, 'ai_model', '') if ai_configured else "",
            },
            "email": {"configured": smtp_configured},
            "meeting": {
                "configured": len(meeting_accounts) > 0,
                "account_count": len(meeting_accounts),
            },
        }
    }


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
        """
        resolved_root = _frontend_dir.resolve()
        file_path = (_frontend_dir / full_path).resolve()
        if str(file_path).startswith(str(resolved_root)) and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(
            str(_frontend_dir / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
