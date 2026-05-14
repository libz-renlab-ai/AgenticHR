"""AI 简历解析后台工作线程

在独立线程中逐个解析未被 AI 解析的简历，支持中断恢复和运行期间新增。

解析核心 (查待办 / 解析单个 target / 字段落库) 收敛在 `_ai_parse_core`，
单条端点 (router.ai_parse_single) 与本 worker 共用同一实现，杜绝逻辑漂移。
本模块只负责后台编排：线程入口、进度状态、轮次循环、F2 触发派发。
"""
import asyncio
import logging

from app.database import SessionLocal

# 向后兼容: _coerce_work_years 历史定义在本模块, 现已迁入 _ai_parse_core。
# 保留再导出, 避免外部 import 断裂 (tests/modules/resume/test_ai_parse_worker.py 等)。
from app.modules.resume._ai_parse_core import (  # noqa: F401
    _coerce_work_years,
    ai_parse_target,
    query_pending_targets,
    reset_stale_parsing,
)

logger = logging.getLogger(__name__)


# 全局状态
_status = {
    "running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current": "",
}


def get_parse_status() -> dict:
    return {**_status}


def start_ai_parse_worker(user_id: int = 0):
    """在后台线程中运行 AI 解析（同步入口，内部用 asyncio）

    Args:
        user_id: 限定解析该用户的简历，0 表示不限制（启动自动续跑用）
    """
    if _status["running"]:
        logger.info("AI 解析任务已在运行中，跳过")
        return

    _status["running"] = True
    _status["completed"] = 0
    _status["failed"] = 0
    _status["current"] = ""

    try:
        asyncio.run(_do_parse_all(user_id=user_id))
    except Exception as e:
        logger.error(f"AI 解析任务异常退出: {e}")
    finally:
        _status["running"] = False
        _status["current"] = ""
        logger.info(f"AI 解析任务结束: 完成 {_status['completed']}, 失败 {_status['failed']}")


def maybe_start_worker_thread():
    """如果 AI 已配置且 worker 没在跑，就在后台线程里启动它。幂等。"""
    if _status["running"]:
        return
    try:
        from app.config import settings
        if not settings.ai_enabled:
            return
        from app.adapters.ai_provider import AIProvider
        if not AIProvider().is_configured():
            return
    except Exception:
        return

    import threading
    thread = threading.Thread(target=start_ai_parse_worker, daemon=True)
    thread.start()
    logger.info("AI 解析后台任务已自动启动")


async def _do_parse_all(user_id: int = 0):
    """逐个解析待办 target —— 覆盖 IntakeCandidate (简历库数据源) 与孤儿 Resume。

    待办查询 / 单个解析全部走 _ai_parse_core，与单条端点同实现。
    支持多轮: 一轮解析完后重查，捕获运行期间新增的待解析项。
    """
    from app.adapters.ai_provider import AIProvider

    ai = AIProvider()
    db = SessionLocal()
    try:
        # 启动时清理上次异常退出 (Ctrl+C/OOM/重载) 留下的 stale 'parsing' 行,
        # 两张表 (IntakeCandidate + Resume) 都清，否则永久卡 'parsing' 拉不回来。
        reset_stale_parsing(db)

        round_idx = 0
        while True:
            pending = query_pending_targets(db, user_id)
            if not pending:
                break
            round_idx += 1
            if round_idx == 1:
                _status["total"] = len(pending)
                logger.info(f"AI 解析任务启动: {len(pending)} 份待解析")
            else:
                _status["total"] += len(pending)
                logger.info(f"第 {round_idx} 轮: 发现 {len(pending)} 份新加入的待解析简历")

            for i, target in enumerate(pending):
                _status["current"] = getattr(target, "name", "") or ""
                logger.info(
                    f"[轮{round_idx} {i + 1}/{len(pending)}] 正在解析: {_status['current']}"
                )

                # 先标 parsing 让前端轮询能看到进度; ai_parse_target 内部会再 commit。
                target.ai_parsed = "parsing"
                db.commit()

                status, score_resume_id = await ai_parse_target(target, ai, db)

                if status == "yes":
                    _status["completed"] += 1
                    # F2 T1 trigger: 用 Resume.id 评分。独立 session，与 worker 长
                    # 生命周期 session 隔离; 失败非致命，不阻塞后续解析。
                    if score_resume_id:
                        try:
                            from app.modules.matching.triggers import on_resume_parsed
                            _t1_db = SessionLocal()
                            try:
                                await on_resume_parsed(_t1_db, score_resume_id)
                            finally:
                                _t1_db.close()
                        except Exception as _t1_err:
                            logger.warning(f"F2 T1 trigger failed (non-fatal): {_t1_err}")
                else:
                    _status["failed"] += 1
    finally:
        db.close()
