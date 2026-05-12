"""6 章 AI 智能筛选 (F-AISCR-01..09)。

QA 清单参考: docs/QA-系统功能清单-v1.md 第 192-204 行。

数据模型:
  IntakeCandidate (pdf_path / status / promoted_resume_id) →
  Resume (promoted) →
  MatchingResult (hard_gate_passed=1) →
  → 候选池 (eligible)

注意:
- F-AISCR-02 启动会真调 claude CLI 子进程, 启动后立即 cancel 验证流程,不等结果。
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from typing import Optional

import pytest


# ---------- helpers ---------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _ensure_user(qa_db_path, user_id: int = 1) -> None:
    """conftest 没把 ensure_qa_user 注册成 autouse,本模块自建.
    BUG: PRAGMA foreign_keys=ON, ScreeningJob.user_id FK users.id;
    缺 users 行 → start 里 db.flush 抛 IntegrityError → 被 except 块翻成
    already_running 让用户摸不着头脑。"""
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, "
            "display_name, is_active, created_at, daily_cap) "
            "VALUES (?, 'qa_user', 'x', 'QA', 1, datetime('now'), 100)",
            (user_id,),
        )
        c.commit()


def _insert_job(qa_db_path, *, user_id: int = 1, title: str | None = None) -> int:
    _ensure_user(qa_db_path, user_id)
    title = title or _unique("AISCR-Job")
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO jobs (user_id, title, education_min, school_tier_min, "
            "work_years_min, work_years_max, is_active, jd_text, "
            "competency_model, competency_model_status, "
            "greet_threshold, created_at, updated_at) "
            "VALUES (?, ?, '本科', '', 0, 99, 1, 'AI 筛选测试 JD', "
            "'{\"hard_skills\":[]}', 'approved', 60, "
            "datetime('now'), datetime('now'))",
            (user_id, title),
        )
        c.commit()
        return cur.lastrowid


def _insert_resume(qa_db_path, *, user_id: int = 1, name: str = "Cand") -> int:
    """Resume.promoted_resume_id 后续用. status=passed."""
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO resumes (user_id, name, phone, education, work_years, "
            "skills, raw_text, status, ai_parsed, seniority, boss_id, "
            "greet_status, intake_status, created_at, updated_at) "
            "VALUES (?, ?, '', '本科', 3, 'Python', 'Python 工程师', "
            "'passed', 'yes', '', '', 'none', 'collecting', "
            "datetime('now'), datetime('now'))",
            (user_id, name),
        )
        c.commit()
        return cur.lastrowid


def _insert_candidate(
    qa_db_path,
    *,
    user_id: int = 1,
    job_id: int,
    name: str = "C",
    pdf_path: str = "/tmp/qa_aiscr.pdf",
    status: str = "pending",
    boss_id: Optional[str] = None,
) -> tuple[int, int]:
    """灌一个 IntakeCandidate + 对应 promoted_resume + MatchingResult(hard_gate=1)。
    返 (candidate_id, resume_id)。pdf_path 设空字符串可触发 BUG-100 排除。"""
    resume_id = _insert_resume(qa_db_path, user_id=user_id, name=name)
    boss_id = boss_id or _unique("boss")
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO intake_candidates (user_id, boss_id, name, phone, email, "
            "job_id, intake_status, status, reject_reason, source, pdf_path, "
            "education, bachelor_school, master_school, phd_school, school_tier, "
            "work_years, skills, work_experience, project_experience, "
            "self_evaluation, seniority, expected_salary_min, expected_salary_max, "
            "qr_code_path, ai_parsed, ai_summary, greet_status, promoted_resume_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, '', '', ?, 'completed', ?, '', 'plugin', ?, '本科', "
            "'', '', '', '', 3, 'Python', '', '', '', '', 0, 0, '', 'yes', '', "
            "'none', ?, datetime('now'), datetime('now'))",
            (user_id, boss_id, name, job_id, status, pdf_path, resume_id),
        )
        candidate_id = cur.lastrowid
        # 对应 MatchingResult, hard_gate_passed=1
        c.execute(
            "INSERT INTO matching_results (resume_id, job_id, total_score, "
            "skill_score, experience_score, seniority_score, education_score, "
            "industry_score, hard_gate_passed, missing_must_haves, evidence, "
            "tags, competency_hash, weights_hash, scored_at) "
            "VALUES (?, ?, 80, 80, 80, 80, 80, 80, 1, '[]', '{}', '[]', "
            "'h', 'w', datetime('now'))",
            (resume_id, job_id),
        )
        c.commit()
    return candidate_id, resume_id


def _insert_decision(qa_db_path, *, user_id: int = 1,
                     job_id: int, candidate_id: int, action: str = "rejected") -> None:
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT INTO job_candidate_decisions (user_id, job_id, candidate_id, "
            "action, decided_at, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (user_id, job_id, candidate_id, action),
        )
        c.commit()


def _cleanup_job(qa_db_path, job_id: int) -> None:
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "DELETE FROM screening_job_items WHERE screening_job_id IN "
            "(SELECT id FROM screening_jobs WHERE job_id=?)", (job_id,),
        )
        c.execute("DELETE FROM screening_jobs WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM job_candidate_decisions WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM intake_candidates WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM matching_results WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        c.commit()


def _claude_cli_available() -> bool:
    """检测 claude binary 是否可用 (与 router 内部判断一致)。"""
    try:
        from app.modules.ai_screening.cli_runner import detect_claude_cli
        return detect_claude_cli()
    except Exception:
        return False


# ===================== F-AISCR-01 候选池预览 ===============================


@pytest.mark.api
def test_F_AISCR_01_preview_eligible_count(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-01: GET /api/jobs/{id}/ai-screening/preview 返 eligible_count + has_running。"""
    job_id = _insert_job(qa_db_path)
    # 灌 2 个合格
    _insert_candidate(qa_db_path, job_id=job_id, name="A1")
    _insert_candidate(qa_db_path, job_id=job_id, name="A2")

    r = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/preview",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["eligible_count"] == 2, body
    assert body["has_running"] is False

    # job 不存在 → 404
    r2 = http.get(
        f"{api_base}/api/jobs/9999999/ai-screening/preview",
        headers=auth_headers,
    )
    assert r2.status_code == 404, r2.text

    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-02 启动筛选 ================================


