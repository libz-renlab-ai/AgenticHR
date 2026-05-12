"""4 章 岗位管理与硬筛 (F-JOB-01..21, F-JOB-16 全局设置不在本文件覆盖)。

覆盖：
  4.1 岗位 CRUD       F-JOB-01..06
  4.2 硬筛           F-JOB-07..12
  4.3 评分权重        F-JOB-13..15
  4.4 能力模型生命周期 F-JOB-17..21
"""
import sqlite3
from typing import Any

import pytest


# ---------- 公用 helpers ---------------------------------------------------


def _job_payload(**override) -> dict:
    """最小可创建岗位 payload, 必要字段都给默认值。"""
    base = {
        "title": "QA Test Job",
        "department": "QA",
        "education_min": "本科",
        "school_tier_min": "",
        "work_years_min": 1,
        "work_years_max": 10,
        "salary_min": 10000,
        "salary_max": 30000,
        "required_skills": "Python, FastAPI",
        "soft_requirements": "",
        "is_active": True,
        "jd_text": "测试用 JD",
    }
    base.update(override)
    return base


def _create_job(http, api_base, auth_headers, **override) -> dict:
    payload = _job_payload(**override)
    r = http.post(f"{api_base}/api/screening/jobs", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def _delete_job(http, api_base, auth_headers, job_id: int) -> None:
    http.delete(f"{api_base}/api/screening/jobs/{job_id}", headers=auth_headers)


def _insert_resume(qa_db_path, **fields) -> int:
    """直接 insert 简历, 跳过 resume API 依赖。"""
    defaults: dict[str, Any] = {
        "user_id": 1,
        "name": "QA 候选人",
        "phone": "",
        "email": "",
        "education": "本科",
        "work_years": 3,
        "expected_salary_min": 0,
        "expected_salary_max": 0,
        "skills": "Python, FastAPI",
        "raw_text": "Python FastAPI 工程师",
        "status": "passed",
        "ai_parsed": "yes",
        # 以下 NOT NULL 列需要显式给值, sqlite raw insert 不会走 ORM default
        "seniority": "",
        "boss_id": "",
        "greet_status": "none",
        "intake_status": "collecting",
    }
    defaults.update(fields)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            f"INSERT INTO resumes ({cols}, created_at, updated_at) "
            f"VALUES ({placeholders}, datetime('now'), datetime('now'))",
            tuple(defaults.values()),
        )
        c.commit()
        return cur.lastrowid


# ===================== 4.1 岗位 CRUD =======================================


