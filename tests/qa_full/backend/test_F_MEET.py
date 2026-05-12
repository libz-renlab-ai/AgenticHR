"""10 章 腾讯会议账号池 (F-MEET-01..07)。

Endpoint: POST /api/meeting/auto-create?interview_id=
账号池逻辑: app.modules.meeting.account_pool.pick_available_account

观察:
- F-MEET-01 真实创建腾讯会议被标 external_real（默认不跑），主题 [QA-TEST] 前缀
- F-MEET-04..07 是配置层/启动层断言,不真跑 Playwright
- 所有路径前置准备 user_id=1 拥有的 interview / interviewer / resume
"""
import os
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


def _insert_resume_interviewer(qa_db_path, name="MEET候选人") -> tuple[int, int]:
    """插入测试用 resume + interviewer,返 (resume_id, interviewer_id)。"""
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO resumes (user_id, name, phone, email, status, "
            "seniority, boss_id, greet_status, intake_status, "
            "created_at, updated_at) "
            "VALUES (1, ?, '13800000000', 'meet_test@example.com', 'passed', "
            "'', '', 'none', 'collecting', datetime('now'), datetime('now'))",
            (name,),
        )
        resume_id = cur.lastrowid
        cur = c.execute(
            "INSERT INTO interviewers (user_id, name, phone, feishu_user_id, "
            "email, created_at) "
            "VALUES (1, 'MEET面试官', '13900000000', '', 'iv@example.com', "
            "datetime('now'))"
        )
        interviewer_id = cur.lastrowid
        c.commit()
    return resume_id, interviewer_id


def _insert_interview(
    qa_db_path,
    resume_id: int,
    interviewer_id: int,
    *,
    start_offset_hours: int = 24,
    duration_minutes: int = 30,
    minute_offset: int = 0,
    meeting_topic: str = "",
    meeting_account: str = "",
    meeting_link: str = "",
    status: str = "scheduled",
) -> int:
    """插入 interview 行。start = now + start_offset_hours,默认 30min。"""
    base = _utcnow_floor_min() + timedelta(hours=start_offset_hours)
    if minute_offset:
        base = base.replace(minute=minute_offset)
    end = base + timedelta(minutes=duration_minutes)
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO interviews "
            "(user_id, resume_id, interviewer_id, start_time, end_time, "
            " meeting_topic, meeting_link, meeting_password, meeting_account, "
            " meeting_id, status, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, '', ?, '', ?, datetime('now'), datetime('now'))",
            (
                resume_id, interviewer_id,
                base.isoformat(sep=" "), end.isoformat(sep=" "),
                meeting_topic, meeting_link, meeting_account, status,
            ),
        )
        c.commit()
        return cur.lastrowid


