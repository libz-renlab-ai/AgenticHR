"""ai_smart screening: screening_jobs + screening_job_items.

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-06

支持 AI 智能筛选 (claude --print 子进程横向打分):
- screening_jobs: 任务级状态 (running/done/failed/cancelled)
- screening_job_items: 单候选 score/reason/pass

回滚: drop table 即可, 决策表 (job_candidate_decisions) 不动。
"""
from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "screening_jobs" not in insp.get_table_names():
        op.create_table(
            "screening_jobs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "job_id",
                sa.Integer,
                sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("mode", sa.String(10), nullable=False),
            sa.Column("threshold", sa.Integer, nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("total", sa.Integer, nullable=False, server_default="0"),
            sa.Column("processed", sa.Integer, nullable=False, server_default="0"),
            sa.Column("cancel_requested", sa.Integer, nullable=False, server_default="0"),
            sa.Column("error_msg", sa.Text, nullable=True),
            sa.Column("started_at", sa.DateTime, nullable=True),
            sa.Column("finished_at", sa.DateTime, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.CheckConstraint(
                "mode IN ('count','ratio')", name="ck_sj_mode_enum"
            ),
            sa.CheckConstraint(
                "status IN ('pending','running','done','failed','cancelled')",
                name="ck_sj_status_enum",
            ),
        )
        op.create_index("ix_sj_user_job", "screening_jobs", ["user_id", "job_id", "status"])
        op.create_index("ix_sj_status", "screening_jobs", ["status"])

    if "screening_job_items" not in insp.get_table_names():
        op.create_table(
            "screening_job_items",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "screening_job_id",
                sa.Integer,
                sa.ForeignKey("screening_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "candidate_id",
                sa.Integer,
                sa.ForeignKey("intake_candidates.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("pdf_path", sa.String(500), nullable=False, server_default=""),
            sa.Column("score", sa.Integer, nullable=True),
            sa.Column("reason", sa.Text, nullable=True),
            sa.Column("pass_flag", sa.Integer, nullable=False, server_default="0"),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("batch_no", sa.Integer, nullable=False, server_default="0"),
            sa.Column("processed_at", sa.DateTime, nullable=True),
        )
        op.create_index(
            "ix_sji_job_score", "screening_job_items",
            ["screening_job_id", "score"],
        )
        op.create_index(
            "ix_sji_job_candidate", "screening_job_items",
            ["screening_job_id", "candidate_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_sji_job_candidate", table_name="screening_job_items")
    op.drop_index("ix_sji_job_score", table_name="screening_job_items")
    op.drop_table("screening_job_items")
    op.drop_index("ix_sj_status", table_name="screening_jobs")
    op.drop_index("ix_sj_user_job", table_name="screening_jobs")
    op.drop_table("screening_jobs")
