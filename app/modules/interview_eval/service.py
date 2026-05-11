"""F-interview-eval 任务编排：create_job / 查询 / cancel."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from app.config import settings
from app.database import SessionLocal
from app.modules.interview_eval.models import InterviewEvalJob, InterviewEvalScorecard
from app.modules.scheduling.models import Interview
from app.modules.screening.models import Job


@dataclass
class ServiceError(Exception):
    code: int
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def _account_pool() -> list[str]:
    return [s.strip() for s in settings.tencent_meeting_accounts.split(",") if s.strip()]


def _spawn_worker(job_id: int) -> None:
    """Spin up worker thread。在测试里被 monkey patch 掉。"""
    from app.modules.interview_eval.worker import run as worker_run
    threading.Thread(target=worker_run, args=(job_id,), daemon=True).start()


def create_job(*, interview_id: int, user_id: int) -> int:
    """5 道校验门后插一行 pending 任务并 spawn worker。返回 job_id。"""
    if not settings.interview_eval_enabled:
        raise ServiceError(503, "AI 面评功能未启用，请联系管理员配置")

    db = SessionLocal()
    try:
        # 校验 1：interview 存在 + 多用户隔离（跨用户按 not found 防 enumerate）
        interview = (
            db.query(Interview)
            .filter(Interview.id == interview_id, Interview.user_id == user_id)
            .first()
        )
        if interview is None:
            raise ServiceError(404, f"面试 {interview_id} 不存在")

        # 校验 2：competency_model approved
        if interview.job_id is None:
            raise ServiceError(400, "本次面试未关联岗位")
        job_row = db.query(Job).filter(Job.id == interview.job_id).first()
        if job_row is None or job_row.competency_model_status != "approved":
            raise ServiceError(
                400, "请先在 Jobs 页完成能力模型抽取并审核通过（F1）",
            )

        # 校验 3：meeting_id + meeting_account（IE-011: strip 后判空）
        if not (interview.meeting_id or "").strip():
            raise ServiceError(400, "本次面试无腾讯会议记录")
        if interview.meeting_account not in _account_pool():
            raise ServiceError(
                400,
                f"腾讯会议账号 '{interview.meeting_account}' 不在账号池，请检查 .env"
                " TENCENT_MEETING_ACCOUNTS",
            )

        # 校验 4：无进行中任务
        active = (
            db.query(InterviewEvalJob)
            .filter(
                InterviewEvalJob.interview_id == interview_id,
                InterviewEvalJob.status.in_(
                    ["pending", "downloading", "transcribing", "scoring"]
                ),
            )
            .first()
        )
        if active is not None:
            raise ServiceError(409, f"已有进行中的 AI 面评任务 (job_id={active.id})")

        # 创建任务
        retention_until = datetime.now(timezone.utc) + timedelta(
            days=settings.interview_eval_recording_retention_days
        )
        # IE-017: pending 行显式打心跳，避免 reconcile 周期 cron 抢杀
        # (last_heartbeat=NULL 会被视为陈旧立刻 failed，留下 error_msg="服务中断"残留)
        new_job = InterviewEvalJob(
            interview_id=interview_id, user_id=user_id, status="pending",
            meeting_account=interview.meeting_account, retention_until=retention_until,
            last_heartbeat=datetime.now(timezone.utc),
        )
        db.add(new_job); db.commit(); db.refresh(new_job)

        # IE-001 并发防御：commit 后再查 active job > 1 → 回滚
        # （5 道校验门 + INSERT 之间无 db 锁，并发可绕过校验 4）
        active_count = (
            db.query(InterviewEvalJob)
            .filter(
                InterviewEvalJob.interview_id == interview_id,
                InterviewEvalJob.status.in_(
                    ["pending", "downloading", "transcribing", "scoring"]
                ),
            )
            .count()
        )
        if active_count > 1:
            db.delete(new_job); db.commit()
            raise ServiceError(409, "已有进行中的 AI 面评任务（并发竞争）")

        # IE-005 spawn 失败兜底：避免 pending job 永远卡死
        try:
            _spawn_worker(new_job.id)
        except Exception as e:
            db.query(InterviewEvalJob).filter_by(id=new_job.id).update(
                {"status": "failed", "error_msg": f"[spawn] {e}"}
            )
            db.commit()
            raise ServiceError(500, f"启动后台任务失败：{e}")
        return new_job.id
    finally:
        db.close()


def get_job(*, job_id: int, user_id: int) -> InterviewEvalJob:
    db = SessionLocal()
    try:
        job = (
            db.query(InterviewEvalJob)
            .filter(InterviewEvalJob.id == job_id, InterviewEvalJob.user_id == user_id)
            .first()
        )
        if job is None:
            raise ServiceError(404, f"任务 {job_id} 不存在")
        db.expunge(job)
        return job
    finally:
        db.close()


def cancel_job(*, job_id: int, user_id: int) -> None:
    db = SessionLocal()
    try:
        job = (
            db.query(InterviewEvalJob)
            .filter(InterviewEvalJob.id == job_id, InterviewEvalJob.user_id == user_id)
            .first()
        )
        if job is None:
            raise ServiceError(404, "任务不存在")
        if job.status not in ("pending", "downloading", "transcribing", "scoring"):
            raise ServiceError(409, f"任务已 {job.status}，不可取消")
        job.cancel_requested = 1
        db.commit()
    finally:
        db.close()


def latest_job_for_interview(*, interview_id: int, user_id: int) -> InterviewEvalJob | None:
    db = SessionLocal()
    try:
        job = (
            db.query(InterviewEvalJob)
            .filter(
                InterviewEvalJob.interview_id == interview_id,
                InterviewEvalJob.user_id == user_id,
            )
            .order_by(InterviewEvalJob.created_at.desc())
            .first()
        )
        if job is not None:
            db.expunge(job)
        return job
    finally:
        db.close()


def scorecards_for_resume(*, resume_id: int, user_id: int) -> list[dict]:
    """聚合候选人多场面试 scorecard（候选人详情页用）."""
    db = SessionLocal()
    try:
        rows = (
            db.query(InterviewEvalScorecard, Interview)
            .join(Interview, Interview.id == InterviewEvalScorecard.interview_id)
            .filter(Interview.resume_id == resume_id, Interview.user_id == user_id)
            .order_by(InterviewEvalScorecard.created_at.desc())
            .all()
        )
        result = []
        for sc, iv in rows:
            avg_score = (
                sum(d["score"] for d in sc.dimensions_json) / len(sc.dimensions_json)
                if sc.dimensions_json else 0
            )
            result.append({
                "scorecard_id": sc.id,
                "interview_id": iv.id,
                "job_id": iv.job_id,
                "interview_date": iv.start_time.isoformat() if iv.start_time else "",
                "hire_recommendation": sc.hire_recommendation,
                "avg_score": round(avg_score, 1),
                "created_at": sc.created_at.isoformat(),
            })
        return result
    finally:
        db.close()