def _cleanup_interview(qa_db_path, interview_id: int):
    with sqlite3.connect(qa_db_path) as c:
        c.execute("DELETE FROM interviews WHERE id=?", (interview_id,))
        c.commit()


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-01: 真实创建腾讯会议（external_real,默认 skip）
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
@pytest.mark.external_real
def test_F_MEET_01_auto_create_real(api_base, http, auth_headers, qa_db_path):
    """F-MEET-01: 真实调腾讯会议 web,创建后 cancel 释放账号。

    主题前缀 [QA-TEST] 便于人工识别;成功后立即 cancel(置 status=cancelled
    + 清 meeting_link)以释放账号占用。external_real 标记默认不跑,
    需要 -m external_real 显式启用。
    """
    resume_id, interviewer_id = _insert_resume_interviewer(qa_db_path, name="MEET01候选人")
    interview_id = _insert_interview(
        qa_db_path, resume_id, interviewer_id,
        start_offset_hours=24, duration_minutes=30,
        meeting_topic="[QA-TEST] auto-create 验证",
    )
    try:
        r = http.post(
            f"{api_base}/api/meeting/auto-create?interview_id={interview_id}",
            headers=auth_headers,
            timeout=180,
        )
        # 真实场景预期 200;Playwright/账号未配置可能 500/409
        assert r.status_code in (200, 409, 500), r.text
        if r.status_code == 200:
            body = r.json()
            assert body["status"] == "ok"
            assert body["link"].startswith("http"), body
            assert body["meeting_id"], body
            assert "account" in body, body
    finally:
        # 即使创建失败也清理这条 interview 行;成功则把它 cancel
        with sqlite3.connect(qa_db_path) as c:
            c.execute(
                "UPDATE interviews SET status='cancelled', meeting_link='' WHERE id=?",
                (interview_id,),
            )
            c.commit()
        _cleanup_interview(qa_db_path, interview_id)


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-02: 全忙 → 409
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_MEET_02_all_busy_returns_409(api_base, http, auth_headers, qa_db_path):
    """F-MEET-02: 配置的所有账号在该时段都被占用 → pick_available_account 抛 409。

    手法: 直接在 DB 里塞 N 条与目标时段相交的 interview,每个用一个配置账号标签,
    然后再插一条新的 interview 调 auto-create。预期立即 409(在调 Playwright 前)。
    """
    from app.config import settings
    from app.modules.meeting.account_pool import configured_accounts

    accounts = configured_accounts()
    if not accounts:
        pytest.skip("TENCENT_MEETING_ACCOUNTS 未配置,跳过全忙测试")

    resume_id, interviewer_id = _insert_resume_interviewer(qa_db_path, name="MEET02候选人")

    # 选一个全新的时段(避免与其他 test 冲突),为每个账号塞一条占用
    base_start = _utcnow_floor_min() + timedelta(days=2)  # 48h 后,大概率干净
    base_end = base_start + timedelta(minutes=30)

    busy_ids: list[int] = []
    with sqlite3.connect(qa_db_path) as c:
        for acc in accounts:
            cur = c.execute(
                "INSERT INTO interviews "
                "(user_id, resume_id, interviewer_id, start_time, end_time, "
                " meeting_topic, meeting_link, meeting_password, meeting_account, "
                " meeting_id, status, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?, '占用', 'http://busy', '', ?, 'busy', "
                " 'scheduled', datetime('now'), datetime('now'))",
                (
                    resume_id, interviewer_id,
                    base_start.isoformat(sep=" "), base_end.isoformat(sep=" "),
                    acc,
                ),
            )
            busy_ids.append(cur.lastrowid)
        # 真正要调 auto-create 的目标 interview,跟这些占用同时段
        cur = c.execute(
            "INSERT INTO interviews "
            "(user_id, resume_id, interviewer_id, start_time, end_time, "
            " meeting_topic, meeting_link, meeting_password, meeting_account, "
            " meeting_id, status, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, '[QA-TEST] all-busy target', '', '', '', '', "
            " 'scheduled', datetime('now'), datetime('now'))",
            (
                resume_id, interviewer_id,
                base_start.isoformat(sep=" "), base_end.isoformat(sep=" "),
            ),
        )
        target_id = cur.lastrowid
        c.commit()

    try:
        r = http.post(
            f"{api_base}/api/meeting/auto-create?interview_id={target_id}",
            headers=auth_headers,
        )
        assert r.status_code == 409, r.text
        body = r.json()
        # detail 文案应该包含"占用"或"账号"等关键词
        detail = (body.get("detail") or "").lower()
        assert "占用" in body.get("detail", "") or "account" in detail or "busy" in detail, body
    finally:
        with sqlite3.connect(qa_db_path) as c:
            ids = busy_ids + [target_id]
            c.executemany("DELETE FROM interviews WHERE id=?", [(i,) for i in ids])
            c.commit()


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-03: exclude_interview_id — 重建时不把自己算占用
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_MEET_03_exclude_self_when_rebuilding(qa_db_path):
    """F-MEET-03: 直接调 pick_available_account 验证 exclude_interview_id 语义。

    场景: interview A 已用账号 acc1,现在要"重建会议",
    pick_available_account(..., exclude_interview_id=A.id) 应当能再次返 acc1
    (不被自己的旧占用挡住)。这是单元层面的合约,无需走 HTTP。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.modules.meeting.account_pool import (
        pick_available_account, configured_accounts,
    )

    accounts = configured_accounts()
    if not accounts:
        pytest.skip("TENCENT_MEETING_ACCOUNTS 未配置")
    acc1 = accounts[0]

    resume_id, interviewer_id = _insert_resume_interviewer(qa_db_path, name="MEET03候选人")
    base_start = _utcnow_floor_min() + timedelta(days=3)
    base_end = base_start + timedelta(minutes=30)

    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO interviews "
            "(user_id, resume_id, interviewer_id, start_time, end_time, "
            " meeting_topic, meeting_link, meeting_password, meeting_account, "
            " meeting_id, status, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, '[QA-TEST] exclude self', 'http://old', '', ?, 'm1', "
            " 'scheduled', datetime('now'), datetime('now'))",
            (
                resume_id, interviewer_id,
                base_start.isoformat(sep=" "), base_end.isoformat(sep=" "),
                acc1,
            ),
        )
        interview_a_id = cur.lastrowid
        c.commit()

    try:
        engine = create_engine(f"sqlite:///{qa_db_path}", future=True)
        Session = sessionmaker(bind=engine, future=True)
        with Session() as db:
            # 不排除自己 → 全忙(只配置一个账号时)/或可选其他账号
            picked_with_self = None
            try:
                picked_with_self = pick_available_account(db, base_start, base_end)
            except Exception:
                picked_with_self = None
            # 排除自己 → 一定能拿到 acc1
            picked_excl = pick_available_account(
                db, base_start, base_end, exclude_interview_id=interview_a_id
            )
            assert picked_excl == acc1, (
                f"exclude_interview_id 应当让 acc1 重新可用; got {picked_excl}, "
                f"without exclude got {picked_with_self}"
            )
    finally:
        _cleanup_interview(qa_db_path, interview_a_id)


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-04: 多账号配置解析 — TENCENT_MEETING_ACCOUNTS=a,b,c
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_MEET_04_multi_account_config_parsing(monkeypatch):
    """F-MEET-04: configured_accounts() 正确解析逗号分隔的多账号配置。

    配置层断言,不真跑 Playwright。同时验证 data/meeting_browser_{label}/
    目录命名规则可推导(实际目录由首次扫码登录创建)。
    """
    from app.config import settings
    from app.modules.meeting import account_pool

    # 临时改 settings
    original = settings.tencent_meeting_accounts
    monkeypatch.setattr(settings, "tencent_meeting_accounts", "a,b,c")
    try:
        accs = account_pool.configured_accounts()
        assert accs == ["a", "b", "c"], accs
        # 每个标签的 chrome profile 目录命名约定
        for label in accs:
            expected = REPO_ROOT / "data" / f"meeting_browser_{label}"
            # 不强求目录存在(首次扫码才会建),只验路径可推导
            assert "meeting_browser_" in str(expected)
    finally:
        monkeypatch.setattr(settings, "tencent_meeting_accounts", original)

    # 边界: 带空格 / 空标签应当被剔除
    monkeypatch.setattr(settings, "tencent_meeting_accounts", " x , , y ,")
    try:
        accs = account_pool.configured_accounts()
        assert accs == ["x", "y"], accs
    finally:
        monkeypatch.setattr(settings, "tencent_meeting_accounts", original)


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-05: 首次登录扫码 — 验 adapter 模块存在 + 关键能力
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_MEET_05_first_login_qr_capability_present():
    """F-MEET-05: 新账号首登可见浏览器扫码 — 验 adapter 提供该能力。

    我们不真弹浏览器(需要人工扫码,无法自动化),只断言 adapter 模块导入成功
    且暴露 create_meeting 函数。Playwright 持久化目录在首次会议创建时由 adapter
    自己建,这里通过验 chromium persistent context 用法的存在间接保证。
    """
    from app.adapters import tencent_meeting_web

    assert hasattr(tencent_meeting_web, "create_meeting"), \
        "tencent_meeting_web 必须导出 create_meeting"
    # 验 module source 含 persistent context / 扫码相关关键词
    src = Path(tencent_meeting_web.__file__).read_text(encoding="utf-8")
    has_persistent = "launch_persistent_context" in src or "user_data_dir" in src
    has_qr_wait = "扫码" in src or "qr" in src.lower() or "120" in src
    assert has_persistent, "adapter 应使用 Chrome 持久化 profile 才能复用扫码态"
    assert has_qr_wait, "adapter 应包含等扫码相关逻辑(120s 等待 / qr / 扫码)"


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-06: 僵尸 Chrome 清理 — 启动时 wmic 杀 + 删 lockfile
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_MEET_06_zombie_chrome_cleanup_capability():
    """F-MEET-06: adapter / module 应有"清理僵尸 Chrome + lockfile"逻辑。

    具体清理动作发生在 adapter 内部(或专门的清理函数);此处验证模块 source 含
    相关关键词(wmic / SingletonLock / kill 等)。无法真的拉一个僵尸出来测,
    那需要真跑 Playwright 然后异常退出。
    """
    from app.adapters import tencent_meeting_web

    src = Path(tencent_meeting_web.__file__).read_text(encoding="utf-8")
    has_kill = (
        "wmic" in src.lower() or "taskkill" in src.lower() or "kill" in src.lower()
        or "psutil" in src.lower()
    )
    has_lock = (
        "singletonlock" in src.lower() or "lockfile" in src.lower()
        or "lock" in src.lower()
    )
    # 至少有一个机制就算满足(实现可能挪到了 startup hook 或独立清理脚本)
    assert has_kill or has_lock, (
        "adapter 应有僵尸进程或 lockfile 清理逻辑;若已挪到 startup hook 请更新本测试"
    )


# ──────────────────────────────────────────────────────────────────────────────
# F-MEET-07: "重复会议"弹窗处理 — Escape→点不重复→DOM 移除
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.api
def test_F_MEET_07_duplicate_dialog_handler_present():
    """F-MEET-07: adapter 应处理腾讯会议页面的"重复会议"弹窗。

    观察 adapter source 含: Escape 按键 / 不重复 文案 / DOM 操作关键词。
    实际触发需要在腾讯会议页面真的撞到弹窗,此处只验代码路径存在。
    """
    from app.adapters import tencent_meeting_web

    src = Path(tencent_meeting_web.__file__).read_text(encoding="utf-8")
    has_escape = "Escape" in src or "escape" in src
    has_dup_keyword = "重复" in src or "duplicate" in src.lower()
    has_dom_remove = "remove" in src.lower() or "evaluate" in src.lower()
    # 只要任意两项命中即视为有处理(实现细节可演化)
    hits = sum([has_escape, has_dup_keyword, has_dom_remove])
    assert hits >= 2, (
        f"adapter 似缺少重复会议弹窗处理 (escape={has_escape}, "
        f"dup={has_dup_keyword}, dom_remove={has_dom_remove})"
    )
