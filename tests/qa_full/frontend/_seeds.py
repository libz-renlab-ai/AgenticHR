"""共用的前端 UI 测试数据预置 helper.

每个 helper 都是 idempotent: 多次调用只插一份数据 (用 _unique 名字避免冲突).
所有 helper 都接受 qa_db_path (sqlite3 connect path) 直接灌库, 不走 HTTP.

用法:
    from tests.qa_full.frontend._seeds import seed_for_competency
    seed_for_competency(qa_db_path)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Optional


def _unique(prefix: str = "qa") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _ensure_user(qa_db_path, user_id: int = 1) -> None:
    """与 backend/test_F_AISCR.py::_ensure_user 一致: 保证 users 行存在."""
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, "
            "display_name, is_active, created_at, daily_cap) "
            "VALUES (?, 'qa_user', 'x', 'QA', 1, datetime('now'), 100)",
            (user_id,),
        )
        c.commit()


# ===================== Competency / Job 预置 =====================

def seed_for_competency(qa_db_path, *, user_id: int = 1) -> int:
    """灌一条 jobs 行, competency_model 含 hard_skills + soft_skills + 教育/经验.

    competency_model_status='draft' (待审) 让 CompetencyEditor 渲染状态徽章 + 各栏目.
    返 job_id.
    """
    _ensure_user(qa_db_path, user_id)
    cm = {
        "hard_skills": [
            {"name": "Python", "level": "advanced", "must_have": True},
            {"name": "FastAPI", "level": "intermediate", "must_have": True},
            {"name": "PostgreSQL", "level": "intermediate", "must_have": False},
        ],
        "soft_skills": [
            {"name": "团队协作", "level": "高"},
            {"name": "沟通能力", "level": "高"},
        ],
        "experience_years": 3,
        "education_min": "本科",
        "industries": ["互联网", "金融"],
    }
    title = _unique("CMP-Job")
    with sqlite3.connect(qa_db_path) as c:
        # 检查是否已存在 (重复调用幂等)
        cur = c.execute(
            "SELECT id FROM jobs WHERE user_id=? AND competency_model_status='draft' "
            "AND title LIKE 'CMP-Job-%' LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur = c.execute(
            "INSERT INTO jobs (user_id, title, department, education_min, "
            "school_tier_min, work_years_min, work_years_max, is_active, "
            "jd_text, competency_model, competency_model_status, "
            "greet_threshold, created_at, updated_at) "
            "VALUES (?, ?, '研发部', '本科', '', 3, 99, 1, ?, ?, 'draft', 60, "
            "datetime('now'), datetime('now'))",
            (user_id, title,
             "我们正在招聘一名后端工程师, 负责设计和实现 Python 服务...",
             json.dumps(cm, ensure_ascii=False)),
        )
        c.commit()
        return cur.lastrowid


# ===================== Skills 库 预置 =====================

def seed_for_skills(qa_db_path, n: int = 3) -> list[int]:
    """确保 skills 表有 n 条非 seed 来源的行 (可点 '合并' 触发 SkillPicker).

    返已存在 / 新插入的 skill id 列表.
    """
    ids: list[int] = []
    with sqlite3.connect(qa_db_path) as c:
        # 先看现有非 seed
        cur = c.execute(
            "SELECT id FROM skills WHERE source != 'seed' LIMIT ?", (n,),
        )
        existing = [r[0] for r in cur.fetchall()]
        ids.extend(existing)
        need = n - len(existing)
        for i in range(need):
            name = _unique("SKL")
            cur = c.execute(
                "INSERT INTO skills (canonical_name, aliases, category, "
                "source, pending_classification, usage_count, "
                "created_at, updated_at) "
                "VALUES (?, '[]', 'language', 'manual', 0, 1, "
                "datetime('now'), datetime('now'))",
                (name,),
            )
            ids.append(cur.lastrowid)
        c.commit()
    return ids


# ===================== AI Screening 预置 =====================

def seed_for_ai_screening(qa_db_path, *, user_id: int = 1) -> int:
    """灌 job + screening_jobs 行 (status='done' 让 AiScreeningPanel 显示完成态).

    返 job_id.
    """
    job_id = seed_for_competency(qa_db_path, user_id=user_id)
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "SELECT id FROM screening_jobs WHERE job_id=? LIMIT 1", (job_id,),
        )
        if cur.fetchone():
            return job_id
        c.execute(
            "INSERT INTO screening_jobs (user_id, job_id, mode, threshold, "
            "status, total, processed, started_at, finished_at, created_at) "
            "VALUES (?, ?, 'count', 5, 'done', 5, 5, "
            "datetime('now', '-10 minutes'), datetime('now'), "
            "datetime('now'))",
            (user_id, job_id),
        )
        c.commit()
    return job_id


# ===================== Resume / Intake 预置 =====================

def seed_for_intake(qa_db_path, *, user_id: int = 1, n: int = 1) -> list[int]:
    """灌 n 条 IntakeCandidate (collecting 状态), 让 /intake 列表非空.

    返 candidate id 列表.
    """
    _ensure_user(qa_db_path, user_id)
    ids: list[int] = []
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "SELECT id FROM intake_candidates WHERE user_id=? LIMIT ?",
            (user_id, n),
        )
        existing = [r[0] for r in cur.fetchall()]
        ids.extend(existing)
        for i in range(n - len(existing)):
            boss_id = _unique("boss")
            name = f"INT-{i}-{boss_id[-4:]}"
            cur = c.execute(
                "INSERT INTO intake_candidates (user_id, boss_id, name, phone, "
                "email, job_id, intake_status, status, reject_reason, source, "
                "pdf_path, education, bachelor_school, master_school, "
                "phd_school, school_tier, work_years, skills, work_experience, "
                "project_experience, self_evaluation, seniority, "
                "expected_salary_min, expected_salary_max, qr_code_path, "
                "ai_parsed, ai_summary, greet_status, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, '', '', NULL, 'collecting', 'pending', '', "
                "'plugin', '', '本科', '', '', '', '', 0, '', '', '', '', '', "
                "0, 0, '', 'no', '', 'none', "
                "datetime('now'), datetime('now'))",
                (user_id, boss_id, name),
            )
            cid = cur.lastrowid
            ids.append(cid)
            # 给该 candidate 灌一条 IntakeSlot 让 SlotsPanel 有内容
            c.execute(
                "INSERT INTO intake_slots (candidate_id, slot_key, slot_category, "
                "value, ask_count, source, created_at, updated_at) "
                "VALUES (?, 'arrival_date', 'hard', '下周一', 1, 'plugin', "
                "datetime('now'), datetime('now'))",
                (cid,),
            )
        c.commit()
    return ids


def seed_for_resumes(qa_db_path, *, user_id: int = 1, n: int = 2) -> list[int]:
    """灌 n 条 Resume 让 /resumes 列表非空."""
    _ensure_user(qa_db_path, user_id)
    ids: list[int] = []
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "SELECT id FROM resumes WHERE user_id=? LIMIT ?",
            (user_id, n),
        )
        existing = [r[0] for r in cur.fetchall()]
        ids.extend(existing)
        for i in range(n - len(existing)):
            cur = c.execute(
                "INSERT INTO resumes (user_id, name, phone, email, education, "
                "work_years, skills, raw_text, status, ai_parsed, seniority, "
                "boss_id, greet_status, intake_status, "
                "created_at, updated_at) "
                "VALUES (?, ?, '13800000000', 'a@b.com', '本科', 3, "
                "'Python,FastAPI', 'Resume content', 'pending', 'yes', '', "
                "'', 'none', 'collecting', "
                "datetime('now'), datetime('now'))",
                (user_id, f"RES-{i}-{_unique()[-4:]}"),
            )
            ids.append(cur.lastrowid)
        c.commit()
    return ids


# ===================== Interviews 预置 =====================

def seed_for_interviews(qa_db_path, *, user_id: int = 1) -> dict:
    """灌 1 个面试官 + 1 份 Resume + 1 个 scheduled 面试 + 1 个 completed 面试.

    返 {interviewer_id, resume_id, interview_scheduled_id, interview_completed_id}.
    """
    _ensure_user(qa_db_path, user_id)
    out: dict = {}
    with sqlite3.connect(qa_db_path) as c:
        # interviewer
        cur = c.execute(
            "SELECT id FROM interviewers WHERE user_id=? LIMIT 1", (user_id,),
        )
        row = cur.fetchone()
        if row:
            out["interviewer_id"] = row[0]
        else:
            cur = c.execute(
                "INSERT INTO interviewers (name, phone, email, department, "
                "user_id, created_at) VALUES "
                "(?, '13900000000', 'iv@b.com', '研发', ?, datetime('now'))",
                (_unique("IV"), user_id),
            )
            out["interviewer_id"] = cur.lastrowid
        # job (用 competency 那条)
        job_id = seed_for_competency(qa_db_path, user_id=user_id)
        # resume
        resume_ids = seed_for_resumes(qa_db_path, user_id=user_id, n=1)
        out["resume_id"] = resume_ids[0]
        # interview scheduled
        cur = c.execute(
            "SELECT id, status FROM interviews WHERE user_id=?", (user_id,),
        )
        existing = {row[1]: row[0] for row in cur.fetchall()}
        if "scheduled" not in existing:
            cur = c.execute(
                "INSERT INTO interviews (user_id, resume_id, interviewer_id, "
                "job_id, start_time, end_time, meeting_topic, meeting_link, "
                "status, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now', '+1 day'), "
                "datetime('now', '+1 day', '+30 minutes'), '面试-测试', "
                "'https://meeting.tencent.com/test', 'scheduled', '', "
                "datetime('now'), datetime('now'))",
                (user_id, out["resume_id"], out["interviewer_id"], job_id),
            )
            out["interview_scheduled_id"] = cur.lastrowid
        else:
            out["interview_scheduled_id"] = existing["scheduled"]
        if "completed" not in existing:
            cur = c.execute(
                "INSERT INTO interviews (user_id, resume_id, interviewer_id, "
                "job_id, start_time, end_time, meeting_topic, status, notes, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now', '-1 day'), "
                "datetime('now', '-1 day', '+30 minutes'), '面试-完成', "
                "'completed', '已结束', datetime('now'), datetime('now'))",
                (user_id, out["resume_id"], out["interviewer_id"], job_id),
            )
            out["interview_completed_id"] = cur.lastrowid
        else:
            out["interview_completed_id"] = existing["completed"]
        c.commit()
    return out


# ===================== Notification 预置 =====================

def seed_for_notifications(qa_db_path, *, user_id: int = 1, n: int = 25) -> int:
    """灌 n 条 NotificationLog (≥20 触发分页器). 返插入条数."""
    _ensure_user(qa_db_path, user_id)
    inserted = 0
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "SELECT COUNT(*) FROM notification_logs WHERE user_id=?", (user_id,),
        )
        existing = cur.fetchone()[0] or 0
        need = max(0, n - existing)
        statuses = ["sent", "failed", "generated"]
        channels = ["email", "feishu", "template"]
        for i in range(need):
            c.execute(
                "INSERT INTO notification_logs (user_id, recipient_type, "
                "recipient_name, channel, recipient_address, subject, "
                "content, status, created_at) "
                "VALUES (?, 'candidate', ?, ?, 'a@b.com', '面试通知', "
                "'通知正文', ?, datetime('now', ?))",
                (user_id, f"测试人{i}", channels[i % 3],
                 statuses[i % 3], f"-{i} minutes"),
            )
            inserted += 1
        c.commit()
    return inserted


# ===================== Hitl 预置 =====================

def seed_for_hitl_skill_classify(qa_db_path) -> int:
    """灌 1 条 hitl_tasks (entity_type=skill, status=pending) 触发归类弹窗按钮."""
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "SELECT id FROM hitl_tasks WHERE entity_type='skill' "
            "AND status='pending' LIMIT 1",
        )
        row = cur.fetchone()
        if row:
            return row[0]
        # skill_id 不重要, payload 提供 name 即可
        payload = {"name": _unique("UnknownSkill"),
                   "category": "uncategorized",
                   "skill_id": 9999}
        cur = c.execute(
            "INSERT INTO hitl_tasks (f_stage, entity_type, entity_id, payload, "
            "status, created_at) "
            "VALUES ('classify', 'skill', 9999, ?, 'pending', datetime('now'))",
            (json.dumps(payload, ensure_ascii=False),),
        )
        c.commit()
        return cur.lastrowid


# ===================== JobCandidateDecision 预置 (供 Interview 候选人下拉) =====================

def seed_passed_decision_for_job(qa_db_path, *, user_id: int = 1,
                                  job_id: int, candidate_id: int) -> int:
    """灌一条 passed 决策, 让 /interviews 新建弹窗的候选人下拉非空.

    注: candidate_id 必须指向 intake_candidates.id (不是 resumes.id),
    见 decision_model.py FK.
    """
    with sqlite3.connect(qa_db_path) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO job_candidate_decisions (user_id, job_id, "
            "candidate_id, action, decided_at, updated_at) "
            "VALUES (?, ?, ?, 'passed', datetime('now'), datetime('now'))",
            (user_id, job_id, candidate_id),
        )
        c.commit()
        return cur.lastrowid
