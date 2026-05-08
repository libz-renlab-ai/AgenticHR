"""F-interview-eval: interview_eval_jobs + interview_eval_scorecards.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-07

新建 2 张表，对既有 schema 无任何修改；downgrade 干净 drop。

BUG-IE-012 修复：legacy 库可能存在「表已存在但列不全」的情况（人工 patch 过）。
此前仅以 get_table_names() 判断 → 跳过 create_table → alembic_version 升到 0027 →
运行时崩溃 (no such column cancel_requested)。现在改为：
- 表不存在：照常 create_table
- 表已存在：对比 canonical 列清单，best-effort 补齐缺失的普通列
  注意 SQLite 限制：通过 add_column 后加 FK / CHECK 约束并不可靠，因此补齐时
  只创建无 FK / CHECK 的同名列；这是 best-effort 恢复，不是完整 schema 校验。
  legacy 库的数据完整性由人工保证。
"""
from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


# ---- canonical 列定义工厂 -------------------------------------------------
# 用 lambda 是因为 SQLAlchemy Column 一旦 attach 到 Table 就不能复用；
# 同一进程里 create_table + add_column 两条路径都需要全新实例。
# 用于 add_column 的工厂故意不带 FK / CHECK（SQLite 后加不可靠）。

def _job_columns_for_create():
    """完整列定义（含 FK / CHECK），仅用于全新建表。"""
    return [
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
    ]


# legacy 补齐用：每列一个 lambda，故意省略 FK / CHECK（SQLite 后加不可靠）。
# retention_until 没有 server_default（原始 schema 也没有），但 legacy 表
# 里若已有数据，add_column NOT NULL 会失败 → 因此放宽为 nullable=True 仅在补齐时。
_JOB_COLUMN_FACTORIES = {
    "id": lambda: sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    "interview_id": lambda: sa.Column("interview_id", sa.Integer, nullable=False),
    "user_id": lambda: sa.Column("user_id", sa.Integer, nullable=False, server_default="0"),
    "status": lambda: sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    "recording_path": lambda: sa.Column("recording_path", sa.String(500), nullable=False, server_default=""),
    "recording_size": lambda: sa.Column("recording_size", sa.Integer, nullable=False, server_default="0"),
    "duration_sec": lambda: sa.Column("duration_sec", sa.Integer, nullable=False, server_default="0"),
    "meeting_account": lambda: sa.Column("meeting_account", sa.String(50), nullable=False, server_default=""),
    "asr_request_id": lambda: sa.Column("asr_request_id", sa.String(100), nullable=False, server_default=""),
    "llm_model": lambda: sa.Column("llm_model", sa.String(100), nullable=False, server_default=""),
    "prompt_version": lambda: sa.Column("prompt_version", sa.String(50), nullable=False, server_default=""),
    "error_msg": lambda: sa.Column("error_msg", sa.Text, nullable=False, server_default=""),
    "cancel_requested": lambda: sa.Column("cancel_requested", sa.Integer, nullable=False, server_default="0"),
    # legacy 补齐：放宽到 nullable=True，避免在已有行场景下 NOT NULL 报错
    "retention_until": lambda: sa.Column("retention_until", sa.DateTime, nullable=True),
    "deleted_at": lambda: sa.Column("deleted_at", sa.DateTime, nullable=True),
    "created_at": lambda: sa.Column(
        "created_at", sa.DateTime, nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ),
    "updated_at": lambda: sa.Column(
        "updated_at", sa.DateTime, nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ),
}


def _scorecard_columns_for_create():
    return [
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
    ]


_SCORECARD_COLUMN_FACTORIES = {
    "id": lambda: sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    "job_id": lambda: sa.Column("job_id", sa.Integer, nullable=False),
    "interview_id": lambda: sa.Column("interview_id", sa.Integer, nullable=False, server_default="0"),
    "transcript_path": lambda: sa.Column("transcript_path", sa.String(500), nullable=False, server_default=""),
    "dimensions_json": lambda: sa.Column("dimensions_json", sa.JSON, nullable=True),
    "hire_recommendation": lambda: sa.Column("hire_recommendation", sa.String(20), nullable=False, server_default="hold"),
    "strengths": lambda: sa.Column("strengths", sa.JSON, nullable=True),
    "risks": lambda: sa.Column("risks", sa.JSON, nullable=True),
    "followups": lambda: sa.Column("followups", sa.JSON, nullable=True),
    "llm_model": lambda: sa.Column("llm_model", sa.String(100), nullable=False, server_default=""),
    "prompt_version": lambda: sa.Column("prompt_version", sa.String(50), nullable=False, server_default=""),
    "created_at": lambda: sa.Column(
        "created_at", sa.DateTime, nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ),
}


def _ensure_index(insp, table_name: str, index_name: str, columns: list[str]) -> None:
    """缺失则创建索引（idempotent）。"""
    existing = {ix["name"] for ix in insp.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- interview_eval_jobs ---------------------------------------------
    if "interview_eval_jobs" not in insp.get_table_names():
        op.create_table("interview_eval_jobs", *_job_columns_for_create())
    else:
        # legacy 补齐：缺哪列加哪列（无 FK / CHECK，best-effort）
        existing_cols = {c["name"] for c in insp.get_columns("interview_eval_jobs")}
        for name, factory in _JOB_COLUMN_FACTORIES.items():
            if name not in existing_cols:
                op.add_column("interview_eval_jobs", factory())

    # 索引（无论是新建表还是 legacy 补齐都要 ensure）
    insp = sa.inspect(bind)  # refresh after possible alters
    _ensure_index(insp, "interview_eval_jobs", "ix_ieval_jobs_interview", ["interview_id"])
    _ensure_index(insp, "interview_eval_jobs", "ix_ieval_jobs_status", ["status"])
    _ensure_index(insp, "interview_eval_jobs", "ix_ieval_jobs_retention", ["retention_until"])
    _ensure_index(insp, "interview_eval_jobs", "ix_ieval_jobs_user_id", ["user_id"])

    # ---- interview_eval_scorecards ---------------------------------------
    if "interview_eval_scorecards" not in insp.get_table_names():
        op.create_table("interview_eval_scorecards", *_scorecard_columns_for_create())
    else:
        existing_cols = {c["name"] for c in insp.get_columns("interview_eval_scorecards")}
        for name, factory in _SCORECARD_COLUMN_FACTORIES.items():
            if name not in existing_cols:
                op.add_column("interview_eval_scorecards", factory())

    insp = sa.inspect(bind)
    _ensure_index(insp, "interview_eval_scorecards", "ix_ieval_sc_job", ["job_id"])
    _ensure_index(insp, "interview_eval_scorecards", "ix_ieval_sc_interview", ["interview_id"])


def downgrade() -> None:
    op.drop_index("ix_ieval_sc_interview", table_name="interview_eval_scorecards")
    op.drop_index("ix_ieval_sc_job", table_name="interview_eval_scorecards")
    op.drop_table("interview_eval_scorecards")
    op.drop_index("ix_ieval_jobs_user_id", table_name="interview_eval_jobs")
    op.drop_index("ix_ieval_jobs_retention", table_name="interview_eval_jobs")
    op.drop_index("ix_ieval_jobs_status", table_name="interview_eval_jobs")
    op.drop_index("ix_ieval_jobs_interview", table_name="interview_eval_jobs")
    op.drop_table("interview_eval_jobs")
