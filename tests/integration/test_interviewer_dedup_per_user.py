"""Regression: 手机号查重必须按 user_id 隔离, 不能跨用户阻塞."""
from app.modules.scheduling.models import Interviewer


def _seed_user(db, uid):
    from sqlalchemy import text
    db.execute(
        text(
            "INSERT OR IGNORE INTO users "
            "(id, username, display_name, password_hash, is_active, daily_cap, created_at) "
            "VALUES (:id, :u, :u, 'x', 1, 200, datetime('now'))"
        ),
        {"id": uid, "u": f"u{uid}"},
    )
    db.commit()


def test_phone_dedup_does_not_leak_across_users(client, db_session):
    """user A 有手机号 X 的面试官, user B (client 默认 uid=1) 应能用同样手机号注册."""
    _seed_user(db_session, 2)
    db_session.add(Interviewer(
        name="李博泽", phone="13165338580", feishu_user_id="ou_other",
        user_id=2,
    ))
    db_session.commit()

    r = client.post(
        "/api/scheduling/interviewers",
        json={
            "name": "新人",
            "phone": "13165338580",
            "feishu_user_id": "ou_self_user_1",
            "department": "tech",
        },
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["name"] == "新人"
    assert body["phone"] == "13165338580"


def test_phone_dedup_blocks_within_same_user(client, db_session):
    """同一 user 重复手机号仍应返 409."""
    # client 默认走 user_id=1
    db_session.add(Interviewer(
        name="既有", phone="13165338580", feishu_user_id="ou_existing",
        user_id=1,
    ))
    db_session.commit()

    r = client.post(
        "/api/scheduling/interviewers",
        json={
            "name": "再加一个",
            "phone": "13165338580",
            "feishu_user_id": "ou_dup",
            "department": "tech",
        },
    )
    assert r.status_code == 409
    detail = r.json().get("detail", "")
    assert "13165338580" in detail
    assert "既有" in detail
