"""F4 T13 — Intake REST API tests (F3.1 T11: migrated to IntakeCandidate)."""
from datetime import datetime, timezone

import pytest

from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.candidate_model import IntakeCandidate


def test_list_candidates_filters_by_status(client, db_session):
    db_session.add(IntakeCandidate(user_id=1, name="a", boss_id="ba", intake_status="collecting", source="plugin"))
    db_session.add(IntakeCandidate(user_id=1, name="b", boss_id="bb", intake_status="complete", source="plugin"))
    db_session.commit()
    r = client.get("/api/intake/candidates?status=collecting")
    assert r.status_code == 200
    body = r.json()
    assert any(c["boss_id"] == "ba" for c in body["items"])
    assert all(c["intake_status"] == "collecting" for c in body["items"])


@pytest.mark.parametrize(
    "status",
    ["collecting", "awaiting_reply", "pending_human", "complete", "abandoned", "timed_out"],
)
def test_list_candidates_accepts_all_intake_status_values(client, db_session, status):
    """Regression for `/intake 等待回复` 标签 400：list endpoint must accept every
    intake_status that service.py writes. Leaving `awaiting_reply` out of the
    enum used to surface as `加载候选人列表失败` in the UI."""
    db_session.add(IntakeCandidate(
        user_id=1, name=f"u_{status}", boss_id=f"b_{status}",
        intake_status=status, source="plugin",
    ))
    db_session.commit()
    r = client.get(f"/api/intake/candidates?status={status}")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(c["intake_status"] == status for c in items)


def test_list_candidates_rejects_unknown_status(client):
    r = client.get("/api/intake/candidates?status=not_a_real_status")
    assert r.status_code == 400


def test_get_candidate_detail_returns_slots(client, db_session):
    c = IntakeCandidate(user_id=1, name="c", boss_id="bc", intake_status="collecting", source="plugin")
    db_session.add(c)
    db_session.commit()
    db_session.add(IntakeSlot(
        candidate_id=c.id, slot_key="arrival_date", slot_category="hard",
        value="下周一", source="regex", ask_count=1,
        asked_at=datetime.now(timezone.utc),
        answered_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    resp = client.get(f"/api/intake/candidates/{c.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert any(s["slot_key"] == "arrival_date" and s["value"] == "下周一" for s in data["slots"])


def test_patch_slot_value(client, db_session):
    c = IntakeCandidate(user_id=1, name="d", boss_id="bd", intake_status="pending_human", source="plugin")
    db_session.add(c)
    db_session.commit()
    s = IntakeSlot(candidate_id=c.id, slot_key="intern_duration", slot_category="hard")
    db_session.add(s)
    db_session.commit()
    resp = client.put(f"/api/intake/slots/{s.id}", json={"value": "6个月"})
    assert resp.status_code == 200
    db_session.refresh(s)
    assert s.value == "6个月"
    assert s.source == "manual"


