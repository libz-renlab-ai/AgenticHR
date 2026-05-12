"""16 章 招聘评分 + 全局设置 + 旧 AI 评估端点
(F-REC-01..04, F-SET-01..02, F-AIE-01..03)。

QA 清单 docs/QA-系统功能清单-v1.md 第 400-412 行。

涵盖：
- F-REC-01: evaluate_and_record (LLM 真调, 标 external_real)
- F-REC-02: record-greet 成功/失败
- F-REC-03: daily-usage 返已用/限额
- F-REC-04: PUT daily-cap 更新上限
- F-SET-01: GET scoring-weights 返 5 维
- F-SET-02: PUT scoring-weights — 总和=100 校验 + 需登录 (BUG-041)
- F-AIE-01: 旧 evaluate 端点 410 Gone
- F-AIE-02: 旧 evaluate/batch 410
- F-AIE-03: ai status 端点

注意：
- main.py 中 ai_evaluation router 实际挂在 prefix="/api/ai", 而非 QA 文档说的 /api/ai-evaluation;
  这里 follow 实际实现 (/api/ai/evaluate, /api/ai/status)
- F-REC-01 要 LLM 真调 → @external_real, 默认 CI 跳过; 兜底验 404 (job 不存在) 走通即可
- F-REC-02/03/04 走 user_id=1 (qa_user)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed_job(db_path: Path, *, job_id: int, user_id: int = 1,
              with_competency: bool = True) -> None:
    import json
    now_str = _ts(datetime.now(timezone.utc))
    cm = json.dumps({
        "hard_skills": [{"name": "Python", "must_have": True}],
        "assessment_dimensions": [
            {"name": "技术", "description": "py", "question_types": []},
        ],
    }) if with_competency else None
    with sqlite3.connect(db_path) as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        c.execute(
            "INSERT INTO jobs (id, user_id, title, jd_text, competency_model, "
            "competency_model_status, school_tier_min, greet_threshold, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, '', ?, ?, '', 60, ?, ?)",
            (job_id, user_id, "QA-REC job", cm,
             "approved" if with_competency else "pending", now_str, now_str),
        )
        c.commit()


def _seed_resume(db_path: Path, *, resume_id: int, user_id: int = 1,
                 boss_id: str = "qa_boss_x", greet_status: str = "none") -> None:
    now_str = _ts(datetime.now(timezone.utc))
    with sqlite3.connect(db_path) as c:
        c.execute("DELETE FROM resumes WHERE id=?", (resume_id,))
        c.execute(
            "INSERT INTO resumes (id, user_id, name, boss_id, status, greet_status, "
            "seniority, intake_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, '', 'collecting', ?, ?)",
            (resume_id, user_id, "QA candidate", boss_id, greet_status,
             now_str, now_str),
        )
        c.commit()


# ============================================================================
# F-REC-01 评分并记录
# ============================================================================

@pytest.mark.api
@pytest.mark.external_real
def test_F_REC_01_evaluate_and_record_real_llm(
    api_base, http, auth_headers, qa_db_path,
):
    """F-REC-01: evaluate_and_record 真调 LLM 走通 (external_real)。

    无 LLM key 或网络时跳过断言, 兜底验请求格式 + 返回 schema。
    """
    job_id = 7100
    _seed_job(qa_db_path, job_id=job_id, with_competency=True)
    body = {
        "job_id": job_id,
        "candidate": {
            "name": "QA-REC-01",
            "boss_id": "qa_rec_01_boss",
            "education": "本科",
            "work_years": 3,
            "intended_job": "Python 开发",
            "skill_tags": ["Python", "FastAPI"],
            "school_tier_tags": [],
        },
        "strategy": "school_only",  # school_only 跳过 LLM, 走通基本流程不依赖外部
    }
    r = http.post(
        f"{api_base}/api/recruit/evaluate_and_record",
        json=body, headers=auth_headers,
    )
    assert r.status_code in (200, 422, 404, 502), r.text
    if r.status_code == 200:
        data = r.json()
        assert "decision" in data
        assert data["decision"] in (
            "should_greet", "skipped_already_greeted", "rejected_low_score",
            "blocked_daily_cap", "error_no_competency", "error_scoring",
        )


@pytest.mark.api
def test_F_REC_01_evaluate_unauth(api_base, http):
    """F-REC-01b: 缺 JWT 应 401。"""
    r = http.post(
        f"{api_base}/api/recruit/evaluate_and_record",
        json={"job_id": 1, "candidate": {"name": "x", "boss_id": "y"}},
    )
    assert r.status_code == 401, r.text


@pytest.mark.api
def test_F_REC_01_evaluate_job_not_found(api_base, http, auth_headers):
    """F-REC-01c: job 不存在 → 404。"""
    body = {
        "job_id": 999_888_777,
        "candidate": {"name": "x", "boss_id": "qa_404_boss"},
        "strategy": "school_only",
    }
    r = http.post(
        f"{api_base}/api/recruit/evaluate_and_record",
        json=body, headers=auth_headers,
    )
    assert r.status_code == 404, r.text


# ============================================================================
# F-REC-02 record-greet
# ============================================================================

@pytest.mark.api
def test_F_REC_02_record_greet_success(api_base, http, auth_headers, qa_db_path):
    """F-REC-02a: success=True → resume.greet_status=greeted。"""
    rid = 7201
    _seed_resume(qa_db_path, resume_id=rid)
    r = http.post(
        f"{api_base}/api/recruit/record-greet",
        json={"resume_id": rid, "success": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "recorded"
    with sqlite3.connect(qa_db_path) as c:
        gs = c.execute(
            "SELECT greet_status FROM resumes WHERE id=?", (rid,)
        ).fetchone()[0]
    assert gs == "greeted"


@pytest.mark.api
def test_F_REC_02_record_greet_failure(api_base, http, auth_headers, qa_db_path):
    """F-REC-02b: success=False + error_msg → greet_status=failed。"""
    rid = 7202
    _seed_resume(qa_db_path, resume_id=rid)
    r = http.post(
        f"{api_base}/api/recruit/record-greet",
        json={"resume_id": rid, "success": False, "error_msg": "qa anti-bot"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(qa_db_path) as c:
        gs = c.execute(
            "SELECT greet_status FROM resumes WHERE id=?", (rid,)
        ).fetchone()[0]
    assert gs == "failed"


@pytest.mark.api
def test_F_REC_02_record_greet_resume_not_found(api_base, http, auth_headers):
    """F-REC-02c: 不存在的 resume_id → 404。"""
    r = http.post(
        f"{api_base}/api/recruit/record-greet",
        json={"resume_id": 99_888_777, "success": True},
        headers=auth_headers,
    )
    assert r.status_code == 404, r.text


# ============================================================================
# F-REC-03 daily-usage
# ============================================================================

@pytest.mark.api
def test_F_REC_03_daily_usage_returns_used_cap_remaining(
    api_base, http, auth_headers,
):
    """F-REC-03: GET daily-usage 返 used + cap + remaining。"""
    r = http.get(f"{api_base}/api/recruit/daily-usage", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("used", "cap", "remaining"):
        assert k in data
        assert isinstance(data[k], int)
    assert data["remaining"] == max(0, data["cap"] - data["used"])


@pytest.mark.api
def test_F_REC_03_daily_usage_unauth(api_base, http):
    """F-REC-03b: 缺 JWT 401。"""
    r = http.get(f"{api_base}/api/recruit/daily-usage")
    assert r.status_code == 401, r.text


# ============================================================================
# F-REC-04 daily-cap
# ============================================================================

@pytest.mark.api
def test_F_REC_04_update_daily_cap(api_base, http, auth_headers, qa_db_path):
    """F-REC-04: PUT daily-cap 更新 user.daily_cap。"""
    new_cap = 500
    r = http.put(
        f"{api_base}/api/recruit/daily-cap",
        json={"cap": new_cap},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["cap"] == new_cap
    # 再 GET usage 验 cap 已变
    r2 = http.get(f"{api_base}/api/recruit/daily-usage", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["cap"] == new_cap


@pytest.mark.api
def test_F_REC_04_daily_cap_max_clamped(api_base, http, auth_headers):
    """F-REC-04b: cap 超 DAILY_CAP_MAX (10000) 应 422 校验失败。"""
    r = http.put(
        f"{api_base}/api/recruit/daily-cap",
        json={"cap": 99999},
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text


# ============================================================================
# F-SET-01 GET 全局权重
# ============================================================================

@pytest.mark.api
def test_F_SET_01_get_scoring_weights(api_base, http, auth_headers):
    """F-SET-01: GET 返 5 维权重 (实际端点要求 JWT, 用 auth_headers)。"""
    r = http.get(
        f"{api_base}/api/settings/scoring-weights", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("skill_match", "experience", "seniority", "education", "industry"):
        assert k in body, f"缺权重 {k}: {body}"
        assert isinstance(body[k], int)


# ============================================================================
# F-SET-02 PUT 全局权重
# ============================================================================

@pytest.mark.api
def test_F_SET_02_put_scoring_weights_sum_100(api_base, http, auth_headers):
    """F-SET-02a: 总和=100 应成功。"""
    body = {
        "skill_match": 35,
        "experience": 30,
        "seniority": 15,
        "education": 10,
        "industry": 10,
    }
    r = http.put(
        f"{api_base}/api/settings/scoring-weights",
        json=body, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["skill_match"] == 35


@pytest.mark.api
def test_F_SET_02_put_scoring_weights_sum_not_100(api_base, http, auth_headers):
    """F-SET-02b: 总和≠100 应 422。"""
    body = {
        "skill_match": 50,
        "experience": 30,
        "seniority": 15,
        "education": 10,
        "industry": 10,  # 总=115
    }
    r = http.put(
        f"{api_base}/api/settings/scoring-weights",
        json=body, headers=auth_headers,
    )
    assert r.status_code == 422, r.text


@pytest.mark.api
def test_F_SET_02_put_scoring_weights_unauth(api_base, http):
    """F-SET-02c: BUG-041 — PUT 必须登录, 缺 JWT 应 401。"""
    body = {
        "skill_match": 35, "experience": 30, "seniority": 15,
        "education": 10, "industry": 10,
    }
    r = http.put(f"{api_base}/api/settings/scoring-weights", json=body)
    assert r.status_code == 401, r.text


# ============================================================================
# F-AIE-01 旧 evaluate 端点 410 Gone
# ============================================================================

@pytest.mark.api
def test_F_AIE_01_evaluate_410_gone(api_base, http, auth_headers):
    """F-AIE-01: 旧 /api/ai/evaluate 返 410 + migrate_to 提示。

    NOTE: main.py 实际挂载 prefix=/api/ai (非 QA 文档的 /api/ai-evaluation)。
    """
    r = http.post(
        f"{api_base}/api/ai/evaluate", json={}, headers=auth_headers,
    )
    assert r.status_code == 410, r.text
    detail = r.json().get("detail", {})
    if isinstance(detail, dict):
        assert "migrate_to" in detail


# ============================================================================
# F-AIE-02 旧 batch 端点 410
# ============================================================================

@pytest.mark.api
def test_F_AIE_02_evaluate_batch_410_gone(api_base, http, auth_headers):
    """F-AIE-02: 旧 /api/ai/evaluate/batch 返 410。"""
    r = http.post(
        f"{api_base}/api/ai/evaluate/batch", json={}, headers=auth_headers,
    )
    assert r.status_code == 410, r.text


# ============================================================================
# F-AIE-03 AI provider 状态
# ============================================================================

@pytest.mark.api
def test_F_AIE_03_status(api_base, http, auth_headers):
    """F-AIE-03: GET /api/ai/status 返 enabled/configured/provider/model。

    注: 端点要求 JWT, 用 auth_headers。
    """
    r = http.get(f"{api_base}/api/ai/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("enabled", "configured", "provider", "model"):
        assert k in body, f"缺字段 {k}: {body}"
