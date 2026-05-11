"""F-interview-eval: 加 last_heartbeat 列 + 索引.

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-11

支持 worker 心跳 + reconcile 模块识别僵尸任务（服务重启后自愈）。
nullable=True，历史行默认 NULL，reconcile 首次扫描时统一视为陈旧。
"""
from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("interview_eval_jobs")}
    if "last_heartbeat" not in cols:
        op.add_column(
            "interview_eval_jobs",
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        )
    indexes = {i["name"] for i in insp.get_indexes("interview_eval_jobs")}
    if "ix_ieval_jobs_heartbeat" not in indexes:
        op.create_index(
            "ix_ieval_jobs_heartbeat", "interview_eval_jobs", ["last_heartbeat"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    indexes = {i["name"] for i in insp.get_indexes("interview_eval_jobs")}
    if "ix_ieval_jobs_heartbeat" in indexes:
        op.drop_index("ix_ieval_jobs_heartbeat", table_name="interview_eval_jobs")
    cols = {c["name"] for c in insp.get_columns("interview_eval_jobs")}
    if "last_heartbeat" in cols:
        with op.batch_alter_table("interview_eval_jobs") as batch:
            batch.drop_column("last_heartbeat")
