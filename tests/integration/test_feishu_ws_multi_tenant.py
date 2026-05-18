"""Regression: feishu_ws 回调必须按 user_id 隔离, 多 HR 共用同一面试官 Feishu
open_id 时不串号。

之前的实现:
- _save_reply: 只取第一条 Interviewer (cross-user), 回复写到 "第一家" HR 的面试
- _save_card_response: name 取自第一条 Interviewer (cross-user), 可能写错名字

修复后行为 (方案 A 广播):
- _save_reply: 找所有匹配 open_id 的 Interviewer, 每个 HR 自己的最新面试都收到回复
- _save_card_response: 用 interview.user_id 反查 Interviewer name, 只写指定 interview
"""
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.modules.scheduling.models import Interview, Interviewer
from app.modules.resume.models import Resume


SHARED_OPEN_ID = "ou_shared_interviewer"


def _seed_pair(db_session, user_id: int, interview_offset_min: int):
    """每个 user 各造一名同 open_id 的面试官 + 一份简历 + 一场 scheduled 面试。"""
    iv = Interviewer(
        name=f"面试官-u{user_id}", phone="", email=f"u{user_id}@x.com",
        department="", feishu_user_id=SHARED_OPEN_ID, user_id=user_id,
    )
    r = Resume(
        name=f"候选人-u{user_id}", phone=f"139{user_id:08d}", skills="",
        work_years=0, education="本科", ai_parsed="yes", source="manual",
        seniority="中级", user_id=user_id,
    )
    db_session.add_all([iv, r]); db_session.flush()

    start = datetime.utcnow() + timedelta(days=1, minutes=interview_offset_min)
    interview = Interview(
        user_id=user_id, resume_id=r.id, interviewer_id=iv.id,
        start_time=start, end_time=start + timedelta(hours=1),
        status="scheduled", notes="",
    )
    db_session.add(interview); db_session.commit()
    return iv, interview


def test_save_reply_broadcasts_to_all_users_sharing_open_id(db_session, db_engine, monkeypatch):
    """两个 HR 各自有 Interviewer 用同一 Feishu open_id, _save_reply 应广播
    到两家 HR 的最新 scheduled 面试 notes 上, 不止写第一家。"""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)

    iv1, interview1 = _seed_pair(db_session, user_id=1, interview_offset_min=0)
    iv2, interview2 = _seed_pair(db_session, user_id=2, interview_offset_min=60)

    from app.adapters.feishu_ws import _save_reply
    _save_reply(SHARED_OPEN_ID, "明早 10 点没空")

    db_session.expire_all()
    interview1_after = db_session.query(Interview).filter_by(id=interview1.id).first()
    interview2_after = db_session.query(Interview).filter_by(id=interview2.id).first()

    assert "明早 10 点没空" in interview1_after.notes, \
        f"user 1 面试应收到回复, notes={interview1_after.notes!r}"
    assert "明早 10 点没空" in interview2_after.notes, \
        f"user 2 面试也应收到回复 (广播), notes={interview2_after.notes!r}"


def test_save_card_response_does_not_write_to_other_users_interview(db_session, db_engine, monkeypatch):
    """user 1 的面试卡片点击, 只能写到 user 1 的 interview, 不能影响 user 2 的。"""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)

    iv1, interview1 = _seed_pair(db_session, user_id=1, interview_offset_min=0)
    iv2, interview2 = _seed_pair(db_session, user_id=2, interview_offset_min=60)

    from app.adapters.feishu_ws import _save_card_response
    _save_card_response(interview1.id, "available", SHARED_OPEN_ID)

    db_session.expire_all()
    interview1_after = db_session.query(Interview).filter_by(id=interview1.id).first()
    interview2_after = db_session.query(Interview).filter_by(id=interview2.id).first()

    assert "已确认有空" in interview1_after.notes, \
        f"user 1 面试应记录卡片回应, notes={interview1_after.notes!r}"
    assert "已确认有空" not in (interview2_after.notes or ""), \
        f"user 2 面试不应被串号, notes={interview2_after.notes!r}"


def test_save_card_response_uses_correct_user_interviewer_name(db_session, db_engine, monkeypatch):
    """卡片回应里嵌入的面试官名字应取自 interview.user_id 下的 Interviewer,
    而不是恰好被查到的第一个跨账户 Interviewer (旧实现可能拿错名字)。"""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)

    # 故意让 user 2 的 Interviewer 先入库, 制造"first() 选中错家"的场景
    iv2, interview2 = _seed_pair(db_session, user_id=2, interview_offset_min=0)
    iv1, interview1 = _seed_pair(db_session, user_id=1, interview_offset_min=60)

    from app.adapters.feishu_ws import _save_card_response
    _save_card_response(interview1.id, "available", SHARED_OPEN_ID)

    db_session.expire_all()
    interview1_after = db_session.query(Interview).filter_by(id=interview1.id).first()
    assert iv1.name in interview1_after.notes, \
        f"应嵌入 user 1 的面试官名 {iv1.name!r}, notes={interview1_after.notes!r}"
    assert iv2.name not in interview1_after.notes, \
        f"不应嵌入 user 2 的面试官名 {iv2.name!r}, notes={interview1_after.notes!r}"
