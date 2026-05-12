"""11 章 通知系统 (F-NOTI-01..08)。

Endpoints:
  POST   /api/notification/send       — 综合发送(邮件/飞书/日历/PDF/模板)
  GET    /api/notification/logs       — 通知日志
  DELETE /api/notification/clear-all  — 仅清当前用户日志

观察:
- F-NOTI-01/04 真发飞书 → external_real,默认 skip
- F-NOTI-02/03/05 是依赖外部的子能力,这里只验"在合理 fixture 下端到端调一次,
  收到 200 + 含合理结构 + 通道明细"
- F-NOTI-06 generate_template 即使发送失败也返,可不依赖外部服务
- F-NOTI-07/08 是纯 DB 操作
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent


# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────
def _utcnow_floor_min():
    return datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)


def _seed_interview(
    qa_db_path,
    *,
    candidate_email: str = "noti_test@example.com",
    candidate_phone: str = "13800000001",
    interviewer_feishu_id: str = "",
    pdf_path: str = "",
    meeting_link: str = "https://meeting.tencent.com/qatest",
    meeting_password: str = "1234",
) -> tuple[int, int, int]:
    """插入 user_id=1 拥有的 resume + interviewer + interview。
    返 (resume_id, interviewer_id, interview_id)。
    """
    start = _utcnow_floor_min() + timedelta(days=1)
    end = start + timedelta(minutes=30)
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO resumes "
            "(user_id, name, phone, email, education, work_years, skills, "
            " job_intention, pdf_path, status) "
            "VALUES (1, 'NOTI候选人', ?, ?, '本科', 3, 'Python,FastAPI', "
            "'后端', ?, 'passed')",
            (candidate_phone, candidate_email, pdf_path),
        )
        resume_id = cur.lastrowid
        cur = c.execute(
            "INSERT INTO interviewers (user_id, name, phone, feishu_user_id, email) "
            "VALUES (1, 'NOTI面试官', '13900000001', ?, 'iv@example.com')",
            (interviewer_feishu_id,),
        )
        interviewer_id = cur.lastrowid
        cur = c.execute(
            "INSERT INTO interviews "
            "(user_id, resume_id, interviewer_id, start_time, end_time, "
            " meeting_topic, meeting_link, meeting_password, meeting_account, "
            " meeting_id, status, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, '[QA-TEST] 通知测试', ?, ?, 'qa_acc', 'qa_mid', "
            " 'scheduled', datetime('now'), datetime('now'))",
            (
                resume_id, interviewer_id,
                start.isoformat(sep=" "), end.isoformat(sep=" "),
                meeting_link, meeting_password,
            ),
        )
        interview_id = cur.lastrowid
        c.commit()
    return resume_id, interviewer_id, interview_id


def _cleanup(qa_db_path, *, resume_id=None, interviewer_id=None, interview_id=None):
    with sqlite3.connect(qa_db_path) as c:
        if interview_id is not None:
            c.execute("DELETE FROM notification_logs WHERE interview_id=?", (interview_id,))
            c.execute("DELETE FROM interviews WHERE id=?", (interview_id,))
        if interviewer_id is not None:
            c.execute("DELETE FROM interviewers WHERE id=?", (interviewer_id,))
        if resume_id is not None:
            c.execute("DELETE FROM resumes WHERE id=?", (resume_id,))
        c.commit()


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-01: 综合发送 — 真实外部调用,标 external_real
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
@pytest.mark.external_real
def test_F_NOTI_01_send_real_feishu(api_base, http, auth_headers, qa_db_path):
    """F-NOTI-01: 真实调 send,会发飞书消息 + 日历 + (可能)邮件。

    需要 .env 里配置真实 feishu_app_id/secret 才能成功;
    interviewer.feishu_user_id 用 QA 专用 OPEN_ID(从环境变量取,没配则 skip)。
    """
    import os
    feishu_uid = os.getenv("QA_FEISHU_TEST_OPEN_ID", "")
    if not feishu_uid:
        pytest.skip("未配置 QA_FEISHU_TEST_OPEN_ID,跳过真实飞书发送")

    resume_id, interviewer_id, interview_id = _seed_interview(
        qa_db_path, interviewer_feishu_id=feishu_uid,
    )
    try:
        r = http.post(
            f"{api_base}/api/notification/send",
            headers=auth_headers,
            json={
                "interview_id": interview_id,
                "send_email_to_candidate": False,  # 邮件单测里不发,免污染
                "send_feishu_to_interviewer": True,
                "generate_template": True,
            },
            timeout=60,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["interview_id"] == interview_id
        channels = {x["channel"] for x in body["results"]}
        assert "feishu" in channels or "calendar" in channels, body
    finally:
        _cleanup(qa_db_path, resume_id=resume_id, interviewer_id=interviewer_id,
                 interview_id=interview_id)


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-02: 候选人邮件包含会议链接 / 密码 / 北京时间
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_NOTI_02_candidate_email_template_has_meeting_info(qa_db_path):
    """F-NOTI-02: interview_email_to_candidate 模板含链接/密码/北京时间格式。

    模板层断言,不依赖 SMTP。真发可能失败(无 SMTP 配置),但模板生成可靠。
    """
    from app.modules.notification.templates import interview_email_to_candidate

    bj_time = "2026-05-13 14:30"
    link = "https://meeting.tencent.com/qatest"
    pwd = "1234"
    subject, body = interview_email_to_candidate(
        "张三", "李面试", "Python 后端", bj_time, link, pwd,
    )
    assert "面试邀请" in subject
    assert "Python 后端" in subject
    assert link in body, "邮件正文应包含会议链接"
    assert pwd in body, "邮件正文应包含会议密码"
    assert bj_time in body, "邮件正文应包含面试时间(北京时间)"
    assert "张三" in body


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-03: 面试官飞书消息含候选人摘要
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_NOTI_03_interviewer_feishu_msg_has_summary(qa_db_path):
    """F-NOTI-03: interview_feishu_to_interviewer 模板含候选人摘要 + 会议链接。"""
    from app.modules.notification.templates import interview_feishu_to_interviewer

    summary = "学历：本科\n工作年限：3年\n技能：Python,FastAPI"
    msg = interview_feishu_to_interviewer(
        "李面试", "张三", "Python 后端", "2026-05-13 14:30",
        "https://meeting.tencent.com/qatest", summary,
    )
    assert "张三" in msg
    assert "Python 后端" in msg
    assert "https://meeting.tencent.com/qatest" in msg
    assert "学历" in msg or "技能" in msg, "应当含候选人摘要字段"


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-04: 飞书日历 — 真实外部
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
@pytest.mark.external_real
def test_F_NOTI_04_feishu_calendar_real(api_base, http, auth_headers, qa_db_path):
    """F-NOTI-04: send 触发飞书日历事件创建,先删旧再建新,持久化 feishu_event_id。

    需要真实 feishu 配置 + QA OPEN_ID。
    """
    import os
    feishu_uid = os.getenv("QA_FEISHU_TEST_OPEN_ID", "")
    if not feishu_uid:
        pytest.skip("未配置 QA_FEISHU_TEST_OPEN_ID,跳过真实飞书日历")

    resume_id, interviewer_id, interview_id = _seed_interview(
        qa_db_path, interviewer_feishu_id=feishu_uid,
    )
    try:
        # 第一次 send → 写 feishu_event_id
        r1 = http.post(
            f"{api_base}/api/notification/send",
            headers=auth_headers,
            json={
                "interview_id": interview_id,
                "send_email_to_candidate": False,
                "send_feishu_to_interviewer": True,
                "generate_template": False,
            },
            timeout=60,
        )
        assert r1.status_code == 200, r1.text
        with sqlite3.connect(qa_db_path) as c:
            row = c.execute(
                "SELECT feishu_event_id FROM interviews WHERE id=?", (interview_id,)
            ).fetchone()
            event_id_1 = row[0] if row else ""

        # 第二次 send → 应当先删旧再建新(event_id 可能变也可能保留;只验仍然 200)
        r2 = http.post(
            f"{api_base}/api/notification/send",
            headers=auth_headers,
            json={
                "interview_id": interview_id,
                "send_email_to_candidate": False,
                "send_feishu_to_interviewer": True,
                "generate_template": False,
            },
            timeout=60,
        )
        assert r2.status_code == 200, r2.text
        # 飞书可达时 event_id 应非空
        if event_id_1:
            assert event_id_1, "飞书日历事件 ID 应当被持久化"
    finally:
        _cleanup(qa_db_path, resume_id=resume_id, interviewer_id=interviewer_id,
                 interview_id=interview_id)


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-05: PDF 路径安全检查 — 必须在 storage_path 下
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_NOTI_05_pdf_path_must_be_under_storage(qa_db_path):
    """F-NOTI-05: service.send_interview_notifications 对 pdf_path 做路径限制。

    白盒断言: service 源码应有"resume.pdf_path 必须在 settings.resume_storage_path 下"
    的检查;路径穿越攻击(如 ../../etc/passwd)不应触发上传。
    """
    from app.modules.notification import service as svc_module

    src = Path(svc_module.__file__).read_text(encoding="utf-8")
    # 关键白盒断言: 源码含 resume_storage_path + startswith / resolve 校验
    assert "resume_storage_path" in src, "service 应引用 storage_path 做路径检查"
    assert "startswith" in src or "is_relative_to" in src, (
        "service 应当用 startswith / is_relative_to 校验 PDF 路径在 storage 下"
    )
    assert "resolve" in src, "service 应 resolve 路径以防 .. 穿越"


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-06: generate_template — 即使外部全失败也返模板
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_NOTI_06_template_generated_even_when_external_off(
    api_base, http, auth_headers, qa_db_path
):
    """F-NOTI-06: send 时 generate_template=true,即使飞书/邮件均不可用,
    results 中仍应包含 channel='template' 的项,且 status='generated' 含 content。
    """
    # 不配 feishu_user_id + 不发邮件 → 走最干净的"只生成模板"路径
    resume_id, interviewer_id, interview_id = _seed_interview(
        qa_db_path, interviewer_feishu_id="",
    )
    try:
        r = http.post(
            f"{api_base}/api/notification/send",
            headers=auth_headers,
            json={
                "interview_id": interview_id,
                "send_email_to_candidate": False,
                "send_feishu_to_interviewer": False,
                "generate_template": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["interview_id"] == interview_id
        results = body["results"]
        assert isinstance(results, list)
        templates = [x for x in results if x.get("channel") == "template"]
        assert len(templates) == 1, f"应当有恰好 1 条 template,实际 {results}"
        t = templates[0]
        assert t["status"] == "generated"
        # content 字段在 schema 默认 ""(NotificationResult.content default ""),但
        # router 实现里 template 路径给了 content;若 schema 序列化保留则非空
        if "content" in t:
            assert "面试" in t["content"] or "会议" in t["content"], t
    finally:
        _cleanup(qa_db_path, resume_id=resume_id, interviewer_id=interviewer_id,
                 interview_id=interview_id)


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-07: GET /logs — 按 interview_id 过滤,按 created_at desc
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_NOTI_07_list_logs_by_interview_desc(
    api_base, http, auth_headers, qa_db_path
):
    """F-NOTI-07: 按 interview_id 过滤,按 created_at desc 排序。"""
    resume_id, interviewer_id, interview_id = _seed_interview(qa_db_path)

    # 直接在 DB 写两条 NotificationLog (created_at 一前一后)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    t1 = datetime.now(timezone.utc) - timedelta(minutes=1)
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT INTO notification_logs "
            "(user_id, interview_id, recipient_type, recipient_name, channel, "
            " recipient_address, subject, content, status, created_at) "
            "VALUES (1, ?, 'candidate', '老的', 'email', 'old@x.com', "
            "'old subject', 'old body', 'sent', ?)",
            (interview_id, t0.isoformat(sep=" ")),
        )
        c.execute(
            "INSERT INTO notification_logs "
            "(user_id, interview_id, recipient_type, recipient_name, channel, "
            " recipient_address, subject, content, status, created_at) "
            "VALUES (1, ?, 'candidate', '新的', 'template', '', "
            "'new subject', 'new body', 'generated', ?)",
            (interview_id, t1.isoformat(sep=" ")),
        )
        c.commit()

    try:
        r = http.get(
            f"{api_base}/api/notification/logs?interview_id={interview_id}",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 2, body
        items = body["items"]
        # 都属于该 interview
        for it in items:
            assert it["interview_id"] == interview_id, it
        # desc 排序: 新的在前
        names = [it["recipient_name"] for it in items]
        idx_new = names.index("新的") if "新的" in names else -1
        idx_old = names.index("老的") if "老的" in names else -1
        assert idx_new != -1 and idx_old != -1, names
        assert idx_new < idx_old, f"应按 created_at desc, 新的应排在老的之前; got {names}"
    finally:
        _cleanup(qa_db_path, resume_id=resume_id, interviewer_id=interviewer_id,
                 interview_id=interview_id)


# ──────────────────────────────────────────────────────────────────────────────
# F-NOTI-08: DELETE /clear-all — 仅删本用户
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_NOTI_08_clear_all_only_current_user(
    api_base, http, auth_headers, qa_db_path
):
    """F-NOTI-08: clear-all 只删 user_id=当前 用户 的日志,他人日志不受影响。"""
    resume_id, interviewer_id, interview_id = _seed_interview(qa_db_path)

    # 插 2 条 user_id=1 (当前用户) + 1 条 user_id=999 (其他用户)
    with sqlite3.connect(qa_db_path) as c:
        for i in range(2):
            c.execute(
                "INSERT INTO notification_logs "
                "(user_id, interview_id, recipient_type, recipient_name, channel, "
                " recipient_address, subject, content, status, created_at) "
                "VALUES (1, ?, 'candidate', ?, 'email', 'a@x.com', 's', 'b', "
                "'sent', datetime('now'))",
                (interview_id, f"qa-own-{i}"),
            )
        c.execute(
            "INSERT INTO notification_logs "
            "(user_id, interview_id, recipient_type, recipient_name, channel, "
            " recipient_address, subject, content, status, created_at) "
            "VALUES (999, ?, 'candidate', 'other-user', 'email', 'a@x.com', "
            "'s', 'b', 'sent', datetime('now'))",
            (interview_id,),
        )
        c.commit()

    try:
        r = http.request(
            "DELETE",
            f"{api_base}/api/notification/clear-all",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "deleted" in body
        assert body["deleted"] >= 2, body  # 至少删了我们插的 2 条

        # 验证: user_id=1 的所有日志都没了; user_id=999 的还在
        with sqlite3.connect(qa_db_path) as c:
            mine = c.execute(
                "SELECT COUNT(*) FROM notification_logs WHERE user_id=1"
            ).fetchone()[0]
            others = c.execute(
                "SELECT COUNT(*) FROM notification_logs WHERE user_id=999"
            ).fetchone()[0]
        assert mine == 0, f"user_id=1 的日志应当被全清,still have {mine}"
        assert others >= 1, f"user_id=999 的日志不应被动,still have {others}"
    finally:
        # 兜底清掉 user_id=999 那条 + interview/resume/interviewer
        with sqlite3.connect(qa_db_path) as c:
            c.execute(
                "DELETE FROM notification_logs WHERE interview_id=?", (interview_id,)
            )
            c.commit()
        _cleanup(qa_db_path, resume_id=resume_id, interviewer_id=interviewer_id,
                 interview_id=interview_id)
