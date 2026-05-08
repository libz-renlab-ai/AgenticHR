"""F-interview-eval audit_events 写入封装（复用 F1 audit_events 表）.

7 类事件：ieval_start, download_recording, asr_call, llm_call, publish, cancel,
failed_at_<step>, retention_purge。

大 payload (>32KB) 外置到 data/audit/{event_id}.json，行内仅记 _external 引用。
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

from app.database import SessionLocal

logger = logging.getLogger(__name__)

AUDIT_EXTERNAL_DIR = "data/audit"
EXTERNAL_THRESHOLD = 32_000  # 大于 32KB 外置存盘


def record(action: str, *, entity_id: int | None = None, payload: dict | None = None,
           **kwargs) -> str:
    """写一条 audit_events 行，超大 payload 外置 data/audit/{event_id}.json。

    返回 event_id（uuid）。失败仅记日志，不抛——audit 失败不阻塞业务。
    """
    try:
        from app.core.audit.models import AuditEvent
    except ImportError:
        logger.warning("audit_events 模型不存在，跳过 audit")
        return ""

    event_id = str(uuid.uuid4())
    merged_payload = {**(payload or {}), **kwargs}
    payload_json = json.dumps(merged_payload, ensure_ascii=False, default=str)
    external_path = ""
    if len(payload_json) > EXTERNAL_THRESHOLD:
        os.makedirs(AUDIT_EXTERNAL_DIR, exist_ok=True)
        external_path = os.path.join(AUDIT_EXTERNAL_DIR, f"{event_id}.json")
        with open(external_path, "w", encoding="utf-8") as f:
            f.write(payload_json)
        # 行内只留引用
        payload_json = json.dumps({"_external": external_path})

    db = SessionLocal()
    try:
        ev = AuditEvent(
            event_id=event_id,
            f_stage="F-interview-eval",
            action=action,
            entity_type="interview_eval_job",
            entity_id=entity_id,
            input_hash="",
            output_hash="",
            prompt_version=str(kwargs.get("prompt_version", "")),
            model_name=str(kwargs.get("model", "")),
            model_version="",
            reviewer_id=None,
            retention_until=datetime.now(timezone.utc) + timedelta(days=3 * 365),
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        logger.exception("audit write failed action=%s: %s", action, e)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
    return event_id