@pytest.mark.api
def test_F_JOB_01_parse_jd_llm(api_base, http, auth_headers):
    """F-JOB-01: LLM 解析 JD —— 真实 LLM 调用 (一次, 简短 JD)。"""
    jd = "Python 后端工程师, 本科以上, 3-5年, 薪资 20-35k, 必须掌握 FastAPI、PostgreSQL。"
    r = http.post(
        f"{api_base}/api/screening/jobs/parse-jd",
        json={"jd_text": jd},
        headers=auth_headers,
        timeout=60,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 不论 LLM 是否成功, 字段必须存在(失败也降级 fallback)
    for k in ("title", "department", "education_min",
              "work_years_min", "work_years_max",
              "salary_min", "salary_max",
              "required_skills", "soft_requirements", "jd_text"):
        assert k in body, f"缺字段: {k}"
    assert body["jd_text"] == jd
    assert "parse_success" in body  # 成功 True / 失败 False 都要有标志


@pytest.mark.api
def test_F_JOB_02_create_job(api_base, http, auth_headers):
    """F-JOB-02: POST /api/screening/jobs → 201。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-02-create")
    assert job["id"] > 0
    assert job["title"] == "F-JOB-02-create"
    assert job["user_id"] == 1
    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_03_list_jobs_user_isolation(api_base, http, qa_db_path):
    """F-JOB-03: 列表 user_id 隔离 + created_at desc。

    用临时 user_id=9003 隔离，避免 uid=1 历史脏数据（NULL 字段）触发
    JobListResponse 的 pydantic 校验 500。
    """
    from tests.qa_full.fixtures.auth import make_token

    iso_uid = 9003
    iso_headers = {"Authorization": f"Bearer {make_token(user_id=iso_uid, username='qa_iso_job03')}"}
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, display_name, "
            "is_active, daily_cap, created_at) VALUES (?, 'qa_iso_job03', 'x', 'ISO', 1, 100, datetime('now'))",
            (iso_uid,),
        )
        # 清掉 iso 自己以前的残留 (重跑场景)
        c.execute("DELETE FROM jobs WHERE user_id=?", (iso_uid,))
        c.commit()

    j1 = _create_job(http, api_base, iso_headers, title="F-JOB-03-A")
    j2 = _create_job(http, api_base, iso_headers, title="F-JOB-03-B")

    r = http.get(f"{api_base}/api/screening/jobs", headers=iso_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "total" in body
    ids = [x["id"] for x in body["items"]]
    # 全部属于当前 iso 用户
    assert all(x["user_id"] == iso_uid for x in body["items"])
    # 我们刚创建的两条都在
    assert j1["id"] in ids and j2["id"] in ids
    # 后建的 j2 排在 j1 前面 (desc)
    pos1 = ids.index(j1["id"])
    pos2 = ids.index(j2["id"])
    assert pos2 < pos1, f"created_at desc 失败: j2={pos2}, j1={pos1}"

    # active_only filter 不报错
    r2 = http.get(
        f"{api_base}/api/screening/jobs?active_only=true", headers=iso_headers
    )
    assert r2.status_code == 200

    _delete_job(http, api_base, iso_headers, j1["id"])
    _delete_job(http, api_base, iso_headers, j2["id"])


@pytest.mark.api
def test_F_JOB_04_get_job_cross_user_403(api_base, http, auth_headers, qa_db_path):
    """F-JOB-04: 单条获取 + 跨用户 403。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-04")

    # 自己拿自己 OK
    r = http.get(f"{api_base}/api/screening/jobs/{job['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text

    # 改 owner 模拟跨用户 (insert user 2 + 把 job 划到他)
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, "
            "display_name, is_active, created_at) "
            "VALUES (2, 'qa_user_2', 'x', 'U2', 1, datetime('now'))"
        )
        c.execute("UPDATE jobs SET user_id=2 WHERE id=?", (job["id"],))
        c.commit()

    r2 = http.get(f"{api_base}/api/screening/jobs/{job['id']}", headers=auth_headers)
    assert r2.status_code == 403, r2.text

    # 还原 owner 然后清理
    with sqlite3.connect(qa_db_path) as c:
        c.execute("UPDATE jobs SET user_id=1 WHERE id=?", (job["id"],))
        c.commit()
    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_05_update_jd_resets_competency(api_base, http, auth_headers, qa_db_path):
    """F-JOB-05: PATCH 改 JD → competency_model_status 重置 none (BUG-011)。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-05",
                      jd_text="原始 JD")
    # 把 status 直接置为 approved + 模型, 模拟已审核
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE jobs SET competency_model_status='approved', "
            "competency_model='{\"hard_skills\":[]}' WHERE id=?",
            (job["id"],),
        )
        c.commit()

    r = http.patch(
        f"{api_base}/api/screening/jobs/{job['id']}",
        json={"jd_text": "全新 JD 文本, 与原始不同"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["competency_model_status"] == "none", body
    assert body["competency_model"] in (None, {}), body

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_06_delete_with_pending_interview_409(
    api_base, http, auth_headers, qa_db_path
):
    """F-JOB-06: 有未取消面试 → 409;无则正常删除。"""
    # 6a: 直接删 → 204
    job_a = _create_job(http, api_base, auth_headers, title="F-JOB-06-A")
    r = http.delete(
        f"{api_base}/api/screening/jobs/{job_a['id']}", headers=auth_headers
    )
    assert r.status_code == 204, r.text

    # 6b: 插一场未取消面试 → 409
    job_b = _create_job(http, api_base, auth_headers, title="F-JOB-06-B")
    resume_id = _insert_resume(qa_db_path, name="F-JOB-06 候选")
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT INTO interviews (job_id, resume_id, user_id, "
            "interviewer_id, status, start_time, end_time, "
            "created_at, updated_at) "
            "VALUES (?, ?, 1, 1, 'scheduled', "
            "datetime('now', '+1 day'), datetime('now', '+1 day', '+1 hour'), "
            "datetime('now'), datetime('now'))",
            (job_b["id"], resume_id),
        )
        c.commit()

    r2 = http.delete(
        f"{api_base}/api/screening/jobs/{job_b['id']}", headers=auth_headers
    )
    assert r2.status_code == 409, r2.text

    # 清理: 把面试改 cancelled 再删
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE interviews SET status='cancelled' WHERE job_id=?", (job_b["id"],)
        )
        c.commit()
    _delete_job(http, api_base, auth_headers, job_b["id"])


# ===================== 4.2 硬筛 ===========================================


@pytest.mark.api
def test_F_JOB_07_education_threshold(api_base, http, auth_headers, qa_db_path):
    """F-JOB-07: 学历门槛 大专=1/本科=2/硕士=3/博士=4。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-07",
        education_min="硕士", required_skills="",
        work_years_min=0, work_years_max=99,
    )
    # 本科 < 硕士 → reject
    r1 = _insert_resume(qa_db_path, name="本科生", education="本科", work_years=5)
    # 博士 > 硕士 → pass
    r2 = _insert_resume(qa_db_path, name="博士生", education="博士", work_years=5)

    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/screen",
        json=[r1, r2], headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {x["resume_id"]: x for x in body["results"]}
    assert by_id[r1]["passed"] is False
    assert any("学历" in s for s in by_id[r1]["reject_reasons"]), by_id[r1]
    assert by_id[r2]["passed"] is True

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
@pytest.mark.xfail(
    reason="F-JOB-08: 服务端 hard screening 当前未实现 school_tier_min 校验",
    strict=False,
)
def test_F_JOB_08_school_tier(api_base, http, auth_headers, qa_db_path):
    """F-JOB-08: 院校等级 不限/QS200/211/985 四档。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-08",
        school_tier_min="985", required_skills="",
        education_min="", work_years_min=0, work_years_max=99,
    )
    # 普通本科 → 应 reject
    r_low = _insert_resume(
        qa_db_path, name="普通本科", education="本科",
        bachelor_school="某二本大学", work_years=3,
    )
    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/screen",
        json=[r_low], headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    by_id = {x["resume_id"]: x for x in r.json()["results"]}
    # 期待: 院校不达 985 → reject
    assert by_id[r_low]["passed"] is False
    assert any("院校" in s or "985" in s for s in by_id[r_low]["reject_reasons"])

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_09_work_years_range(api_base, http, auth_headers, qa_db_path):
    """F-JOB-09: 工作年限范围越界进 reject_reasons。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-09",
        work_years_min=3, work_years_max=8,
        required_skills="", education_min="",
    )
    r_low = _insert_resume(qa_db_path, name="新人", work_years=1)
    r_ok = _insert_resume(qa_db_path, name="刚好", work_years=5)
    r_high = _insert_resume(qa_db_path, name="资深", work_years=15)

    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/screen",
        json=[r_low, r_ok, r_high], headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    by_id = {x["resume_id"]: x for x in r.json()["results"]}
    assert by_id[r_low]["passed"] is False
    assert any("年限" in s for s in by_id[r_low]["reject_reasons"])
    assert by_id[r_ok]["passed"] is True
    assert by_id[r_high]["passed"] is False
    assert any("年限" in s for s in by_id[r_high]["reject_reasons"])

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_10_must_have_skills(api_base, http, auth_headers, qa_db_path):
    """F-JOB-10: 必备技能逐项匹配, 缺失进 reject_reasons。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-10",
        required_skills="Kubernetes, Python",
        education_min="", work_years_min=0, work_years_max=99,
    )
    r_miss = _insert_resume(
        qa_db_path, name="缺K8s", skills="Python", raw_text="Python 工程师",
    )
    r_full = _insert_resume(
        qa_db_path, name="齐全", skills="Python, Kubernetes",
        raw_text="K8s + Python",
    )
    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/screen",
        json=[r_miss, r_full], headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    by_id = {x["resume_id"]: x for x in r.json()["results"]}
    assert by_id[r_miss]["passed"] is False
    assert any("Kubernetes" in s for s in by_id[r_miss]["reject_reasons"])
    assert by_id[r_full]["passed"] is True

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_11_no_global_status_mutation(api_base, http, auth_headers, qa_db_path):
    """F-JOB-11: 硬筛只写 MatchingResult, 不改 Resume.status (BUG-064)。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-11",
        education_min="博士",  # 故意全卡掉
        required_skills="", work_years_min=0, work_years_max=99,
    )
    r_id = _insert_resume(
        qa_db_path, name="待保护", education="本科", status="passed",
    )

    # 截 status 前
    with sqlite3.connect(qa_db_path) as c:
        before = c.execute(
            "SELECT status FROM resumes WHERE id=?", (r_id,)
        ).fetchone()[0]
    assert before == "passed"

    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/screen",
        json=[r_id], headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    by_id = {x["resume_id"]: x for x in r.json()["results"]}
    assert by_id[r_id]["passed"] is False  # 学历卡掉

    with sqlite3.connect(qa_db_path) as c:
        after = c.execute(
            "SELECT status FROM resumes WHERE id=?", (r_id,)
        ).fetchone()[0]
    # 关键: 硬筛不应改全局 status
    assert after == "passed", f"BUG-064 复发: status 被改 {before} → {after}"

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_12_screen_with_resume_ids_subset(
    api_base, http, auth_headers, qa_db_path
):
    """F-JOB-12: POST /jobs/{id}/screen 可指定 resume_ids 限定范围。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-12",
        education_min="", required_skills="",
        work_years_min=0, work_years_max=99,
    )
    r1 = _insert_resume(qa_db_path, name="A12")
    r2 = _insert_resume(qa_db_path, name="B12")
    r3 = _insert_resume(qa_db_path, name="C12")

    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/screen",
        json=[r1, r3], headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {x["resume_id"] for x in body["results"]}
    assert r1 in ids and r3 in ids
    assert r2 not in ids, f"resume_ids 限定失败, 返了 {ids}"
    assert body["total"] == 2

    _delete_job(http, api_base, auth_headers, job["id"])


# ===================== 4.3 评分权重 =========================================


@pytest.mark.api
def test_F_JOB_13_get_scoring_weights(api_base, http, auth_headers):
    """F-JOB-13: GET /jobs/{id}/scoring-weights → 返 5 维 JSON。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-13")
    r = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/scoring-weights",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "weights" in body and "custom" in body
    w = body["weights"]
    for k in ("skill_match", "experience", "seniority", "education", "industry"):
        assert k in w, f"缺权重维度 {k}: {w}"

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_14_set_scoring_weights_sum_must_100(
    api_base, http, auth_headers
):
    """F-JOB-14: 5 项总和必须 = 100, 否则 422。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-14")

    # 总和 99 → 422
    bad = {"skill_match": 19, "experience": 20,
           "seniority": 20, "education": 20, "industry": 20}
    r1 = http.put(
        f"{api_base}/api/screening/jobs/{job['id']}/scoring-weights",
        json=bad, headers=auth_headers,
    )
    assert r1.status_code == 422, r1.text

    # 总和 100 → 200
    good = {"skill_match": 40, "experience": 20,
            "seniority": 10, "education": 20, "industry": 10}
    r2 = http.put(
        f"{api_base}/api/screening/jobs/{job['id']}/scoring-weights",
        json=good, headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["custom"] is True
    assert body["weights"]["skill_match"] == 40

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_15_reset_scoring_weights(api_base, http, auth_headers):
    """F-JOB-15: DELETE /scoring-weights → scoring_weights 置 null,
    后续 GET custom=false。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-15")
    # 先设
    good = {"skill_match": 40, "experience": 20,
            "seniority": 10, "education": 20, "industry": 10}
    http.put(
        f"{api_base}/api/screening/jobs/{job['id']}/scoring-weights",
        json=good, headers=auth_headers,
    )
    # 再 reset
    r = http.delete(
        f"{api_base}/api/screening/jobs/{job['id']}/scoring-weights",
        headers=auth_headers,
    )
    assert r.status_code == 204, r.text

    # GET 应回 custom=false
    r2 = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/scoring-weights",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["custom"] is False

    _delete_job(http, api_base, auth_headers, job["id"])


