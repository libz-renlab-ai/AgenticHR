"""AI 智能筛选 worker.

异步流:
  load → 单批/多批跑 → finalize → 写决策表
取消: 每批之间检查 cancel_requested, 终止子进程。
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.ai_screening.cli_runner import (
    ClaudeProcessHandle,
    CliError,
    run_claude_batch,
)
from app.modules.ai_screening.models import ScreeningJob, ScreeningJobItem
from app.modules.matching.decision_service import set_decision
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 10
FINALIST_BUFFER = 5  # 决赛取 top (threshold + buffer), 但不超 batch_size 避免分批丧失对比
BATCH_TIMEOUT_S = 300


class WorkerCancelled(Exception):
    pass


def _refresh_status(db: Session, sj_id: int) -> ScreeningJob:
    """单独 query 刷新, 避免 stale。"""
    db.expire_all()
    return db.query(ScreeningJob).filter_by(id=sj_id).first()


def _check_cancel(db: Session, sj_id: int) -> bool:
    sj = _refresh_status(db, sj_id)
    return sj is not None and sj.cancel_requested == 1


def _chunk(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _write_batch_results(
    db: Session,
    sj_id: int,
    results: list[dict],
    batch_no: int,
) -> None:
    """把 results 写到对应 items, 按 candidate_id 匹配。"""
    by_cid = {r["candidate_id"]: r for r in results}
    items = (
        db.query(ScreeningJobItem)
        .filter(
            ScreeningJobItem.screening_job_id == sj_id,
            ScreeningJobItem.candidate_id.in_(list(by_cid.keys())),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for it in items:
        r = by_cid[it.candidate_id]
        it.score = r["score"]
        it.reason = r["reason"]
        it.batch_no = batch_no
        it.processed_at = now
    db.commit()


def _mark_batch_error(
    db: Session,
    sj_id: int,
    candidate_ids: list[int],
    error: str,
    batch_no: int,
) -> None:
    items = (
        db.query(ScreeningJobItem)
        .filter(
            ScreeningJobItem.screening_job_id == sj_id,
            ScreeningJobItem.candidate_id.in_(candidate_ids),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for it in items:
        if it.score is None:  # 决赛已写过分的不覆盖
            it.score = 0
        it.error = error[:500]
        it.batch_no = batch_no
        it.processed_at = now
    db.commit()


def _bump_processed(db: Session, sj_id: int, delta: int) -> None:
    sj = db.query(ScreeningJob).filter_by(id=sj_id).first()
    if sj:
        sj.processed = (sj.processed or 0) + delta
        db.commit()


def _finalize(db: Session, sj_id: int) -> None:
    """切线 + 写决策表 + status=done。"""
    sj = db.query(ScreeningJob).filter_by(id=sj_id).first()
    if not sj:
        return
    items = (
        db.query(ScreeningJobItem)
        .filter_by(screening_job_id=sj.id)
        .order_by(ScreeningJobItem.score.desc().nulls_last(), ScreeningJobItem.candidate_id)
        .all()
    )
    if sj.mode == "count":
        pass_n = min(sj.threshold, len(items))
    else:  # ratio
        pass_n = math.ceil(len(items) * sj.threshold / 100)
        pass_n = min(pass_n, len(items))

    pass_ids = []
    for idx, it in enumerate(items):
        if idx < pass_n and it.score is not None and it.error is None:
            it.pass_flag = 1
            pass_ids.append(it.candidate_id)
        else:
            it.pass_flag = 0
    db.commit()

    # 写决策表 (passed only, 失败不写, 让 HR 决定)
    for cid in pass_ids:
        try:
            set_decision(
                db, user_id=sj.user_id, job_id=sj.job_id,
                candidate_id=cid, action="passed",
            )
        except Exception as e:
            logger.warning("set_decision failed for cand=%s: %s", cid, e)

    sj.status = "done"
    sj.finished_at = datetime.now(timezone.utc)
    db.commit()


def _mark_status(
    db: Session, sj_id: int, status: str, error_msg: Optional[str] = None
) -> None:
    sj = db.query(ScreeningJob).filter_by(id=sj_id).first()
    if not sj:
        return
    sj.status = status
    sj.finished_at = datetime.now(timezone.utc)
    if error_msg is not None:
        sj.error_msg = error_msg[:1000]
    db.commit()


async def run_screening(
    sj_id: int,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout_s: int = BATCH_TIMEOUT_S,
    session_factory=SessionLocal,
) -> None:
    """worker 主入口。每批用独立 session。

    跨进程的话需 commit 前刷新; 单进程 asyncio 也需注意 ORM 缓存。
    """
    db = session_factory()
    try:
        sj = db.query(ScreeningJob).filter_by(id=sj_id).first()
        if not sj or sj.status != "running":
            return
        job = db.query(Job).filter_by(id=sj.job_id).first()
        jd_text = (job.jd_text if job else "") or ""

        all_items = (
            db.query(ScreeningJobItem)
            .filter_by(screening_job_id=sj.id)
            .order_by(ScreeningJobItem.candidate_id)
            .all()
        )
        candidates = [
            {"candidate_id": it.candidate_id, "pdf_path": it.pdf_path}
            for it in all_items
        ]

        if not candidates:
            _mark_status(db, sj_id, "failed", "empty pool")
            return

        batches = list(_chunk(candidates, batch_size))
        single_batch = len(batches) == 1

        # ---- Stage 1: 各批独立打分 ----
        for i, batch in enumerate(batches):
            if _check_cancel(db, sj_id):
                _mark_status(db, sj_id, "cancelled")
                return

            handle = ClaudeProcessHandle()
            try:
                results = await run_claude_batch(
                    jd_text, batch, timeout=timeout_s, handle=handle,
                )
                _write_batch_results(db, sj_id, results, batch_no=i + 1)
            except CliError as e:
                logger.warning("batch %d failed: %s", i + 1, e)
                _mark_batch_error(
                    db, sj_id,
                    [c["candidate_id"] for c in batch],
                    str(e), batch_no=i + 1,
                )
            except Exception as e:
                logger.exception("batch %d unexpected error", i + 1)
                _mark_batch_error(
                    db, sj_id,
                    [c["candidate_id"] for c in batch],
                    f"unexpected: {e}", batch_no=i + 1,
                )

            _bump_processed(db, sj_id, len(batch))

        # ---- Stage 2: 多批走决赛 (单批跑完保横向对比) ----
        if not single_batch:
            if _check_cancel(db, sj_id):
                _mark_status(db, sj_id, "cancelled")
                return

            sj = db.query(ScreeningJob).filter_by(id=sj_id).first()
            # 决赛上限 = batch_size, 保证一批跑完保横向对比公平
            finalist_n = min(sj.threshold + FINALIST_BUFFER, batch_size)
            if finalist_n <= 1:
                # threshold 极小不需决赛
                pass
            else:
                scored_items = (
                    db.query(ScreeningJobItem)
                    .filter_by(screening_job_id=sj.id)
                    .filter(ScreeningJobItem.score.isnot(None))
                    .filter(ScreeningJobItem.error.is_(None))
                    .order_by(ScreeningJobItem.score.desc(), ScreeningJobItem.candidate_id)
                    .limit(finalist_n)
                    .all()
                )
                finalists = [
                    {"candidate_id": it.candidate_id, "pdf_path": it.pdf_path}
                    for it in scored_items
                ]

                if finalists:
                    handle = ClaudeProcessHandle()
                    try:
                        results = await run_claude_batch(
                            jd_text, finalists, timeout=timeout_s, handle=handle,
                        )
                        _write_batch_results(db, sj_id, results, batch_no=100)
                    except CliError as e:
                        logger.warning("finalist batch failed: %s", e)
                        # 决赛失败 → 保留初评分数, 不再覆盖

        if _check_cancel(db, sj_id):
            _mark_status(db, sj_id, "cancelled")
            return

        _finalize(db, sj_id)
    except Exception as e:
        logger.exception("worker run_screening crashed")
        _mark_status(db, sj_id, "failed", f"worker crash: {e}")
    finally:
        db.close()


def spawn(sj_id: int) -> asyncio.Task:
    """fire-and-forget 创建 task。返回 task 句柄。"""
    return asyncio.create_task(run_screening(sj_id))
