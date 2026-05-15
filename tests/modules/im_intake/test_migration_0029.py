"""migration 0029: 从 MatchingResult 反推回填历史 NULL job_id —
spec-2026-05-15-job-binding T4.
"""
import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _seed_through_0028(db_path: str) -> None:
    """跑完 baseline (M2 + F2) + 0010..0028 所有迁移,得到一个可插数据的 schema.

    schema 与 tests/modules/recruit_bot/conftest.py::_seed_m2_schema 对齐 —
    包含 resumes.status, jobs.competency_model 等,确保 0020/0022 等中间
    迁移在 ALTER 时找得到目标列。
    """
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(200) NOT NULL,
            display_name VARCHAR(100) DEFAULT '',
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            title VARCHAR(200) NOT NULL,
            department VARCHAR(100) DEFAULT '',
            education_min VARCHAR(50) DEFAULT '',
            work_years_min INTEGER DEFAULT 0,
            work_years_max INTEGER DEFAULT 99,
            salary_min REAL DEFAULT 0,
            salary_max REAL DEFAULT 0,
            required_skills TEXT DEFAULT '',
            soft_requirements TEXT DEFAULT '',
            greeting_templates TEXT DEFAULT '',
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME,
            jd_text TEXT DEFAULT '' NOT NULL,
            competency_model JSON,
            competency_model_status VARCHAR(20) DEFAULT 'none' NOT NULL,
            scoring_weights JSON
        );
        CREATE TABLE resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            name VARCHAR(100) NOT NULL,
            phone VARCHAR(20) DEFAULT '',
            email VARCHAR(200) DEFAULT '',
            education VARCHAR(50) DEFAULT '',
            bachelor_school VARCHAR(200) DEFAULT '',
            master_school VARCHAR(200) DEFAULT '',
            phd_school VARCHAR(200) DEFAULT '',
            qr_code_path VARCHAR(500) DEFAULT '',
            work_years INTEGER DEFAULT 0,
            expected_salary_min REAL DEFAULT 0,
            expected_salary_max REAL DEFAULT 0,
            job_intention VARCHAR(200) DEFAULT '',
            skills TEXT DEFAULT '',
            work_experience TEXT DEFAULT '',
            project_experience TEXT DEFAULT '',
            self_evaluation TEXT DEFAULT '',
            source VARCHAR(50) DEFAULT '',
            raw_text TEXT DEFAULT '',
            pdf_path VARCHAR(500) DEFAULT '',
            status VARCHAR(20) DEFAULT 'passed',
            ai_parsed VARCHAR(10) DEFAULT 'no',
            ai_score REAL,
            ai_summary TEXT DEFAULT '',
            reject_reason VARCHAR(200) DEFAULT '',
            seniority VARCHAR(20) NOT NULL DEFAULT '',
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE audit_events (
            event_id TEXT PRIMARY KEY,
            f_stage TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            input_hash TEXT,
            output_hash TEXT,
            prompt_version TEXT,
            model_name TEXT,
            model_version TEXT,
            reviewer_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            retention_until DATETIME
        );
        CREATE TABLE matching_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resume_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            total_score INTEGER NOT NULL DEFAULT 0,
            skill_score INTEGER NOT NULL DEFAULT 0,
            experience_score INTEGER NOT NULL DEFAULT 0,
            seniority_score INTEGER NOT NULL DEFAULT 0,
            education_score INTEGER NOT NULL DEFAULT 0,
            industry_score INTEGER NOT NULL DEFAULT 0,
            hard_gate_passed INTEGER NOT NULL DEFAULT 0,
            missing_must_haves TEXT DEFAULT '[]',
            evidence TEXT DEFAULT '{}',
            tags TEXT DEFAULT '[]',
            competency_hash TEXT DEFAULT '',
            weights_hash TEXT DEFAULT '',
            scored_at DATETIME,
            job_action VARCHAR(20) DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_matching_results_resume_job
            ON matching_results(resume_id, job_id);
    """)
    conn.commit()
    conn.close()


def _cfg(db: str) -> Config:
    cfg = Config("migrations/alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.fixture
def db_at_0028(tmp_path):
    """SQLite 已 upgrade 到 0028, 等着插数据 + 跑 0029."""
    db = tmp_path / "t.db"
    _seed_through_0028(str(db))
    cfg = _cfg(str(db))
    command.stamp(cfg, "0009")
    command.upgrade(cfg, "0028")
    return str(db), cfg


def _conn(db: str) -> sqlite3.Connection:
    return sqlite3.connect(db)


def test_0029_backfills_single_match(db_at_0028):
    db, cfg = db_at_0028
    c = _conn(db)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (10, 1, 'A')")
    c.execute(
        "INSERT INTO intake_candidates (id, user_id, boss_id, name, "
        "job_id, promoted_resume_id, intake_status) "
        "VALUES (1, 1, 'b1', 'X', NULL, 100, 'complete')"
    )
    c.execute(
        "INSERT INTO resumes (id, user_id, name, boss_id, job_id) "
        "VALUES (100, 1, 'X', 'b1', NULL)"
    )
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 10, 70, 1)"
    )
    c.commit(); c.close()

    command.upgrade(cfg, "0029")

    c = _conn(db)
    cand_job = c.execute(
        "SELECT job_id FROM intake_candidates WHERE id = 1"
    ).fetchone()[0]
    res_job = c.execute(
        "SELECT job_id FROM resumes WHERE id = 100"
    ).fetchone()[0]
    c.close()

    assert cand_job == 10, f"candidate.job_id 应回填为 10, 实际 {cand_job}"
    assert res_job == 10, f"resume.job_id 应回填为 10, 实际 {res_job}"


def test_0029_picks_highest_score_when_multiple_passed(db_at_0028):
    db, cfg = db_at_0028
    c = _conn(db)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (10, 1, 'A')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (20, 1, 'B')")
    c.execute(
        "INSERT INTO intake_candidates (id, user_id, boss_id, name, "
        "job_id, promoted_resume_id, intake_status) "
        "VALUES (1, 1, 'b2', 'X', NULL, 100, 'complete')"
    )
    c.execute(
        "INSERT INTO resumes (id, user_id, name, boss_id, job_id) "
        "VALUES (100, 1, 'X', 'b2', NULL)"
    )
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 10, 60, 1)"
    )
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 20, 80, 1)"
    )
    c.commit(); c.close()

    command.upgrade(cfg, "0029")

    c = _conn(db)
    cand_job = c.execute(
        "SELECT job_id FROM intake_candidates WHERE id = 1"
    ).fetchone()[0]
    c.close()
    assert cand_job == 20, f"多 match 时选 score 最高,期望 20, 实际 {cand_job}"


def test_0029_prefers_passed_over_higher_unpassed(db_at_0028):
    """硬筛通过的优先级 > 分数更高但未过硬筛."""
    db, cfg = db_at_0028
    c = _conn(db)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (10, 1, 'A')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (20, 1, 'B')")
    c.execute(
        "INSERT INTO intake_candidates (id, user_id, boss_id, name, "
        "job_id, promoted_resume_id, intake_status) "
        "VALUES (1, 1, 'b3', 'X', NULL, 100, 'complete')"
    )
    c.execute(
        "INSERT INTO resumes (id, user_id, name, boss_id, job_id) "
        "VALUES (100, 1, 'X', 'b3', NULL)"
    )
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 10, 50, 1)"
    )
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 20, 90, 0)"
    )
    c.commit(); c.close()

    command.upgrade(cfg, "0029")

    c = _conn(db)
    cand_job = c.execute(
        "SELECT job_id FROM intake_candidates WHERE id = 1"
    ).fetchone()[0]
    c.close()
    assert cand_job == 10, (
        f"硬筛通过的优先级>未过硬筛的高分,期望 10, 实际 {cand_job}"
    )


def test_0029_skips_when_no_matching_result(db_at_0028):
    db, cfg = db_at_0028
    c = _conn(db)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    c.execute(
        "INSERT INTO intake_candidates (id, user_id, boss_id, name, "
        "job_id, promoted_resume_id, intake_status) "
        "VALUES (1, 1, 'b4', 'X', NULL, 100, 'complete')"
    )
    c.execute(
        "INSERT INTO resumes (id, user_id, name, boss_id, job_id) "
        "VALUES (100, 1, 'X', 'b4', NULL)"
    )
    c.commit(); c.close()

    command.upgrade(cfg, "0029")  # 不应炸

    c = _conn(db)
    cand_job = c.execute(
        "SELECT job_id FROM intake_candidates WHERE id = 1"
    ).fetchone()[0]
    c.close()
    assert cand_job is None, "无 MatchingResult 时 job_id 保持 NULL"


def test_0029_does_not_overwrite_existing_job_id(db_at_0028):
    db, cfg = db_at_0028
    c = _conn(db)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (5, 1, 'A')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (10, 1, 'B')")
    # candidate 已绑定 job_id=5
    c.execute(
        "INSERT INTO intake_candidates (id, user_id, boss_id, name, "
        "job_id, promoted_resume_id, intake_status) "
        "VALUES (1, 1, 'b5', 'X', 5, 100, 'complete')"
    )
    c.execute(
        "INSERT INTO resumes (id, user_id, name, boss_id, job_id) "
        "VALUES (100, 1, 'X', 'b5', 5)"
    )
    # 哪怕 MatchingResult 指向 job 10,迁移也不应覆盖 5
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 10, 99, 1)"
    )
    c.commit(); c.close()

    command.upgrade(cfg, "0029")

    c = _conn(db)
    cand_job = c.execute(
        "SELECT job_id FROM intake_candidates WHERE id = 1"
    ).fetchone()[0]
    res_job = c.execute(
        "SELECT job_id FROM resumes WHERE id = 100"
    ).fetchone()[0]
    c.close()
    assert cand_job == 5, f"已有 job_id 不被覆盖,期望 5, 实际 {cand_job}"
    assert res_job == 5, f"resume.job_id 已有不被覆盖,期望 5, 实际 {res_job}"


def test_0029_idempotent_rerun(db_at_0028):
    """重复跑迁移不应改动状态(回填后 job_id 非 NULL,二次跑 IS NULL 过滤跳过)."""
    db, cfg = db_at_0028
    c = _conn(db)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    c.execute("INSERT INTO jobs (id, user_id, title) VALUES (10, 1, 'A')")
    c.execute(
        "INSERT INTO intake_candidates (id, user_id, boss_id, name, "
        "job_id, promoted_resume_id, intake_status) "
        "VALUES (1, 1, 'b6', 'X', NULL, 100, 'complete')"
    )
    c.execute(
        "INSERT INTO resumes (id, user_id, name, boss_id, job_id) "
        "VALUES (100, 1, 'X', 'b6', NULL)"
    )
    c.execute(
        "INSERT INTO matching_results "
        "(resume_id, job_id, total_score, hard_gate_passed) "
        "VALUES (100, 10, 70, 1)"
    )
    c.commit(); c.close()

    command.upgrade(cfg, "0029")
    # 二次"调"用 (回滚到 0028 + 升回 0029) — 防止迁移路径误覆盖
    command.downgrade(cfg, "0028")
    command.upgrade(cfg, "0029")

    c = _conn(db)
    cand_job = c.execute(
        "SELECT job_id FROM intake_candidates WHERE id = 1"
    ).fetchone()[0]
    c.close()
    assert cand_job == 10, "幂等: 二次升级仍为正确值"


def test_0029_downgrade_is_noop(db_at_0028):
    """downgrade 不应抛 (回填型迁移无逆操作)."""
    db, cfg = db_at_0028
    command.upgrade(cfg, "0029")
    command.downgrade(cfg, "0028")  # 不应炸