# ===================== 4.4 能力模型生命周期 =================================


@pytest.mark.api
def test_F_JOB_17_extract_competency_llm(api_base, http, auth_headers):
    """F-JOB-17: 抽取能力模型, 真实 LLM (一次, 简短 JD)。
    成功 → draft + HITL; 失败 → 降级 fallback。"""
    job = _create_job(
        http, api_base, auth_headers, title="F-JOB-17",
        jd_text="后端开发, 本科以上, 3年经验, 必须会 Python 和 FastAPI。",
    )
    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/competency/extract",
        json={}, headers=auth_headers, timeout=120,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 成功路径: status=draft + hitl_task_id;
    # 失败路径: status=failed + fallback=flat_form
    assert body.get("status") in ("draft", "failed"), body
    if body["status"] == "draft":
        assert body.get("hitl_task_id"), body

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_18_get_competency(api_base, http, auth_headers, qa_db_path):
    """F-JOB-18: GET .../competency 返 JSON + status。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-18")

    # 初始 → none
    r = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/competency",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "competency_model" in body and "status" in body
    assert body["status"] == "none"
    assert body["competency_model"] is None

    # 写入一份合法 model 后再 GET
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE jobs SET competency_model='{\"hard_skills\":[],"
            "\"schema_version\":1}', competency_model_status='draft' "
            "WHERE id=?",
            (job["id"],),
        )
        c.commit()
    r2 = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/competency",
        headers=auth_headers,
    )
    body2 = r2.json()
    assert body2["status"] == "draft"
    assert isinstance(body2["competency_model"], dict)

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_19_manual_competency(api_base, http, auth_headers):
    """F-JOB-19: 手填扁平字段 → schema, 直接 approved。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-19")
    flat = {
        "required_skills": "Python, FastAPI",
        "work_years_min": 2,
        "work_years_max": 8,
        "education_min": "本科",
    }
    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/competency/manual",
        json={"flat_fields": flat}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    # 再读, 确实 approved + 含 hard_skills
    r2 = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/competency",
        headers=auth_headers,
    )
    body = r2.json()
    assert body["status"] == "approved"
    assert isinstance(body["competency_model"], dict)
    assert isinstance(body["competency_model"].get("hard_skills"), list)
    assert len(body["competency_model"]["hard_skills"]) == 2

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_20_save_competency_draft(api_base, http, auth_headers):
    """F-JOB-20: PUT .../competency/save → status=draft。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-20",
                      jd_text="JD for save draft")
    cm = {
        "schema_version": 1,
        "hard_skills": [
            {"name": "Python", "weight": 5, "level": "熟练", "must_have": True}
        ],
        "soft_skills": [],
        "experience": {"years_min": 2, "years_max": 8,
                       "industries": [], "company_scale": None},
        "education": {"min_level": "本科", "preferred_level": None,
                      "prestigious_bonus": False},
        "job_level": "",
        "bonus_items": [],
        "exclusions": [],
        "assessment_dimensions": [],
        "source_jd_hash": "h1",
        "extracted_at": "2026-05-12T00:00:00Z",
    }
    r = http.put(
        f"{api_base}/api/screening/jobs/{job['id']}/competency/save",
        json={"competency_model": cm}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"

    r2 = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/competency",
        headers=auth_headers,
    )
    body = r2.json()
    assert body["status"] == "draft"
    assert body["competency_model"]["hard_skills"][0]["name"] == "Python"

    _delete_job(http, api_base, auth_headers, job["id"])


@pytest.mark.api
def test_F_JOB_21_approve_competency(api_base, http, auth_headers):
    """F-JOB-21: POST .../competency/approve → draft→approved + 触发 F2 重算。"""
    job = _create_job(http, api_base, auth_headers, title="F-JOB-21",
                      jd_text="JD for approve")
    cm = {
        "schema_version": 1,
        "hard_skills": [
            {"name": "FastAPI", "weight": 5, "level": "熟练", "must_have": True}
        ],
        "soft_skills": [],
        "experience": {"years_min": 1, "years_max": 10,
                       "industries": [], "company_scale": None},
        "education": {"min_level": "本科", "preferred_level": None,
                      "prestigious_bonus": False},
        "job_level": "",
        "bonus_items": [],
        "exclusions": [],
        "assessment_dimensions": [],
        "source_jd_hash": "h2",
        "extracted_at": "2026-05-12T00:00:00Z",
    }
    # 先 save 草稿
    http.put(
        f"{api_base}/api/screening/jobs/{job['id']}/competency/save",
        json={"competency_model": cm}, headers=auth_headers,
    )

    r = http.post(
        f"{api_base}/api/screening/jobs/{job['id']}/competency/approve",
        json={"competency_model": cm}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    r2 = http.get(
        f"{api_base}/api/screening/jobs/{job['id']}/competency",
        headers=auth_headers,
    )
    assert r2.json()["status"] == "approved"

    _delete_job(http, api_base, auth_headers, job["id"])
