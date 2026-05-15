"""spec-2026-05-15-job-binding: 从 MatchingResult 反推回填历史 NULL job_id.

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-15

背景:
    F3 一键打招呼路径之前没把 job_id 写进 IntakeCandidate / Resume(详见
    docs/spec-2026-05-15-job-binding.md)。结果是: 候选人 job_id 字段长期为
    NULL,系统答不出"我给岗位 A 打过招呼的人有哪些"。本次迁移把已 promote
    且有 MatchingResult 的历史候选人回填到反推的 job_id。

算法 (per spec):
    for cand in IntakeCandidate where job_id IS NULL and promoted_resume_id IS NOT NULL:
        matches = MatchingResult where resume_id = cand.promoted_resume_id
        if not matches: skip
        passed = [m for m in matches if hard_gate_passed == 1]
        winner = max(passed or matches, key=total_score)
        cand.job_id = winner.job_id
        resume.job_id = winner.job_id  (仅当 resume.job_id IS NULL,不覆盖)

不可逆 (回填型),downgrade no-op。
"""
from alembic import op
import sqlalchemy as sa


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    candidates = bind.execute(sa.text(
        "SELECT id, promoted_resume_id FROM intake_candidates "
        "WHERE job_id IS NULL AND promoted_resume_id IS NOT NULL"
    )).fetchall()

    backfilled = 0
    for cand_id, resume_id in candidates:
        if not resume_id:
            continue
        matches = bind.execute(sa.text(
            "SELECT job_id, hard_gate_passed, total_score "
            "FROM matching_results WHERE resume_id = :rid"
        ), {"rid": resume_id}).fetchall()
        if not matches:
            continue
        # 优先选 hard_gate_passed=1 的;同组里选 total_score 最高的。
        # passed 池为空(都没过硬筛)时退回所有 match 里选 score 最高。
        passed = [m for m in matches if (m[1] or 0) == 1]
        pool = passed if passed else list(matches)
        winner = max(pool, key=lambda m: m[2] or 0)
        chosen_job_id = winner[0]
        if chosen_job_id is None:
            continue
        # 双写,均使用 IS NULL 过滤防止并发冲突或重复运行覆盖已有值
        bind.execute(sa.text(
            "UPDATE intake_candidates SET job_id = :j "
            "WHERE id = :c AND job_id IS NULL"
        ), {"j": chosen_job_id, "c": cand_id})
        bind.execute(sa.text(
            "UPDATE resumes SET job_id = :j "
            "WHERE id = :r AND job_id IS NULL"
        ), {"j": chosen_job_id, "r": resume_id})
        backfilled += 1

    # 留一条审计 trail 便于事后查
    if backfilled:
        bind.execute(sa.text(
            "INSERT INTO audit_events "
            "(event_id, f_stage, action, entity_type, entity_id, created_at) "
            "VALUES (:eid, 'f4_backfill', 'migration_0029', "
            "        'intake_candidate', :count, CURRENT_TIMESTAMP)"
        ), {"eid": f"migration_0029_{backfilled}", "count": backfilled})


def downgrade() -> None:
    # 回填型迁移; 无逆操作 (回填的数据不应被反向清空,
    # 否则会破坏 0029 之后写入路径正常产生的 job_id)。
    pass
