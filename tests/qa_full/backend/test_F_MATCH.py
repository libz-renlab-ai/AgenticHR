"""7 章 F2 简历匹配 (F-MATCH-01..11)。

QA 清单参考: docs/QA-系统功能清单-v1.md 第 208-225 行。
所有用例先把 owner=qa_user(id=1) 的 job/resume 直接 sqlite3 插表准备数据,
再走 REST 端点 (避免依赖未必存在的数据/前置流程)。

注意:
- F-MATCH-04/05 后台任务是 in-memory dict, 起任务后立即查 status (不等完成)。
- F-MATCH-10 跑真实 LLM, 标 external_real, 默认 skip 防 token 烧。
"""
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import pytest


# ---------- 公共数据准备 ---------- #

def _insert_resume(
    db_path,
    *,
    user_id: int = 1,
    name: str = "QA Match Resume",
    education: str = "本科",
    work_years: int = 5,
    skills: str = "Python,SQL,FastAPI",
    work_experience: str = "5年互联网后端开发,负责 Python/FastAPI 服务",
    seniority: str = "P6",
    bachelor_school: str = "清华大学",
) -> int:
    """裸 INSERT Resume,返回新 id。状态 ai_parsed=yes / status=passed。"""
    with sqlite3.connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO resumes (user_id, name, phone, education, bachelor_school, "
            "work_years, skills, work_experience, seniority, ai_parsed, status, "
            "intake_status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (user_id, name, "13800000000", education, bachelor_school,
             work_years, skills, work_experience, seniority, "yes", "passed",
             "completed"),
        )
        c.commit()
        return cur.lastrowid


def _insert_job(
    db_path,
    *,
    user_id: int = 1,
    title: str = "Senior Backend",
    education_min: str = "本科",
    work_years_min: int = 3,
    work_years_max: int = 10,
    competency_model: dict | None = None,
    status: str = "approved",
    is_active: bool = True,
) -> int:
    """裸 INSERT Job + 默认能力模型,返回新 id。"""
    cm = competency_model or {
        "hard_skills": [
            {"name": "Python", "must_have": True, "weight": 5},
            {"name": "FastAPI", "must_have": False, "weight": 3},
        ],
        "experience": {"years_min": 3, "years_max": 10, "industries": ["互联网"]},
        "education": {"min": "本科"},
        "job_level": "P6",
    }
    cm_json = json.dumps(cm, ensure_ascii=False)
    with sqlite3.connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO jobs (user_id, title, education_min, school_tier_min, "
            "work_years_min, work_years_max, is_active, jd_text, "
            "competency_model, competency_model_status, "
            "greet_threshold, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (user_id, title, education_min, "", work_years_min, work_years_max,
             1 if is_active else 0, "JD: 后端高级工程师,熟悉 Python/FastAPI",
             cm_json, status, 60),
        )
        c.commit()
        return cur.lastrowid


def _cleanup_match_rows(db_path, job_id: int):
    with sqlite3.connect(db_path) as c:
        c.execute("DELETE FROM matching_results WHERE job_id=?", (job_id,))
        c.commit()


# ---------- F-MATCH-01: 单对评分 ---------- #

