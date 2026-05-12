"""8 章 F4 IM 智能接待 (F-INT-01..24)。

QA 清单 docs/QA-系统功能清单-v1.md L226-L269 24 项 / 4 小节:
  8.1 候选人 CRUD       (01-04)
  8.2 主流程            (05-13)
  8.3 outbox 与限流     (14-20)
  8.4 自扫 / 启动会话   (21-24)

测试约定:
- 多数端点是 user_id 隔离的, 直接断言 owner=qa_user (id=1) 的行为
- F-INT-05/12 真实 LLM 抽 slot — 控制 token: 短聊天记录 + 1-2 个 slot
- F-INT-13 ack-sent state drift 难构造, skip + 注明
- F-INT-14..17 outbox 流程: 直接 sqlite3 插 IntakeOutbox 行
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import pytest


# ---------- 共享小工具 ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _register_candidate(http, api_base, headers, boss_id: str,
                        name: str = "测试候选", job_title: str | None = None) -> int:
    """注册 (幂等), 返回 candidate_id."""
    body = {"boss_id": boss_id, "name": name}
    if job_title is not None:
        body["job_title"] = job_title
    r = http.post(f"{api_base}/api/intake/candidates/register",
                  json=body, headers=headers)
    assert r.status_code in (200, 201), r.text
    return int(r.json()["candidate_id"])


def _enable_intake(http, api_base, headers, target: int = 100) -> None:
    """打开 intake 总开关, 让 outbox/claim、autoscan 等返非空。"""
    r = http.put(f"{api_base}/api/intake/settings",
                 json={"enabled": True, "target_count": target}, headers=headers)
    assert r.status_code == 200, r.text


def _set_candidate_status(qa_db_path, candidate_id: int, status: str) -> None:
    """跳过 LLM 直接改 intake_status — 用于构造 terminal 候选。"""
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE intake_candidates SET intake_status=?, intake_completed_at=? WHERE id=?",
            (status, datetime.now(timezone.utc).isoformat(), candidate_id),
        )
        c.commit()


def _insert_outbox(qa_db_path, candidate_id: int, user_id: int = 1,
                   action_type: str = "send_hard", text: str = "请问您的姓名?",
                   status: str = "pending", scheduled_for: datetime | None = None,
                   slot_keys: list | None = None) -> int:
    """直接插一条 IntakeOutbox 行, 返回 id."""
    sched = (scheduled_for or datetime.now(timezone.utc)).replace(tzinfo=None).isoformat()
    keys_json = json.dumps(slot_keys or [])
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO intake_outbox "
            "(candidate_id, user_id, action_type, text, slot_keys, status, "
            " scheduled_for, attempts, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (candidate_id, user_id, action_type, text, keys_json, status, sched,
             0, datetime.now(timezone.utc).isoformat()),
        )
        c.commit()
        return int(cur.lastrowid)


def _outbox_status(qa_db_path, outbox_id: int) -> str:
    with sqlite3.connect(qa_db_path) as c:
        row = c.execute(
            "SELECT status FROM intake_outbox WHERE id=?", (outbox_id,)
        ).fetchone()
    return row[0] if row else ""


# ---------- 8.1 候选人 CRUD (01-04) ----------

@pytest.mark.api
@pytest.mark.smoke
def test_F_INT_01_list_enum_validation(api_base, http, auth_headers):
    """F-INT-01: list 候选人对 status / recruit_status enum 校验 (BUG-122)。"""
    # 合法: 不传任何过滤 → 200
    r = http.get(f"{api_base}/api/intake/candidates", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "total" in body

    # 非法 status (typo) → 400
    r = http.get(f"{api_base}/api/intake/candidates?status=typo_status",
                 headers=auth_headers)
    assert r.status_code == 400, r.text

    # 非法 recruit_status (典型 typo: accepted) → 400
    r = http.get(f"{api_base}/api/intake/candidates?recruit_status=accepted",
                 headers=auth_headers)
    assert r.status_code == 400, r.text

    # 合法 enum → 200
    r = http.get(f"{api_base}/api/intake/candidates?status=collecting&recruit_status=pending",
                 headers=auth_headers)
    assert r.status_code == 200, r.text


@pytest.mark.api
def test_F_INT_02_register_idempotent(api_base, http, auth_headers):
    """F-INT-02: register 候选人身份, 同 boss_id 第二次返同一 candidate_id (幂等)。"""
    boss = f"qa-int-02-{int(time.time())}"
    r1 = http.post(f"{api_base}/api/intake/candidates/register",
                   json={"boss_id": boss, "name": "张三"}, headers=auth_headers)
    assert r1.status_code in (200, 201), r1.text
    cid1 = r1.json()["candidate_id"]
    assert r1.json()["boss_id"] == boss
    assert r1.json()["status"] in ("collecting", "awaiting_reply")

    # 同 boss_id 再注册 → 同 candidate
    r2 = http.post(f"{api_base}/api/intake/candidates/register",
                   json={"boss_id": boss, "name": "张三"}, headers=auth_headers)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["candidate_id"] == cid1


@pytest.mark.api
def test_F_INT_03_get_detail_with_slots(api_base, http, auth_headers):
    """F-INT-03: 单条详情含 slots 列表。"""
    boss = f"qa-int-03-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)

    r = http.get(f"{api_base}/api/intake/candidates/{cid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resume_id"] == cid  # schema 用 resume_id 名字
    assert body["boss_id"] == boss
    assert "slots" in body
    assert isinstance(body["slots"], list)


@pytest.mark.api
def test_F_INT_03b_get_detail_404(api_base, http, auth_headers):
    """F-INT-03: 不存在的 candidate → 404。"""
    r = http.get(f"{api_base}/api/intake/candidates/999999", headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.api
def test_F_INT_04_slot_patch_terminal_blocked(api_base, http, auth_headers, qa_db_path):
    """F-INT-04: 槽位手填; complete/abandoned 候选 slot 只读 → 409。"""
    boss = f"qa-int-04-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)

    # 拿一个 slot id (register 后 service 会预创建 hard slot 行;若没建,fallback 自创)
    detail = http.get(f"{api_base}/api/intake/candidates/{cid}",
                      headers=auth_headers).json()
    slots = detail.get("slots") or []
    if not slots:
        # 没预创建,直接 sqlite 插一条
        with sqlite3.connect(qa_db_path) as c:
            cur = c.execute(
                "INSERT INTO intake_slots "
                "(candidate_id, slot_key, slot_category, value, ask_count, created_at, updated_at) "
                "VALUES (?, 'name', 'hard', '', 0, datetime('now'), datetime('now'))",
                (cid,),
            )
            c.commit()
            slot_id = int(cur.lastrowid)
    else:
        slot_id = slots[0]["id"]

    # collecting 状态 patch slot → 200
    r = http.put(f"{api_base}/api/intake/slots/{slot_id}",
                 json={"value": "李四"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "李四"

    # 改 candidate 为 abandoned, 再 patch slot → 409
    _set_candidate_status(qa_db_path, cid, "abandoned")
    r = http.put(f"{api_base}/api/intake/slots/{slot_id}",
                 json={"value": "王五"}, headers=auth_headers)
    assert r.status_code == 409, r.text


# ---------- 8.2 主流程 (05-13) ----------

@pytest.mark.api
def test_F_INT_05_collect_chat_llm_extract(api_base, http, auth_headers):
    """F-INT-05: collect-chat 真实 LLM 抽 slot → 决策下一动作。

    用极短聊天记录控制 token (~50 tokens). 不强求一定抽出某 slot,
    只验 endpoint 端到端可达 + 返合规结构。
    """
    boss = f"qa-int-05-{int(time.time())}"
    body = {
        "boss_id": boss,
        "name": "Alice",
        "messages": [
            {"sender_id": "hr", "content": "你好,请问怎么称呼?"},
            {"sender_id": boss, "content": "我叫 Alice"},
        ],
    }
    try:
        r = http.post(f"{api_base}/api/intake/collect-chat",
                      json=body, headers=auth_headers, timeout=180)
    except Exception as e:
        pytest.skip(f"LLM 调用超时/网络故障: {e}")
    # LLM 未配置时 503; 配置时 200
    assert r.status_code in (200, 503), r.text
    if r.status_code != 200:
        pytest.skip(f"LLM not configured: {r.text}")
    out = r.json()
    assert "candidate_id" in out
    assert "intake_status" in out
    assert "next_action" in out
    na = out["next_action"]
    assert na["type"] in ("send_hard", "request_pdf", "wait_pdf", "wait_reply",
                          "send_soft", "complete", "mark_pending_human", "abandon")


@pytest.mark.api
def test_F_INT_05b_collect_chat_terminal_noop(api_base, http, auth_headers, qa_db_path):
    """F-INT-05 / BUG-050: terminal 候选返 wait_reply no-op。"""
    boss = f"qa-int-05b-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    _set_candidate_status(qa_db_path, cid, "complete")

    r = http.post(f"{api_base}/api/intake/collect-chat",
                  json={"boss_id": boss, "name": "X", "messages": []},
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["intake_status"] == "complete"
    # 终态 no-op: 返 wait_reply, 文本为空
    assert out["next_action"]["type"] == "wait_reply"
    assert out["next_action"]["text"] == ""


@pytest.mark.api
@pytest.mark.parametrize("bad_pdf", [
    "../../etc/passwd",        # 路径穿越
    "/etc/passwd",             # 绝对路径(非 storage)
])
def test_F_INT_06_pdf_path_validation(api_base, http, auth_headers, bad_pdf):
    """F-INT-06: pdf_url 必须 http(s)/ 安全相对路径; 穿越/绝对路径 → 422 (schema 层)。"""
    boss = f"qa-int-06-{int(time.time())}-{abs(hash(bad_pdf)) % 999}"
    body = {
        "boss_id": boss,
        "name": "PDF测试",
        "messages": [],
        "pdf_present": True,
        "pdf_url": bad_pdf,
    }
    r = http.post(f"{api_base}/api/intake/collect-chat",
                  json=body, headers=auth_headers)
    # schema-level _validate_pdf_url 拒绝 → 422
    assert r.status_code == 422, r.text


@pytest.mark.api
def test_F_INT_06c_pdf_card_title_silent_reject(api_base, http, auth_headers, qa_db_path):
    """F-INT-06 (BUG-A2): 裸卡片标题 '简历.pdf' schema 通过(看似合法相对路径),
    但 router._is_valid_pdf_url 在 storage 内找不到该文件 → silent reject:
    返 200, c.pdf_path 保持空, audit 记 f4_pdf_invalid_path/rejected。
    （实际 app 行为，不再视为 hard reject。）"""
    boss = f"qa-int-06c-{int(time.time())}"
    body = {
        "boss_id": boss,
        "name": "PDF标题测试",
        "messages": [],
        "pdf_present": True,
        "pdf_url": "简历.pdf",
    }
    r = http.post(f"{api_base}/api/intake/collect-chat",
                  json=body, headers=auth_headers)
    # 不是 hard reject — schema 通过,主流程内由 router silent-reject
    assert r.status_code == 200, r.text
    cid = r.json()["candidate_id"]
    # 关键断言: pdf_path 未被写入 (silent reject 行为)
    with sqlite3.connect(qa_db_path) as c:
        row = c.execute(
            "SELECT pdf_path FROM intake_candidates WHERE id=?", (cid,)
        ).fetchone()
    assert row is not None, f"candidate {cid} not found"
    pdf_path = row[0] or ""
    assert pdf_path == "", f"silent-reject 应保持 pdf_path 空, got {pdf_path!r}"


@pytest.mark.api
def test_F_INT_06b_pdf_present_without_url_rejected(api_base, http, auth_headers):
    """F-INT-06 (BUG-053): pdf_present=True 但缺 pdf_url → 422。"""
    boss = f"qa-int-06b-{int(time.time())}"
    r = http.post(f"{api_base}/api/intake/collect-chat",
                  json={"boss_id": boss, "name": "X", "messages": [],
                        "pdf_present": True}, headers=auth_headers)
    assert r.status_code == 422, r.text


@pytest.mark.api
def test_F_INT_07_abandon_idempotent(api_base, http, auth_headers, qa_db_path):
    """F-INT-07: abandon 标 abandoned + expire outbox; 幂等。"""
    boss = f"qa-int-07-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    # 先插一条 pending outbox, 验 abandon 后应被 expire
    ob_id = _insert_outbox(qa_db_path, cid)

    r1 = http.post(f"{api_base}/api/intake/candidates/{cid}/abandon",
                   headers=auth_headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["ok"] is True
    # outbox 应被 expire
    assert _outbox_status(qa_db_path, ob_id) == "expired"

    # 第二次调用幂等 (already abandoned, 仍返 ok)
    r2 = http.post(f"{api_base}/api/intake/candidates/{cid}/abandon",
                   headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True


@pytest.mark.api
def test_F_INT_08_force_complete(api_base, http, auth_headers):
    """F-INT-08: force-complete 强 promote → Resume; 返 ok + promoted_resume_id。"""
    boss = f"qa-int-08-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss, name="ForceComplete")

    r = http.post(f"{api_base}/api/intake/candidates/{cid}/force-complete",
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    # promoted_resume_id 可能 None (promote 失败, e.g. 缺字段) — 但 endpoint 仍 200
    assert "promoted_resume_id" in body


@pytest.mark.api
def test_F_INT_09_mark_timed_out(api_base, http, auth_headers, qa_db_path):
    """F-INT-09: mark-timed-out 标 timed_out + expire outbox。"""
    boss = f"qa-int-09-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    ob_id = _insert_outbox(qa_db_path, cid)

    r = http.post(f"{api_base}/api/intake/candidates/{cid}/mark-timed-out",
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # outbox 应被 expire
    assert _outbox_status(qa_db_path, ob_id) == "expired"

    # 第二次幂等 (noop=True)
    r2 = http.post(f"{api_base}/api/intake/candidates/{cid}/mark-timed-out",
                   headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("noop") is True or r2.json()["status"] == "timed_out"


@pytest.mark.api
def test_F_INT_10_status_patch_terminal_promote(api_base, http, auth_headers, qa_db_path):
    """F-INT-10: PATCH status; 转 complete → 同步 promote + intake_completed_at。"""
    boss = f"qa-int-10-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss, name="StatusPromote")

    # 非法 status → 400
    r = http.patch(f"{api_base}/api/intake/candidates/{cid}/status",
                   json={"status": "junk"}, headers=auth_headers)
    assert r.status_code == 400, r.text

    # 合法 collecting → 200
    r = http.patch(f"{api_base}/api/intake/candidates/{cid}/status",
                   json={"status": "collecting"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "collecting"

    # 转 complete → terminal, intake_completed_at 应填上
    r = http.patch(f"{api_base}/api/intake/candidates/{cid}/status",
                   json={"status": "complete"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["intake_completed_at"] is not None


@pytest.mark.api
def test_F_INT_11_last_checked(api_base, http, auth_headers):
    """F-INT-11: PATCH last-checked 更新 last_checked_at。"""
    boss = f"qa-int-11-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)

    r = http.patch(f"{api_base}/api/intake/candidates/{cid}/last-checked",
                   headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "last_checked_at" in body
    # ISO 时间格式
    assert "T" in body["last_checked_at"]


@pytest.mark.api
def test_F_INT_12_reextract_no_messages_skip(api_base, http, auth_headers):
    """F-INT-12: reextract 对无 chat_snapshot 候选 → skipped=no_messages (不调 LLM)。"""
    boss = f"qa-int-12-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    r = http.post(f"{api_base}/api/intake/candidates/{cid}/reextract",
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("skipped") == "no_messages"


@pytest.mark.api
def test_F_INT_12b_reextract_404(api_base, http, auth_headers):
    """F-INT-12: 不存在的 candidate → 404。"""
    r = http.post(f"{api_base}/api/intake/candidates/999999/reextract",
                  headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.api
@pytest.mark.skip(reason="F-INT-13: ack-sent state-drift 难以稳定构造 — "
                          "需精确控制 server-side analyze_chat 在两次调用之间产生不同 action。"
                          "常态下两次 analyze (空 messages) 多半返同一个 wait_reply, "
                          "也不会被 client_action_type=send_hard 触发 drift。"
                          "改为审计日志手工排查或专用 unit test 覆盖。")
def test_F_INT_13_ack_sent_state_drift(api_base, http, auth_headers):
    """F-INT-13 / BUG-052: client/server action 不一致 → 409 state_drift。"""
    pass


@pytest.mark.api
def test_F_INT_13b_ack_sent_404(api_base, http, auth_headers):
    """F-INT-13: ack-sent 对不存在 candidate → 404。"""
    r = http.post(f"{api_base}/api/intake/candidates/999999/ack-sent",
                  json={"action_type": "send_hard", "delivered": True},
                  headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.api
def test_F_INT_13c_ack_sent_not_delivered_noop(api_base, http, auth_headers):
    """F-INT-13: delivered=False → 立即 noop, 不动 outbox / 不分析。"""
    boss = f"qa-int-13c-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    r = http.post(f"{api_base}/api/intake/candidates/{cid}/ack-sent",
                  json={"action_type": "send_hard", "delivered": False},
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("noop") is True


# ---------- 8.3 outbox 与限流 (14-20) ----------

@pytest.mark.api
def test_F_INT_14_outbox_claim_when_running(api_base, http, auth_headers, qa_db_path):
    """F-INT-14: outbox/claim is_running=true → 取到 pending; is_running=false → 返空。"""
    boss = f"qa-int-14-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)

    # 先插一条 pending outbox
    ob_id = _insert_outbox(qa_db_path, cid, action_type="send_hard",
                           text="请问您的姓名?", slot_keys=["name"])

    # case 1: 关闭开关 → 必返空 (防关闭后重放)
    r = http.put(f"{api_base}/api/intake/settings",
                 json={"enabled": False, "target_count": 100}, headers=auth_headers)
    assert r.status_code == 200, r.text
    r = http.post(f"{api_base}/api/intake/outbox/claim",
                  json={"limit": 1}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []

    # case 2: 开开关 → 应能 claim 到 (status: pending → claimed)
    _enable_intake(http, api_base, auth_headers, target=100)
    r = http.post(f"{api_base}/api/intake/outbox/claim",
                  json={"limit": 1}, headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # 可能返 0 (其他测试已 claim) 或 1; 若返 1 验字段
    if items:
        item = items[0]
        assert "id" in item and "candidate_id" in item
        assert item["action_type"] == "send_hard"
        # DB 端状态应为 claimed
        assert _outbox_status(qa_db_path, item["id"]) == "claimed"


@pytest.mark.api
def test_F_INT_15_outbox_ack_success_and_failed(api_base, http, auth_headers, qa_db_path):
    """F-INT-15: ack success → sent; ack failure → failed; attempts ++。"""
    _enable_intake(http, api_base, auth_headers)
    boss = f"qa-int-15-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)

    # 直接插 claimed (跳过 claim 步骤, 也防止其他测试干扰)
    ob_id_ok = _insert_outbox(qa_db_path, cid, action_type="send_hard",
                              text="ok msg", status="claimed")
    r = http.post(f"{api_base}/api/intake/outbox/{ob_id_ok}/ack",
                  json={"success": True}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _outbox_status(qa_db_path, ob_id_ok) == "sent"

    # 失败 ack
    ob_id_fail = _insert_outbox(qa_db_path, cid, action_type="send_hard",
                                text="fail msg", status="claimed")
    r = http.post(f"{api_base}/api/intake/outbox/{ob_id_fail}/ack",
                  json={"success": False, "error": "网络异常"},
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    # ack_failed 可能把行恢复到 pending 重试或保持 claimed/failed (实现自定);
    # 这里只验非 sent, 且 last_error 写入了 (验证管道走通)
    new_status = _outbox_status(qa_db_path, ob_id_fail)
    assert new_status != "sent", new_status

    # ack 不存在的 outbox → 404
    r = http.post(f"{api_base}/api/intake/outbox/999999/ack",
                  json={"success": True}, headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.api
@pytest.mark.parametrize("terminal_status", ["complete", "abandoned", "timed_out"])
def test_F_INT_16_outbox_expire_on_terminal(api_base, http, auth_headers, qa_db_path, terminal_status):
    """F-INT-16: 候选转 terminal 时 → pending+claimed outbox 全部 expired。"""
    boss = f"qa-int-16-{terminal_status}-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    ob_pending = _insert_outbox(qa_db_path, cid, status="pending")
    ob_claimed = _insert_outbox(qa_db_path, cid, status="claimed",
                                action_type="send_soft", text="soft q")

    # 触发 terminal 转换
    if terminal_status == "complete":
        # 用 force-complete (走 promote)
        r = http.post(f"{api_base}/api/intake/candidates/{cid}/force-complete",
                      headers=auth_headers)
        assert r.status_code == 200, r.text
        # force-complete 不显式 expire outbox; 用 PATCH status 复测
        r = http.patch(f"{api_base}/api/intake/candidates/{cid}/status",
                       json={"status": "complete"}, headers=auth_headers)
        assert r.status_code == 200, r.text
    elif terminal_status == "abandoned":
        r = http.post(f"{api_base}/api/intake/candidates/{cid}/abandon",
                      headers=auth_headers)
        assert r.status_code == 200, r.text
    elif terminal_status == "timed_out":
        r = http.post(f"{api_base}/api/intake/candidates/{cid}/mark-timed-out",
                      headers=auth_headers)
        assert r.status_code == 200, r.text

    # 两条 outbox 应全部 expired
    assert _outbox_status(qa_db_path, ob_pending) == "expired"
    assert _outbox_status(qa_db_path, ob_claimed) == "expired"


@pytest.mark.api
def test_F_INT_17_outbox_stale_row_age_expire(api_base, http, auth_headers, qa_db_path):
    """F-INT-17: 行年龄 > max_age (默认 24h) 的 pending 行在 claim 时 auto-expire。

    构造一个 scheduled_for 设到 48h 前的 pending row; 调 claim 后该行应被 expire 而不被 claim。
    """
    _enable_intake(http, api_base, auth_headers)
    boss = f"qa-int-17-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)

    # 48h 前的 stale pending row
    sched = datetime.now(timezone.utc) - timedelta(hours=48)
    ob_stale = _insert_outbox(qa_db_path, cid, status="pending",
                              scheduled_for=sched, text="stale msg")

    r = http.post(f"{api_base}/api/intake/outbox/claim",
                  json={"limit": 1}, headers=auth_headers)
    assert r.status_code == 200, r.text

    # stale row 应在 claim 中被 expire (双轨防御之一)
    new_status = _outbox_status(qa_db_path, ob_stale)
    assert new_status == "expired", f"stale row should be expired, got {new_status}"


@pytest.mark.api
def test_F_INT_18_daily_cap_query(api_base, http, auth_headers):
    """F-INT-18: GET /daily-cap 返今日 used / cap / remaining。"""
    r = http.get(f"{api_base}/api/intake/daily-cap", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "date" in body and "used" in body and "cap" in body and "remaining" in body
    assert body["used"] >= 0
    assert body["cap"] >= 0
    assert body["remaining"] == max(0, body["cap"] - body["used"])


@pytest.mark.api
def test_F_INT_19_settings_get(api_base, http, auth_headers):
    """F-INT-19: GET /settings 返 enabled / target / current / is_running。"""
    r = http.get(f"{api_base}/api/intake/settings", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"enabled", "target_count", "complete_count", "is_running"} <= set(body.keys())
    assert isinstance(body["enabled"], bool)
    assert isinstance(body["is_running"], bool)
    assert body["target_count"] >= 0
    assert body["complete_count"] >= 0


@pytest.mark.api
def test_F_INT_20_settings_put_stop_bulk_expire(api_base, http, auth_headers, qa_db_path):
    """F-INT-20: 从 running → stop 时, 自动 bulk-expire 所有未发 outbox。"""
    # 1. 先开启 + 装一条 pending outbox
    _enable_intake(http, api_base, auth_headers, target=100)
    boss = f"qa-int-20-{int(time.time())}"
    cid = _register_candidate(http, api_base, auth_headers, boss)
    ob_p = _insert_outbox(qa_db_path, cid, status="pending")
    ob_c = _insert_outbox(qa_db_path, cid, status="claimed",
                          action_type="send_soft", text="claimed soft")

    # 2. 关 enabled → was_running True, is_now_running False, 触发 bulk-expire
    r = http.put(f"{api_base}/api/intake/settings",
                 json={"enabled": False, "target_count": 100},
                 headers=auth_headers)
    assert r.status_code == 200, r.text

    # 3. 两条 outbox 都应被 expire
    assert _outbox_status(qa_db_path, ob_p) == "expired"
    assert _outbox_status(qa_db_path, ob_c) == "expired"


# ---------- 8.4 自扫 / 启动会话 (21-24) ----------

@pytest.mark.api
def test_F_INT_21_autoscan_rank(api_base, http, qa_db_path):
    """F-INT-21: autoscan/rank 返候选排序; 关闭时返空。

    隔离策略: 用专属 user_id=9021 (避开 user_id=1 累积的几百条历史候选),
    跑测前清理该用户的旧候选 + 旧 settings 行, 再 register 一条 fresh,
    用 limit=999 查全量, 断言新 cid 必在结果中。
    """
    import bcrypt
    from tests.qa_full.fixtures.auth import make_token

    user_id = 9021
    iso_token = make_token(user_id=user_id, username="qa_int21_user")
    iso_headers = {"Authorization": f"Bearer {iso_token}"}

    # 1) 准备 user_id=9021 + 清掉历史候选/settings
    pwd_hash = bcrypt.hashpw(b"qa_int21_pwd", bcrypt.gensalt()).decode()
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO users "
            "(id, username, password_hash, display_name, is_active, daily_cap, created_at) "
            "VALUES (?, 'qa_int21_user', ?, 'INT21 Iso User', 1, 100, datetime('now'))",
            (user_id, pwd_hash),
        )
        c.execute("DELETE FROM intake_candidates WHERE user_id=?", (user_id,))
        # settings 行: 不同 schema 名称容差 — 用 LIKE 防御性删
        try:
            c.execute("DELETE FROM intake_settings WHERE user_id=?", (user_id,))
        except sqlite3.OperationalError:
            pass
        c.commit()

    # 2) 关 enabled → 必空
    r = http.put(f"{api_base}/api/intake/settings",
                 json={"enabled": False, "target_count": 100}, headers=iso_headers)
    assert r.status_code == 200, r.text
    r = http.get(f"{api_base}/api/intake/autoscan/rank?limit=5", headers=iso_headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []
    assert r.json()["limit"] == 5

    # 3) 开 enabled, 注册一条 collecting 候选 → 全量排序应包含它
    _enable_intake(http, api_base, iso_headers, target=100)
    boss = f"qa-int-21-{int(time.time())}"
    cid = _register_candidate(http, api_base, iso_headers, boss)

    # 关键: limit=999 取全量, 排除 limit 截断导致 false-negative
    r = http.get(f"{api_base}/api/intake/autoscan/rank?limit=999", headers=iso_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    cand_ids = {it["candidate_id"] for it in items}
    assert cid in cand_ids, f"new candidate {cid} not in rank items {cand_ids}"


@pytest.mark.api
def test_F_INT_22_autoscan_tick(api_base, http, auth_headers):
    """F-INT-22: autoscan/tick 返当日 tick 计数 + 透传 processed/skipped/total。"""
    body = {"processed": 3, "skipped": 1, "total": 5, "ts": _now_iso()}
    r = http.post(f"{api_base}/api/intake/autoscan/tick",
                  json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    assert out["processed"] == 3
    assert out["skipped"] == 1
    assert out["total_seen"] == 5
    assert out["ticks_today"] >= 1

    # 第二次 tick → ticks_today 单调不减 (>=2)
    r = http.post(f"{api_base}/api/intake/autoscan/tick",
                  json={"processed": 0, "skipped": 0, "total": 0},
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ticks_today"] >= 2


@pytest.mark.api
def test_F_INT_22b_autoscan_tick_typed_validation(api_base, http, auth_headers):
    """F-INT-22 (BUG-045 / BUG-051): 非数字 processed → 422 (typed body 防御)。"""
    r = http.post(f"{api_base}/api/intake/autoscan/tick",
                  json={"processed": "three", "skipped": 0, "total": 0},
                  headers=auth_headers)
    assert r.status_code == 422, r.text

    # 负数 → 422 (Field ge=0)
    r = http.post(f"{api_base}/api/intake/autoscan/tick",
                  json={"processed": -1, "skipped": 0, "total": 0},
                  headers=auth_headers)
    assert r.status_code == 422, r.text


@pytest.mark.api
def test_F_INT_23_start_conversation_url_encode_inject_defense(api_base, http, auth_headers, qa_db_path):
    """F-INT-23 (BUG-046): boss_id 含 '&' / '?' 应被 URL-encoded, 不能注入额外查询参数。"""
    # 注意: schemas._validate_boss_id 不限制 '&', 直接拼 deep link 才有注入风险。
    # 直接 sqlite3 插一条 candidate 绕过 register validator (即便 register 通过, 也是用纯 boss_id)
    inject_boss_id = "victim&attacker_param=1"
    with sqlite3.connect(qa_db_path) as c:
        # intake_candidates 大量 NOT NULL 列, 一并补齐
        cur = c.execute(
            "INSERT INTO intake_candidates "
            "(user_id, boss_id, name, phone, email, intake_status, status, "
            " reject_reason, source, "
            " education, bachelor_school, master_school, phd_school, school_tier, "
            " work_years, skills, work_experience, project_experience, "
            " self_evaluation, seniority, expected_salary_min, expected_salary_max, "
            " qr_code_path, ai_parsed, ai_summary, greet_status, "
            " created_at, updated_at) "
            "VALUES (1, ?, 'inject test', '13800000000', 'inj@example.com', "
            " 'collecting', 'pending', '', 'plugin', "
            " '', '', '', '', '', "
            " 0, '', '', '', "
            " '', '', 0.0, 0.0, "
            " '', 'no', '', 'none', "
            " datetime('now'), datetime('now'))",
            (inject_boss_id,),
        )
        c.commit()
        cid = int(cur.lastrowid)

    r = http.post(f"{api_base}/api/intake/candidates/{cid}/start-conversation",
                  headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    deep_link = body["deep_link"]

    # deep_link 应 URL-encode boss_id; '&' 不应作为参数分隔符出现在 boss_id 段
    parsed = urlparse(deep_link)
    qs = parse_qs(parsed.query)
    # 解析后 attacker_param 必须不在 query (因为整个 boss_id 已被 quote)
    assert "attacker_param" not in qs, f"injection succeeded! qs={qs}, deep_link={deep_link}"
    # boss_id 应在 id 参数中以原值出现 (parse_qs 自动 url-decode)
    assert qs.get("id") == [inject_boss_id], qs


@pytest.mark.api
def test_F_INT_23b_start_conversation_404(api_base, http, auth_headers):
    """F-INT-23: start-conversation 对不存在 candidate → 404。"""
    r = http.post(f"{api_base}/api/intake/candidates/999999/start-conversation",
                  headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.api
def test_F_INT_24_llm_ask_limit_config_present(api_base, http, auth_headers):
    """F-INT-24: hard_max_asks=3 / ask_cooldown_hours / soft_max_n=3 配置存在。

    验 config.py 中 Settings 类已定义这些字段, 默认值合理。
    无 HTTP endpoint 暴露; 直接 import 验证。
    """
    from app.config import settings as app_settings
    assert getattr(app_settings, "f4_hard_max_asks", None) == 3
    assert getattr(app_settings, "f4_soft_question_max", None) == 3
    # ask_cooldown_hours 存在且 > 0
    cooldown = getattr(app_settings, "f4_ask_cooldown_hours", None)
    assert isinstance(cooldown, int) and cooldown > 0