@pytest.mark.api
def test_F_AISCR_02_start_validations(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-02: 启动前置校验 — threshold 越界 / 池空 / 已 running / CLI 不存在。"""
    if not _claude_cli_available():
        pytest.skip("claude CLI 不在 PATH, F-AISCR-02 仅 503 路径可测, 跳过避开 423")

    job_id = _insert_job(qa_db_path)
    cid, _ = _insert_candidate(qa_db_path, job_id=job_id, name="V1")

    # threshold 越界 (count: 大于池) → 400
    r_bad = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "count", "threshold": 999}, headers=auth_headers,
    )
    assert r_bad.status_code == 400, r_bad.text

    # ratio threshold>100 → 422 (pydantic) 或 400 (router)
    r_ratio = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "ratio", "threshold": 200}, headers=auth_headers,
    )
    assert r_ratio.status_code in (400, 422), r_ratio.text

    # 启动一次 OK (count=1) → 立即 cancel, 防 token 烧
    r_ok = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "count", "threshold": 1}, headers=auth_headers,
    )
    assert r_ok.status_code == 200, r_ok.text
    sj_id = r_ok.json()["screening_job_id"]
    assert sj_id > 0
    assert r_ok.json()["total"] >= 1

    # 二次启动 → 409 already_running
    r_dup = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "count", "threshold": 1}, headers=auth_headers,
    )
    assert r_dup.status_code == 409, r_dup.text

    # 立即 cancel 收尾 (worker spawn 是后台 daemon, cancel 标记 + terminate)
    http.post(
        f"{api_base}/api/ai-screening/{sj_id}/cancel", headers=auth_headers,
    )
    # 给 worker 几秒收尾, 避免后续测试 partial unique 冲突
    for _ in range(20):
        with sqlite3.connect(qa_db_path) as c:
            row = c.execute(
                "SELECT status FROM screening_jobs WHERE id=?", (sj_id,),
            ).fetchone()
        if row and row[0] != "running":
            break
        time.sleep(0.5)
    # 强制终态: 即使 worker 没收到 cancel (handle 已退栈), 直接置 cancelled 防后续干扰
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE screening_jobs SET status='cancelled', "
            "finished_at=datetime('now') "
            "WHERE id=? AND status='running'",
            (sj_id,),
        )
        c.commit()

    # 池空 → 422 (拿一个空池岗位测)
    job_empty = _insert_job(qa_db_path)
    r_empty = http.post(
        f"{api_base}/api/jobs/{job_empty}/ai-screening/start",
        json={"mode": "count", "threshold": 1}, headers=auth_headers,
    )
    assert r_empty.status_code == 422, r_empty.text

    _cleanup_job(qa_db_path, job_empty)
    _cleanup_job(qa_db_path, job_id)


@pytest.mark.api
def test_F_AISCR_02b_start_cli_missing_503(
    api_base, http, auth_headers, qa_db_path, monkeypatch,
):
    """F-AISCR-02 (503 路径): CLI 不存在 → 503 (router-level monkey-patch
    detect_claude_cli 不生效因为 server 已起独立进程, 改用 env 路径不存在)."""
    # 这里通过设置无效的 CLAUDE_CLI_PATH 环境变量是无法影响已经启动的 server 的；
    # 改为直接验证当前路径下若 CLI 真不存在则会返 503 (上面 test_F_AISCR_02 跳过分支)。
    if _claude_cli_available():
        pytest.skip("claude CLI 已可用,503 路径无法在不重启 server 情况下复现")

    job_id = _insert_job(qa_db_path)
    _insert_candidate(qa_db_path, job_id=job_id, name="No-CLI")
    r = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "count", "threshold": 1}, headers=auth_headers,
    )
    assert r.status_code == 503, r.text
    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-03 CLI 路径锁定 ============================


@pytest.mark.api
def test_F_AISCR_03_cli_path_locked_to_screening_job(
    api_base, http, auth_headers, qa_db_path,
):
    """F-AISCR-03: start 时把 binary 绝对路径锁到 ScreeningJob.cli_path (BUG-102)。"""
    if not _claude_cli_available():
        pytest.skip("claude CLI 不在 PATH, 启动会先返 503 无法验证 cli_path 写入")

    job_id = _insert_job(qa_db_path)
    _insert_candidate(qa_db_path, job_id=job_id, name="L1")
    r = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "count", "threshold": 1}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    sj_id = r.json()["screening_job_id"]

    with sqlite3.connect(qa_db_path) as c:
        row = c.execute(
            "SELECT cli_path FROM screening_jobs WHERE id=?", (sj_id,),
        ).fetchone()
    assert row and row[0], f"cli_path 未锁定: {row}"
    cli_path = row[0]
    # 应是绝对路径
    assert os.path.isabs(cli_path) or "claude" in cli_path.lower(), cli_path

    http.post(f"{api_base}/api/ai-screening/{sj_id}/cancel", headers=auth_headers)
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE screening_jobs SET status='cancelled', "
            "finished_at=datetime('now') "
            "WHERE id=? AND status='running'", (sj_id,),
        )
        c.commit()
    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-04 取消任务 =================================


@pytest.mark.api
def test_F_AISCR_04_cancel_sets_flag(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-04: POST /api/ai-screening/{id}/cancel → cancel_requested=1 (BUG-090)。"""
    if not _claude_cli_available():
        pytest.skip("claude CLI 不在 PATH, 无法跑真启动取消")
    job_id = _insert_job(qa_db_path)
    _insert_candidate(qa_db_path, job_id=job_id, name="X1")
    r = http.post(
        f"{api_base}/api/jobs/{job_id}/ai-screening/start",
        json={"mode": "count", "threshold": 1}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    sj_id = r.json()["screening_job_id"]

    rc = http.post(
        f"{api_base}/api/ai-screening/{sj_id}/cancel", headers=auth_headers,
    )
    assert rc.status_code == 200, rc.text
    body = rc.json()
    assert body["cancel_requested"] == 1
    assert "terminated" in body  # bool 是否真杀

    with sqlite3.connect(qa_db_path) as c:
        flag = c.execute(
            "SELECT cancel_requested FROM screening_jobs WHERE id=?", (sj_id,),
        ).fetchone()[0]
    assert flag == 1

    # 已取消的再 cancel → 400 not_running (status 转完后)
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE screening_jobs SET status='cancelled', "
            "finished_at=datetime('now') WHERE id=?", (sj_id,),
        )
        c.commit()
    rc2 = http.post(
        f"{api_base}/api/ai-screening/{sj_id}/cancel", headers=auth_headers,
    )
    assert rc2.status_code == 400, rc2.text

    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-05 当前任务 =================================


@pytest.mark.api
def test_F_AISCR_05_current(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-05: GET .../current  无任务 → status=idle; 有 running → 返该任务。"""
    job_id = _insert_job(qa_db_path)

    # 无任务 → idle
    r = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/current",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "idle"

    # 直接 INSERT 一条 running 任务 (绕过 worker)
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO screening_jobs (user_id, job_id, mode, threshold, "
            "status, total, processed, cancel_requested, started_at, "
            "created_at) "
            "VALUES (1, ?, 'count', 1, 'running', 5, 2, 0, "
            "datetime('now'), datetime('now'))",
            (job_id,),
        )
        c.commit()
        sj_id = cur.lastrowid

    r2 = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/current",
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["id"] == sj_id
    assert body["status"] == "running"
    assert body["total"] == 5 and body["processed"] == 2

    # 结束后取最新 finished
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE screening_jobs SET status='done', processed=5, "
            "finished_at=datetime('now') WHERE id=?", (sj_id,),
        )
        c.commit()
    r3 = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/current",
        headers=auth_headers,
    )
    assert r3.json()["status"] == "done"

    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-06 结果列表 =================================


@pytest.mark.api
def test_F_AISCR_06_items_not_finished_409(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-06: items - 未 finished 返 409, 完成后按 score desc。"""
    job_id = _insert_job(qa_db_path)
    cid_a, _ = _insert_candidate(qa_db_path, job_id=job_id, name="ItemA")
    cid_b, _ = _insert_candidate(qa_db_path, job_id=job_id, name="ItemB")
    cid_c, _ = _insert_candidate(qa_db_path, job_id=job_id, name="ItemC")

    # 直插 running 任务 + 3 条 items
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO screening_jobs (user_id, job_id, mode, threshold, "
            "status, total, processed, cancel_requested, started_at, created_at) "
            "VALUES (1, ?, 'count', 2, 'running', 3, 0, 0, "
            "datetime('now'), datetime('now'))",
            (job_id,),
        )
        c.commit()
        sj_id = cur.lastrowid
        for cid, score in ((cid_a, 80), (cid_b, 95), (cid_c, 70)):
            c.execute(
                "INSERT INTO screening_job_items (screening_job_id, candidate_id, "
                "pdf_path, score, reason, pass_flag, batch_no) "
                "VALUES (?, ?, '/tmp/x.pdf', ?, '', 0, 1)",
                (sj_id, cid, score),
            )
        c.commit()

    # 未 finished → 409
    r = http.get(
        f"{api_base}/api/ai-screening/{sj_id}/items", headers=auth_headers,
    )
    assert r.status_code == 409, r.text

    # 标 done 后再查
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "UPDATE screening_jobs SET status='done', processed=3, "
            "finished_at=datetime('now') WHERE id=?", (sj_id,),
        )
        c.commit()

    r2 = http.get(
        f"{api_base}/api/ai-screening/{sj_id}/items", headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    items = body["items"]
    assert len(items) == 3
    # score desc
    scores = [it["score"] for it in items]
    assert scores == sorted(scores, reverse=True), scores
    assert items[0]["score"] == 95

    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-07 决赛轮 ==================================


@pytest.mark.api
def test_F_AISCR_07_finalist_batch_no_negative(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-07: 决赛轮 items.batch_no=-1 标识 (BUG-114)。
    用 raw insert 模拟 worker 写完决赛后的状态, 验证 API 不会因 batch_no=-1 报错。"""
    job_id = _insert_job(qa_db_path)
    cid_a, _ = _insert_candidate(qa_db_path, job_id=job_id, name="FinA")

    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT INTO screening_jobs (user_id, job_id, mode, threshold, "
            "status, total, processed, cancel_requested, started_at, created_at) "
            "VALUES (1, ?, 'count', 1, 'done', 1, 1, 0, "
            "datetime('now'), datetime('now'))",
            (job_id,),
        )
        c.commit()
        sj_id = cur.lastrowid
        # 决赛 batch_no=-1
        c.execute(
            "INSERT INTO screening_job_items (screening_job_id, candidate_id, "
            "pdf_path, score, reason, pass_flag, batch_no) "
            "VALUES (?, ?, '/tmp/x.pdf', 90, 'finalist', 1, -1)",
            (sj_id, cid_a),
        )
        c.commit()

    r = http.get(
        f"{api_base}/api/ai-screening/{sj_id}/items", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["score"] == 90
    assert body["items"][0]["pass_flag"] == 1

    # 验证 FINALIST_BUFFER 常量定义为 5 (代码契约)
    from app.modules.ai_screening.worker import FINALIST_BUFFER, FINALIST_BATCH_NO
    assert FINALIST_BUFFER == 5
    assert FINALIST_BATCH_NO == -1

    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-08 空白 PDF 过滤 ============================


@pytest.mark.api
def test_F_AISCR_08_blank_pdf_excluded(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-08: pdf_path 为 '' / 仅空白都不入候选池 (BUG-100)。"""
    job_id = _insert_job(qa_db_path)
    # 1 个有 pdf, 1 个 pdf="", 1 个 pdf="   "
    _insert_candidate(qa_db_path, job_id=job_id, name="OK", pdf_path="/tmp/ok.pdf")
    _insert_candidate(qa_db_path, job_id=job_id, name="Empty", pdf_path="")
    _insert_candidate(qa_db_path, job_id=job_id, name="Blank", pdf_path="    ")

    r = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/preview",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    # 仅 OK 应进池
    assert r.json()["eligible_count"] == 1, r.json()

    _cleanup_job(qa_db_path, job_id)


# ===================== F-AISCR-09 rejected 排除 ===========================


@pytest.mark.api
def test_F_AISCR_09_rejected_excluded(api_base, http, auth_headers, qa_db_path):
    """F-AISCR-09: per-job decision=rejected 的不进候选池。"""
    job_id = _insert_job(qa_db_path)
    cid_keep, _ = _insert_candidate(qa_db_path, job_id=job_id, name="Keep")
    cid_drop, _ = _insert_candidate(qa_db_path, job_id=job_id, name="Drop")
    # 把 cid_drop 标 rejected
    _insert_decision(
        qa_db_path, job_id=job_id, candidate_id=cid_drop, action="rejected",
    )

    r = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/preview",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    # 仅 Keep 应在池里
    assert r.json()["eligible_count"] == 1, r.json()

    # 仅 passed (非 rejected) 不应排除 — 加一个 passed 决策
    cid_pass, _ = _insert_candidate(qa_db_path, job_id=job_id, name="Pass")
    _insert_decision(
        qa_db_path, job_id=job_id, candidate_id=cid_pass, action="passed",
    )
    r2 = http.get(
        f"{api_base}/api/jobs/{job_id}/ai-screening/preview",
        headers=auth_headers,
    )
    assert r2.json()["eligible_count"] == 2, r2.json()

    _cleanup_job(qa_db_path, job_id)