@pytest.mark.api
@pytest.mark.smoke
def test_F_MATCH_01_score_pair(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-01: POST /api/matching/score → 5 维分数 + hard_gate_passed + 证据。"""
    rid = _insert_resume(qa_db_path)
    jid = _insert_job(qa_db_path)
    try:
        r = http.post(
            f"{api_base}/api/matching/score",
            headers=auth_headers,
            json={"resume_id": rid, "job_id": jid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        for k in ("skill_score", "experience_score", "seniority_score",
                  "education_score", "industry_score", "total_score",
                  "hard_gate_passed", "evidence"):
            assert k in body, f"缺字段 {k}: {body}"
        assert isinstance(body["hard_gate_passed"], bool)
        assert isinstance(body["evidence"], dict)
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-02: 结果列表 ---------- #

@pytest.mark.api
def test_F_MATCH_02_results_list_filters_dead(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-02: GET /results?job_id=&page=, SQL EXISTS 过滤死候选 (BUG-097)。

    本用例验证:基本结构 + status=rejected 的 Resume 不出现在 list 中。
    """
    rid_alive = _insert_resume(qa_db_path, name="Alive Resume")
    rid_dead = _insert_resume(qa_db_path, name="Dead Resume")
    jid = _insert_job(qa_db_path)
    try:
        # 先打两次分,产生 matching_results 行
        for rid in (rid_alive, rid_dead):
            r = http.post(
                f"{api_base}/api/matching/score",
                headers=auth_headers,
                json={"resume_id": rid, "job_id": jid},
            )
            assert r.status_code == 200, r.text

        # 把其中一个 Resume 标 rejected,模拟"死候选"
        with sqlite3.connect(qa_db_path) as c:
            c.execute("UPDATE resumes SET status='rejected' WHERE id=?", (rid_dead,))
            c.commit()

        # list 应只剩 alive 那一行
        r = http.get(
            f"{api_base}/api/matching/results?job_id={jid}&page=1",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body and "total" in body
        resume_ids = {it["resume_id"] for it in body["items"]}
        assert rid_alive in resume_ids
        assert rid_dead not in resume_ids, f"rejected resume 应被过滤: {body}"
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-03: 通过候选人 ---------- #

@pytest.mark.api
def test_F_MATCH_03_passed_resumes_action_filter(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-03: GET /passed-resumes/{job_id}?action=passed|rejected|undecided。

    校验 action 参数 enum 校验生效;非法值 → 400。
    """
    jid = _insert_job(qa_db_path)
    try:
        # 合法值: 应当 200 (即使为空名单)
        for action in ("passed", "rejected", "undecided"):
            r = http.get(
                f"{api_base}/api/matching/passed-resumes/{jid}?action={action}",
                headers=auth_headers,
            )
            assert r.status_code == 200, f"action={action} → {r.status_code} {r.text}"

        # 非法值 → 400
        r = http.get(
            f"{api_base}/api/matching/passed-resumes/{jid}?action=invalid_xyz",
            headers=auth_headers,
        )
        assert r.status_code == 400, r.text
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-04: 后台重算 (in-memory) ---------- #

@pytest.mark.api
def test_F_MATCH_04_recompute_background(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-04: POST /recompute → 后台任务,立即返 task_id+total。"""
    rid = _insert_resume(qa_db_path)
    jid = _insert_job(qa_db_path)
    try:
        r = http.post(
            f"{api_base}/api/matching/recompute",
            headers=auth_headers,
            json={"job_id": jid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "task_id" in body and "total" in body, body
        assert isinstance(body["task_id"], str) and len(body["task_id"]) > 0
        assert body["total"] >= 0  # 可能 0 (硬筛 0 通过) 或更多
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-05: 任务状态 ---------- #

@pytest.mark.api
def test_F_MATCH_05_recompute_status(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-05: GET /recompute/status/{task_id} → total/completed/failed/running/current。"""
    rid = _insert_resume(qa_db_path)
    jid = _insert_job(qa_db_path)
    try:
        r = http.post(
            f"{api_base}/api/matching/recompute",
            headers=auth_headers,
            json={"job_id": jid},
        )
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]

        # 立即查 status (不等完成,只验字段齐全)
        s = http.get(
            f"{api_base}/api/matching/recompute/status/{task_id}",
            headers=auth_headers,
        )
        assert s.status_code == 200, s.text
        body = s.json()
        for k in ("task_id", "total", "completed", "failed", "running", "current"):
            assert k in body, f"缺字段 {k}: {body}"
        assert body["task_id"] == task_id

        # 不存在的 task_id → 404
        r404 = http.get(
            f"{api_base}/api/matching/recompute/status/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert r404.status_code == 404, r404.text
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-06: 决策表覆盖 (deprecated PATCH) ---------- #

@pytest.mark.api
def test_F_MATCH_06_legacy_set_action(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-06: PATCH /results/{id}/action 旧端点仍可用,与 decision_router 表原子化。"""
    rid = _insert_resume(qa_db_path)
    jid = _insert_job(qa_db_path)
    try:
        # 先打分产生 matching_result row
        r = http.post(
            f"{api_base}/api/matching/score",
            headers=auth_headers,
            json={"resume_id": rid, "job_id": jid},
        )
        assert r.status_code == 200, r.text
        result_id = r.json()["id"]

        # 标 passed
        rp = http.patch(
            f"{api_base}/api/matching/results/{result_id}/action",
            headers=auth_headers,
            json={"action": "passed"},
        )
        assert rp.status_code == 200, rp.text
        assert rp.json().get("job_action") == "passed"

        # 非法 action → 400
        rb = http.patch(
            f"{api_base}/api/matching/results/{result_id}/action",
            headers=auth_headers,
            json={"action": "garbage"},
        )
        assert rb.status_code == 400, rb.text

        # 不存在的 result_id → 404
        rn = http.patch(
            f"{api_base}/api/matching/results/9999999/action",
            headers=auth_headers,
            json={"action": "passed"},
        )
        assert rn.status_code == 404, rn.text
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-07: resume_id 翻译 (404) ---------- #

@pytest.mark.api
def test_F_MATCH_07_unknown_resume_404(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-07: 不存在的 resume_id → 404 (BUG-072)。"""
    jid = _insert_job(qa_db_path)
    try:
        r = http.post(
            f"{api_base}/api/matching/score",
            headers=auth_headers,
            json={"resume_id": 99999999, "job_id": jid},
        )
        assert r.status_code == 404, r.text
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-08: hash stale 检测 ---------- #

@pytest.mark.api
def test_F_MATCH_08_hash_stale_detection(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-08: 评分后改岗位能力模型 → list 中该 result.stale=true。"""
    rid = _insert_resume(qa_db_path)
    jid = _insert_job(qa_db_path)
    try:
        # 1) 打分
        r = http.post(
            f"{api_base}/api/matching/score",
            headers=auth_headers,
            json={"resume_id": rid, "job_id": jid},
        )
        assert r.status_code == 200, r.text
        # 单 row 响应已含 stale 字段
        first = r.json()
        assert "stale" in first
        assert first["stale"] is False, f"刚打完分应当 fresh: {first}"

        # 2) 改 competency_model → hash 变化
        new_cm = {
            "hard_skills": [{"name": "Go", "must_have": True, "weight": 5}],
            "experience": {"years_min": 1, "years_max": 5, "industries": []},
            "education": {"min": "硕士"},
            "job_level": "P7",
        }
        with sqlite3.connect(qa_db_path) as c:
            c.execute(
                "UPDATE jobs SET competency_model=? WHERE id=?",
                (json.dumps(new_cm, ensure_ascii=False), jid),
            )
            c.commit()

        # 3) list 中应当 stale=true
        r2 = http.get(
            f"{api_base}/api/matching/results?job_id={jid}&page=1",
            headers=auth_headers,
        )
        assert r2.status_code == 200, r2.text
        items = r2.json()["items"]
        assert len(items) >= 1
        assert items[0]["stale"] is True, f"改 cm 后应当 stale: {items[0]}"
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-09: 级联清理 ---------- #

@pytest.mark.api
def test_F_MATCH_09_cascade_purge_after_recompute(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-09: recompute 时 purge 不在硬筛通过集合的旧 matching_results。

    构造: rid_keep 满足硬筛, rid_drop 不满足 → 先打两次分, 再触发 recompute,
    校验 rid_drop 的旧 row 被删 (硬筛 0 通过场景下集合空也会清干净)。
    """
    # 满足硬筛的 (work_years 和 education 都满足) — 默认 job 要本科 + 3-10 年
    rid_keep = _insert_resume(
        qa_db_path, name="Keep", education="本科", work_years=5,
        skills="Python,FastAPI",
    )
    # 不满足硬筛 (学历不够) — 用大专
    rid_drop = _insert_resume(
        qa_db_path, name="Drop", education="大专", work_years=5,
        skills="Python",
    )
    jid = _insert_job(qa_db_path)
    try:
        # 先双方都打分,产生两行
        for rid in (rid_keep, rid_drop):
            http.post(
                f"{api_base}/api/matching/score",
                headers=auth_headers,
                json={"resume_id": rid, "job_id": jid},
            )

        # 触发 recompute → endpoint 内部会先 _purge_outside_hard_filter
        rp = http.post(
            f"{api_base}/api/matching/recompute",
            headers=auth_headers,
            json={"job_id": jid},
        )
        assert rp.status_code == 200, rp.text

        # 直接查 DB: rid_drop 的 row 应当被删 (硬筛已淘汰)
        with sqlite3.connect(qa_db_path) as c:
            ids = [r[0] for r in c.execute(
                "SELECT resume_id FROM matching_results WHERE job_id=?", (jid,)
            ).fetchall()]
        assert rid_drop not in ids, (
            f"硬筛淘汰的 resume {rid_drop} 仍残留在 matching_results: {ids}"
        )
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-10: LLM 证据生成降级 ---------- #

@pytest.mark.api
@pytest.mark.external_real
def test_F_MATCH_10_evidence_llm_degrade(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-10: matching_evidence_llm_enabled=False 时,evidence 走启发式 (不调 LLM)。

    本用例直接打分一次,验证 evidence dict 至少有降级形态的字段;
    real LLM 模式只跑一次 (mark external_real)。
    """
    rid = _insert_resume(qa_db_path, name="LLM Evidence Resume")
    jid = _insert_job(qa_db_path)
    try:
        r = http.post(
            f"{api_base}/api/matching/score",
            headers=auth_headers,
            json={"resume_id": rid, "job_id": jid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        ev = body.get("evidence", {})
        # 不论是否走 LLM,evidence 至少应是 dict (降级也要有启发式条目)
        assert isinstance(ev, dict)
        # build_deterministic_evidence 至少给出 5 维 key 之一
        assert any(k in ev for k in (
            "skill", "experience", "seniority", "education", "industry",
        )), f"启发式证据应至少含 5 维之一: {ev}"
    finally:
        _cleanup_match_rows(qa_db_path, jid)


# ---------- F-MATCH-11: 标签推导 ---------- #

@pytest.mark.api
def test_F_MATCH_11_derive_tags(api_base, http, auth_headers, qa_db_path):
    """F-MATCH-11: 评分后 derive_tags 按总分/硬门槛/缺失项打 tag。"""
    rid = _insert_resume(qa_db_path, name="Tag Resume")
    jid = _insert_job(qa_db_path)
    try:
        r = http.post(
            f"{api_base}/api/matching/score",
            headers=auth_headers,
            json={"resume_id": rid, "job_id": jid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # tags 字段应存在且为 list
        assert "tags" in body and isinstance(body["tags"], list), body
    finally:
        _cleanup_match_rows(qa_db_path, jid)
