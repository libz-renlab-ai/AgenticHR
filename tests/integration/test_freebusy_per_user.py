"""Regression: freebusy 端点必须按 user_id 隔离, 不允许跨账号读取面试官日历."""
from app.modules.scheduling.models import Interviewer


def test_freebusy_blocks_cross_user(client, db_session):
    """user_id=2 拥有的 Interviewer, client (uid=1) 调 freebusy 应 404."""
    iv = Interviewer(
        name="他人面试官",
        phone="13900000001",
        feishu_user_id="ou_other_user",
        user_id=2,
    )
    db_session.add(iv)
    db_session.commit()

    r = client.get(f"/api/scheduling/interviewers/{iv.id}/freebusy")
    assert r.status_code == 404, r.json()


def test_freebusy_allows_owner(client, db_session, monkeypatch):
    """同 user 的面试官应能正常返回 (走 token=None 分支避免实际飞书 IO)."""
    # 跳过实际 feishu 调用: _get_token 返回 None 后端会走早退分支
    from app.adapters.feishu import FeishuAdapter

    async def _no_token(self):
        return None

    monkeypatch.setattr(FeishuAdapter, "_get_token", _no_token)

    iv = Interviewer(
        name="自家面试官",
        phone="13900000002",
        feishu_user_id="ou_self_user",
        user_id=1,
    )
    db_session.add(iv)
    db_session.commit()

    r = client.get(f"/api/scheduling/interviewers/{iv.id}/freebusy")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body.get("interviewer") == "自家面试官"
