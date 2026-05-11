"""F-interview-eval 飞书卡片推送（HR + 面试官）.

复用 app.adapters.feishu.FeishuAdapter（async send_message + msg_type=interactive）。
推送失败仅日志，不抛——卡片推送是辅助通道，不能阻塞主流程。
"""
import asyncio
import json
import logging
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app.modules.scheduling.models import Interviewer

logger = logging.getLogger(__name__)


def _send_card(receive_id: str, card: dict[str, Any]) -> None:
    """同步包装：调 FeishuAdapter.send_message 发送 interactive 卡片。

    注意：adapter 当前以 open_id 调用 receive_id_type；项目内 feishu_user_id
    存的就是 open_id（参见 calendar/lookup_user_id 的统一约定）。
    """
    from app.adapters.feishu import FeishuAdapter

    adapter = FeishuAdapter()
    coro = adapter.send_message(receive_id, json.dumps(card, ensure_ascii=False),
                                 msg_type="interactive")
    try:
        # 大多数调用环境无活跃 event loop（worker 同步线程）
        asyncio.run(coro)
    except RuntimeError:
        # IE-027: 已在 event loop 里时新建 loop，用完不仅 close 还要解绑 thread-local
        # 避免长期累积 loop 对象 / signal handler 引用
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        finally:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)


def _resolve_hr_feishu_id(user_id: int) -> str:
    """HR 自己的 feishu user_id（项目里用 users 表存）。User 模型若无该字段返回空。"""
    from app.modules.auth.models import User
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(id=user_id).first()
        return getattr(u, "feishu_user_id", "") if u else ""
    finally:
        db.close()


def _resolve_interviewer_feishu_id(interviewer_id: int) -> str:
    db = SessionLocal()
    try:
        i = db.query(Interviewer).filter_by(id=interviewer_id).first()
        return i.feishu_user_id if i else ""
    finally:
        db.close()


def _build_card(interview, scorecard) -> dict:
    avg = (
        sum(d.get("score", 0) for d in scorecard.dimensions_json)
        / len(scorecard.dimensions_json)
        if scorecard.dimensions_json else 0
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🤖 AI 面评已生成"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "fields": [
                {"is_short": True, "text": {"tag": "lark_md",
                                            "content": f"**面试 ID**\n{interview.id}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                                            "content": f"**录用建议**\n{scorecard.hire_recommendation}"}},
                {"is_short": True, "text": {"tag": "lark_md",
                                            "content": f"**总分**\n{avg:.1f}/10"}},
            ]},
            {"tag": "action", "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看完整 AI 面评 →"},
                "type": "primary",
                "url": f"http://{getattr(settings, 'app_host', '127.0.0.1')}"
                       f":{getattr(settings, 'app_port', 8000)}"
                       f"/interviews?id={interview.id}&tab=ai-eval",
            }]},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": "此为 AI 草稿，仅供参考；最终决定权在 HR/面试官"}]},
        ],
    }


def push(interview, scorecard) -> None:
    """推送给 HR + 面试官，失败仅日志，不抛。"""
    if not settings.feishu_app_id:
        logger.info("feishu not configured, skip push for interview %s", interview.id)
        return

    card = _build_card(interview, scorecard)
    targets = []
    # BUG-IE-015: HR 是触发者，UI 已有 done 状态反馈；默认不重复推送，需开关启用
    if getattr(settings, "feishu_notify_trigger_hr", False):
        targets.append((lambda: _resolve_hr_feishu_id(interview.user_id), "HR"))
    targets.append(
        (lambda: _resolve_interviewer_feishu_id(interview.interviewer_id), "interviewer")
    )
    for resolver, label in targets:
        try:
            uid = resolver()
            if uid:
                _send_card(uid, card)
        except Exception as e:
            logger.warning("feishu push to %s failed: %s", label, e)
