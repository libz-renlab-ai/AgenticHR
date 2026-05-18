# -*- coding: utf-8 -*-
"""Feishu WebSocket long connection for receiving bot messages and card callbacks."""
import json
import logging
import threading
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_tz8 = timezone(timedelta(hours=8))


def start_feishu_ws(app_id: str, app_secret: str):
    thread = threading.Thread(target=_run_ws, args=(app_id, app_secret), daemon=True)
    thread.start()
    logger.info("Feishu WebSocket client starting...")


def _on_message(event):
    """Handle text messages from interviewer."""
    try:
        msg = event.event.message
        sender = event.event.sender
        content = msg.content or "{}"

        text = ""
        try:
            text = json.loads(content).get("text", "")
        except (json.JSONDecodeError, AttributeError):
            text = str(content)

        sender_id = sender.sender_id.open_id if sender and sender.sender_id else ""
        logger.info(f"[Feishu] Message from {sender_id}: {text}")

        if text.strip():
            _save_reply(sender_id, text.strip())

    except Exception as e:
        logger.error(f"[Feishu] Message handler error: {e}")


def _on_card_action(data):
    """Handle card button clicks. Must return P2CardActionTriggerResponse."""
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

    try:
        event = data.event
        action = event.action
        operator = event.operator

        action_value = action.value or {}
        action_type = action_value.get("action", "")
        interview_id = action_value.get("interview_id", 0)
        open_id = operator.open_id if operator else ""

        logger.info(f"[Feishu] Card action: {action_type} interview={interview_id} from {open_id}")

        if interview_id and action_type:
            _save_card_response(int(interview_id), action_type, open_id)

        # Return toast response
        resp = P2CardActionTriggerResponse()
        from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackToast
        toast = CallBackToast()
        if action_type == "available":
            toast.type = "success"
            toast.content = "已确认有空，感谢！"
        else:
            toast.type = "info"
            toast.content = "已收到，请回复消息告知方便的时间。"
        resp.toast = toast
        return resp

    except Exception as e:
        logger.error(f"[Feishu] Card action error: {e}", exc_info=True)
        return P2CardActionTriggerResponse()


def _save_reply(open_id: str, text: str):
    """收到 Feishu 文字回复 → 写到该面试官最新 scheduled 面试的 notes。

    多租户语义 (方案 A 广播): 若多个 HR 都把同一位面试官 (同 open_id) 注册到
    自己账号下, 回复内容会广播到 **每个 HR** 名下该面试官的最新 scheduled 面试,
    避免旧实现只命中"第一个查到的 Interviewer"导致串号到错家 HR。
    """
    try:
        from app.database import SessionLocal
        from app.modules.scheduling.models import Interviewer, Interview

        db = SessionLocal()
        try:
            interviewers = (
                db.query(Interviewer)
                .filter(Interviewer.feishu_user_id == open_id)
                .all()
            )
            if not interviewers:
                return

            now = datetime.now(_tz8).strftime("%m-%d %H:%M")
            written_ids: list[int] = []
            for interviewer in interviewers:
                # 注意: Interview.interviewer_id FK 已保证同 user_id, 不需额外过滤
                interview = (
                    db.query(Interview)
                    .filter(
                        Interview.interviewer_id == interviewer.id,
                        Interview.status == "scheduled",
                    )
                    .order_by(Interview.created_at.desc())
                    .first()
                )
                if interview is None:
                    continue
                note = f"[{now} {interviewer.name}] {text}"
                interview.notes = f"{interview.notes}\n{note}" if interview.notes else note
                written_ids.append(interview.id)
            if written_ids:
                db.commit()
                logger.info(f"[Feishu] Reply broadcast to interviews {written_ids}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[Feishu] Save reply error: {e}")


def _save_card_response(interview_id: int, action: str, open_id: str):
    """卡片按钮点击 → 写指定 interview 的 notes (interview_id 由 card payload 提供)。

    多租户语义: interview 是单条权威定位, 不广播。面试官姓名按
    interview.user_id 反查同 open_id Interviewer, 防止旧实现取到跨账户
    Interviewer 把别家 HR 的面试官名字写进当前 notes。
    """
    try:
        from app.database import SessionLocal
        from app.modules.scheduling.models import Interviewer, Interview

        db = SessionLocal()
        try:
            interview = db.query(Interview).filter(Interview.id == interview_id).first()
            if interview is None:
                return

            interviewer = (
                db.query(Interviewer)
                .filter(
                    Interviewer.feishu_user_id == open_id,
                    Interviewer.user_id == interview.user_id,
                )
                .first()
            )
            name = interviewer.name if interviewer else "unknown"

            now = datetime.now(_tz8).strftime("%m-%d %H:%M")
            if action == "available":
                note = f"[{now}] {name}: 已确认有空"
            else:
                note = f"[{now}] {name}: 没空，等待回复可用时间"

            interview.notes = f"{interview.notes}\n{note}" if interview.notes else note
            db.commit()
            logger.info(f"[Feishu] Card response: interview={interview_id} {action}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[Feishu] Save card response error: {e}")


def _run_ws(app_id: str, app_secret: str):
    try:
        import lark_oapi as lark

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_on_message)
            .build()
        )

        cli = lark.ws.Client(
            app_id, app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        logger.info("Feishu WebSocket connecting...")
        cli.start()

    except Exception as e:
        logger.error(f"Feishu WebSocket error: {e}", exc_info=True)
