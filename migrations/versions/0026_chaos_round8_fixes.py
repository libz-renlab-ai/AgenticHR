"""chaos_round8 系列修: ScreeningJob 并发约束 + cli_path 锁定 +
0023 1:N dedup + 0024 跨用户清理 + 0022 reject_reason 补全

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-06

修复 BUG-088 (并发 start 双 running) / BUG-102 (cli binary TOCTOU) /
BUG-107 (跨用户决策脏数据) / BUG-108 (1:N candidate→resume) /
BUG-115 (abandoned/timed_out 候选 reject_reason 补全)。

回滚:
- 删 partial unique index uniq_sj_user_job_running
- 删 cli_path 列 (SQLite drop column 用 batch_alter_table)
- 数据修复不可逆 (但仅是清理脏数据, 不动正常数据)
"""
from alembic import op
import sqlalchemy as sa


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- BUG-102: ScreeningJob.cli_path 列 ----
    sj_cols = {c["name"] for c in insp.get_columns("screening_jobs")}
    if "cli_path" not in sj_cols:
        op.add_column(
            "screening_jobs",
            sa.Column("cli_path", sa.String(500), nullable=True),
        )

    # ---- BUG-088: partial UNIQUE on (user_id, job_id) WHERE status='running' ----
    # SQLite partial index via raw SQL
    sj_idx = {ix["name"] for ix in insp.get_indexes("screening_jobs")}
    if "uniq_sj_user_job_running" not in sj_idx:
        # 先尝试清理可能存在的并发遗留 (只保留 id 最大的那条 running)
        bind.execute(sa.text("""
            UPDATE screening_jobs
            SET status='failed', error_msg='dedup by 0026 migration',
                finished_at=COALESCE(finished_at, CURRENT_TIMESTAMP)
            WHERE status='running'
              AND id NOT IN (
                  SELECT MAX(id) FROM screening_jobs
                  WHERE status='running'
                  GROUP BY user_id, job_id
              )
        """))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX uniq_sj_user_job_running "
            "ON screening_jobs(user_id, job_id) WHERE status='running'"
        ))

    # ---- BUG-108: intake_candidates 1:N → 1:1 dedup (0023 漏的兜底) ----
    # 多个 candidate 共享同一 promoted_resume_id 时, 只保留 id 最小的反向键,
    # 其余 SET promoted_resume_id=NULL 让 partial unique 不冲突。
    bind.execute(sa.text("""
        UPDATE intake_candidates
        SET promoted_resume_id = NULL
        WHERE promoted_resume_id IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM intake_candidates
              WHERE promoted_resume_id IS NOT NULL
              GROUP BY promoted_resume_id
          )
    """))

    # ---- BUG-107: 决策表跨用户脏数据清理 ----
    # 0024 回填取 r.user_id 但 mr.job_id 的 owner 可能不一致, 导致 decision.user_id
    # 与 job 的 user_id 不一致。统一改为 job.user_id 真值。
    bind.execute(sa.text("""
        UPDATE job_candidate_decisions
        SET user_id = (
            SELECT j.user_id FROM jobs j WHERE j.id = job_candidate_decisions.job_id
        )
        WHERE EXISTS (
            SELECT 1 FROM jobs j
            WHERE j.id = job_candidate_decisions.job_id
              AND j.user_id != job_candidate_decisions.user_id
        )
    """))

    # ---- BUG-115: abandoned / timed_out 候选 reject_reason 补默认描述 ----
    bind.execute(sa.text("""
        UPDATE intake_candidates
        SET reject_reason = CASE intake_status
            WHEN 'abandoned' THEN '采集放弃 (硬性问题多次未答)'
            WHEN 'timed_out' THEN 'PDF 等待超时'
            ELSE reject_reason
        END
        WHERE intake_status IN ('abandoned','timed_out')
          AND (reject_reason IS NULL OR reject_reason = '')
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP INDEX IF EXISTS uniq_sj_user_job_running"))
    insp = sa.inspect(bind)
    sj_cols = {c["name"] for c in insp.get_columns("screening_jobs")}
    if "cli_path" in sj_cols:
        # SQLite drop column 走 batch_alter_table
        with op.batch_alter_table("screening_jobs") as batch:
            batch.drop_column("cli_path")
