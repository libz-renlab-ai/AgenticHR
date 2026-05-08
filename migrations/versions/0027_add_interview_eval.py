"""F-interview-eval: interview_eval_jobs + interview_eval_scorecards.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-07

新建 2 张表，对既有 schema 无任何修改；downgrade 干净 drop。
"""
from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "interview_eval_jobs" not in insp.get_table_names():
        op.create_table(
            "interview_eval_jobs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "interview_id", sa.Integer,
                sa.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("recording_path", sa.String(500), nullable=False, server_default=""),
            sa.Column("recording_size", sa.Integer, nullable=False, server_default="0"),
            sa.Column("duration_sec", sa.Integer, nullable=False, server_default="0"),
            sa.Column("meeting_account", sa.String(50), nullable=False, server_default=""),
            sa.Column("asr_request_id", sa.String(100), nullable=False, server_default=""),
            sa.Column("llm_model", sa.String(100), nullable=False, server_default=""),
            sa.Column("prompt_version", sa.String(50), nullable=False, server_default=""),
            sa.Column("error_msg", sa.Text, nullable=False, server_default=""),
            sa.Column("cancel_requested", sa.Integer, nullable=False, server_default="0"),
            sa.Column("retention_until", sa.DateTime, nullable=False),
            sa.Column("deleted_at", sa.DateTime, nullable=True),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "status IN ('pending','downloading','transcribing','scoring',"
                "'done','failed','cancelled')",
                name="ck_ieval_job_status",
            ),
        )
        op.create_index("ix_ieval_jobs_interview", "interview_eval_jobs", ["interview_id"])
        op.create_index("ix_ieval_jobs_status", "interview_eval_jobs", ["status"])
        op.create_index("ix_ieval_jobs_retention", "interview_eval_jobs", ["retention_until"])
        op.create_index("ix_ieval_jobs_user_id", "interview_eval_jobs", ["user_id"])

    if "interview_eval_scorecards" not in insp.get_table_names():
        op.create_table(
            "interview_eval_scorecards",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "job_id", sa.Integer,
                sa.ForeignKey("interview_eval_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("interview_id", sa.Integer, nullable=False),
            sa.Column("transcript_path", sa.String(500), nullable=False),
            sa.Column("dimensions_json", sa.JSON, nullable=False),
            sa.Column("hire_recommendation", sa.String(20), nullable=False),
            sa.Column("strengths", sa.JSON, nullable=False),
            sa.Column("risks", sa.JSON, nullable=False),
            sa.Column("followups", sa.JSON, nullable=False),
            sa.Column("llm_model", sa.String(100), nullable=False),
            sa.Column("prompt_version", sa.String(50), nullable=False),
            sa.Column(
                "created_at", sa.DateTime, nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "hire_recommendation IN ('strong_hire','hire','hold','no_hire')",
                name="ck_ieval_sc_recommendation",
            ),
        )
        op.create_index("ix_ieval_sc_job", "interview_eval_scorecards", ["job_id"])
        op.create_index("ix_ieval_sc_interview", "interview_eval_scorecards", ["interview_id"])


def downgrade() -> None:
    op.drop_index("ix_ieval_sc_interview", table_name="interview_eval_scorecards")
    op.drop_index("ix_ieval_sc_job", table_name="interview_eval_scorecards")
    op.drop_table("interview_eval_scorecards")
    op.drop_index("ix_ieval_jobs_user_id", table_name="interview_eval_jobs")
    op.drop_index("ix_ieval_jobs_retention", table_name="interview_eval_jobs")
    op.drop_index("ix_ieval_jobs_status", table_name="interview_eval_jobs")
    op.drop_index("ix_ieval_jobs_interview", table_name="interview_eval_jobs")
    op.drop_table("interview_eval_jobs")
