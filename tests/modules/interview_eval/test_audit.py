"""audit_events 写入封装测试（T8）."""
import os
import json
import pytest

from app.database import Base, engine, SessionLocal


@pytest.fixture(autouse=True)
def setup_tables():
    Base.metadata.create_all(bind=engine)
    from app.core.audit.models import AuditEvent
    db = SessionLocal()
    try:
        db.query(AuditEvent).filter(
            AuditEvent.action.in_(["ieval_start", "llm_call"]),
            AuditEvent.entity_id.in_([42, 99]),
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield


def test_record_event_writes_audit_row():
    from app.modules.interview_eval.audit import record
    from app.core.audit.models import AuditEvent

    record("ieval_start", entity_id=42, foo="bar")
    db = SessionLocal()
    try:
        rows = db.query(AuditEvent).filter_by(action="ieval_start", entity_id=42).all()
        assert len(rows) == 1
        ev = rows[0]
        assert ev.f_stage == "F-interview-eval"
        assert ev.entity_type == "interview_eval_job"
        assert ev.event_id  # uuid 写入
    finally:
        db.close()


def test_record_event_external_payload(tmp_path, monkeypatch):
    """大 payload (transcript) 应写到 data/audit/{event_id}.json."""
    from app.modules.interview_eval import audit

    # 重定向外置目录到 tmp_path，避免污染仓库
    monkeypatch.setattr(audit, "AUDIT_EXTERNAL_DIR", str(tmp_path))

    big_text = "x" * 100_000
    eid = audit.record("llm_call", entity_id=99, payload={"raw": big_text})
    assert eid
    external_file = tmp_path / f"{eid}.json"
    assert external_file.exists()
    data = json.loads(external_file.read_text(encoding="utf-8"))
    assert data["raw"] == big_text
