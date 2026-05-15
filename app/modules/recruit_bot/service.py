"""F3 RecruitBot 核心服务 — 候选人 upsert / 决策 / 打招呼记录 / 配额."""
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.resume.models import Resume
from app.modules.im_intake.candidate_model import IntakeCandidate

if TYPE_CHECKING:
    from app.modules.recruit_bot.schemas import ScrapedCandidate


def _ensure_candidate_for_f3(
    db: Session, *, user_id: int, boss_id: str, name: str,
    education: str = "", work_years: int = 0, intended_job: str = "",
    skills_csv: str = "", latest_work_brief: str = "", raw_text: str = "",
    job_id: int | None = None,
) -> IntakeCandidate:
    """spec 0429 阶段 B: F3 路径 ensure IntakeCandidate（写 Resume 前的镜像）。

    用 (user_id, boss_id) 唯一索引 dedup；非空字段才覆盖既有值，不清掉已抽到的信息。

    spec 2026-05-15: 新建分支写入 ``job_id``;upsert 分支 NULL → 实值时回填(不算
    "覆盖",因为 NULL 不是 first-write);非 NULL 且与传入的不一致时,**保持现值**
    (first-write wins) 并落一条 ``f3_job_rebind_attempt`` 审计行,事后可查
    cross-job greet 事实。这样 HR 同时招多个岗位时,候选人有稳定的 primary 岗位
    归属,不会因为二次招呼而漂移。
    """
    c = (db.query(IntakeCandidate)
         .filter_by(user_id=user_id, boss_id=boss_id).first())
    now = datetime.now(timezone.utc)
    if c is None:
        c = IntakeCandidate(
            user_id=user_id, boss_id=boss_id, name=name or "",
            education=education or "",
            work_years=work_years or 0,
            job_intention=intended_job or "",
            job_id=job_id,
            skills=skills_csv or "",
            work_experience=latest_work_brief or "",
            raw_text=raw_text or "",
            source="f3_recruit_bot",
            intake_status="collecting",
            intake_started_at=now,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
    else:
        # upsert 语义：scraper 给值时才覆盖，空值视为未观察
        if name and not c.name:
            c.name = name
        if education and not c.education:
            c.education = education
        if work_years and not c.work_years:
            c.work_years = work_years
        if intended_job and not c.job_intention:
            c.job_intention = intended_job
        if skills_csv and not c.skills:
            c.skills = skills_csv
        if latest_work_brief and not c.work_experience:
            c.work_experience = latest_work_brief
        if raw_text:
            c.raw_text = raw_text
        # spec 2026-05-15: job_id 回填 / cross-job 审计
        if job_id:
            if c.job_id is None:
                # NULL → 实值: 回填,first-write 的"第一次有效写"
                c.job_id = job_id
            elif c.job_id != job_id:
                # 已有 primary 归属,二次 greet 落到不同岗位 → 不动,审计
                try:
                    log_event(
                        f_stage="f3_job_rebind_attempt",
                        action="cross_job_greet",
                        entity_type="intake_candidate",
                        entity_id=c.id,
                        input_payload={
                            "boss_id": c.boss_id,
                            "primary_job_id": c.job_id,
                            "attempted_job_id": job_id,
                        },
                        reviewer_id=user_id,
                    )
                except Exception:
                    # 审计失败不应阻断主流程
                    pass
        db.commit()
    return c


def _safe_csv(tags: list[str]) -> str:
    """Join tags into a CSV; strip embedded commas from each tag so downstream
    ``str.split(',')`` consumers don't get split mid-tag (e.g. a tag
    ``"C++, Java"`` would otherwise corrupt the skills column)."""
    return ",".join(t.replace(",", " ") for t in tags)


def _summarize_raw_text(c: "ScrapedCandidate") -> str:
    """拼接所有 scraped 字段为调试 summary."""
    parts = [
        f"姓名:{c.name}",
        f"boss_id:{c.boss_id}",
        f"年龄:{c.age or ''}",
        f"学历:{c.education}",
        f"毕业年:{c.grad_year or ''}",
        f"工作年:{c.work_years}",
        f"学校:{c.school}",
        f"专业:{c.major}",
        f"意向:{c.intended_job}",
        f"技能:{_safe_csv(c.skill_tags)}",
        f"院校tag:{_safe_csv(c.school_tier_tags)}",
        f"排名tag:{_safe_csv(c.ranking_tags)}",
        f"期望薪资:{c.expected_salary}",
        f"活跃:{c.active_status}",
        f"推荐理由:{c.recommendation_reason}",
        f"最近工作:{c.latest_work_brief}",
    ]
    return " | ".join(parts)


def upsert_resume_by_boss_id(
    db: Session, user_id: int, candidate: "ScrapedCandidate",
    job_id: int | None = None,
) -> Resume:
    """按 (user_id, boss_id) 查找或新建 Resume 行.

    已存在时更新非状态字段（保留 status / greet_status / greeted_at / ai_* 不动）.

    **Empty-string semantic:** ``candidate.education == ""``, ``work_years == 0`` 等
    在 update 分支视为 "scraper 本次未观察到该字段, 保留既有值". 这是 Boss 页面
    DOM 抓取的自然模式 (缺字段 → 空默认), 避免页面偶发渲染失败把已有值清空.
    如需主动清字段, 不得走 upsert — 必须 DELETE+INSERT 或单独的 dedicated
    endpoint. 在 ``ScrapedCandidate`` schema 迁移为 None-sentinel 之前, 保留此
    语义; 任何语义变更应当作独立任务, 不能在 T2 范围内改.

    spec 2026-05-15: ``job_id`` 一路透传给 IntakeCandidate + Resume,确定性绑定
    候选人到 HR 当前操作的岗位。NULL → 实值 时回填(first-write 的第一次有效写),
    非 NULL 不覆盖(由 ``_ensure_candidate_for_f3`` 落 cross-job 审计)。
    """
    existing = (
        db.query(Resume)
        .filter(Resume.user_id == user_id, Resume.boss_id == candidate.boss_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    skills_csv = _safe_csv(candidate.skill_tags)
    summary = _summarize_raw_text(candidate)
    raw_text = (
        f"{summary} || 原文:{candidate.raw_text}" if candidate.raw_text else summary
    )

    # spec 0429 阶段 B: F3 路径必须先建 IntakeCandidate（孤儿避免 + 简历库可见）
    # spec 2026-05-15: job_id 透传 — 候选人 primary 岗位绑定
    cand_row = _ensure_candidate_for_f3(
        db, user_id=user_id, boss_id=candidate.boss_id, name=candidate.name,
        education=candidate.education, work_years=candidate.work_years,
        intended_job=candidate.intended_job, skills_csv=skills_csv,
        latest_work_brief=candidate.latest_work_brief, raw_text=raw_text,
        job_id=job_id,
    )

    if existing:
        existing.name = candidate.name
        existing.education = candidate.education or existing.education
        existing.work_years = candidate.work_years or existing.work_years
        existing.job_intention = candidate.intended_job or existing.job_intention
        existing.skills = skills_csv or existing.skills
        existing.work_experience = (
            candidate.latest_work_brief or existing.work_experience
        )
        existing.raw_text = raw_text
        existing.updated_at = now
        # spec 0429 阶段 C: 反向键回填
        if not existing.intake_candidate_id:
            existing.intake_candidate_id = cand_row.id
        # 维护 candidate 的 promoted_resume_id 反向链
        if not cand_row.promoted_resume_id:
            cand_row.promoted_resume_id = existing.id
        # spec 2026-05-15: Resume.job_id NULL → 实值时回填(与 candidate 同步)
        if job_id and not existing.job_id:
            existing.job_id = job_id
        # 故意不动: status, greet_status, greeted_at, ai_parsed, ai_score, ai_summary
        db.commit()
        db.refresh(existing)
        return existing

    r = Resume(
        user_id=user_id,
        name=candidate.name,
        boss_id=candidate.boss_id,
        education=candidate.education,
        work_years=candidate.work_years,
        job_intention=candidate.intended_job,
        skills=skills_csv,
        work_experience=candidate.latest_work_brief,
        source="boss_zhipin",
        raw_text=raw_text,
        status="passed",
        greet_status="none",
        created_at=now,
        updated_at=now,
        # spec 0429 阶段 C: 反向键 1:1
        intake_candidate_id=cand_row.id,
        # spec 2026-05-15: F3 路径透传 job_id, 候选人 primary 岗位归属
        job_id=job_id,
    )
    try:
        db.add(r)
        db.commit()
        db.refresh(r)
        # 维护正向键
        cand_row.promoted_resume_id = r.id
        db.commit()
        return r
    except IntegrityError:
        # Race: 另一路并发 writer 先插入了相同 (user_id, boss_id).
        # UNIQUE 索引触发 IntegrityError → rollback 并回查获胜行返回.
        db.rollback()
        winner = (
            db.query(Resume)
            .filter(Resume.user_id == user_id, Resume.boss_id == candidate.boss_id)
            .first()
        )
        if winner is None:
            # 若 IntegrityError 但没查到行 (不应发生), 抛出以暴露问题.
            raise
        # 反向键补刀
        if not winner.intake_candidate_id:
            winner.intake_candidate_id = cand_row.id
            db.commit()
        return winner


import logging
from app.core.audit.logger import log_event
from app.modules.auth.models import User
from app.modules.recruit_bot.schemas import RecruitDecision, UsageInfo
from app.modules.screening.models import Job
from app.modules.matching.service import MatchingService

logger = logging.getLogger(__name__)


def _today_start_utc() -> datetime:
    """当日 UTC 零点 (配额窗口起点)."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_daily_usage(db: Session, user_id: int) -> UsageInfo:
    """返该 user 今日已打招呼次数 + 配额."""
    user = db.query(User).filter(User.id == user_id).first()
    cap = user.daily_cap if user else 1000
    start = _today_start_utc()
    used = (
        db.query(Resume)
        .filter(
            Resume.user_id == user_id,
            Resume.greet_status == "greeted",
            Resume.greeted_at >= start,
        )
        .count()
    )
    return UsageInfo(used=used, cap=cap, remaining=max(0, cap - used))


async def evaluate_and_record(
    db: Session, user_id: int, job_id: int,
    candidate: "ScrapedCandidate",
    strategy: str | None = None,
) -> RecruitDecision:
    """核心决策: daily_cap → upsert → 已 greeted skip → F2 score → threshold → record."""
    # 1. daily_cap 先于一切 (省打分钱, 也避免无意义 upsert)
    usage = get_daily_usage(db, user_id)
    if usage.remaining <= 0:
        log_event(
            f_stage="F3_evaluate", action="blocked_daily_cap",
            entity_type="job", entity_id=job_id,
            input_payload={"boss_id": candidate.boss_id, "usage": usage.model_dump()},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="blocked_daily_cap",
            reason=f"今日已打 {usage.used}/{usage.cap}",
        )

    # 2. job 归属 + competency_model
    job = (
        db.query(Job)
        .filter(Job.id == job_id, Job.user_id == user_id)
        .first()
    )
    if not job:
        raise ValueError(f"job {job_id} not found for user {user_id}")
    # 3. upsert resume
    # spec 2026-05-15: job_id 一路透传到 candidate+resume,确定性绑定 primary 岗位
    resume = upsert_resume_by_boss_id(db, user_id=user_id, candidate=candidate, job_id=job_id)

    # 4. 已 greeted 跳过 (历史覆盖, 不重复打招呼)
    if resume.greet_status == "greeted":
        log_event(
            f_stage="F3_evaluate", action="skipped_already_greeted",
            entity_type="resume", entity_id=resume.id,
            input_payload={"boss_id": candidate.boss_id},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="skipped_already_greeted",
            resume_id=resume.id,
            reason="历史已打过招呼",
        )

    # 5. school_only 策略: 跳过 LLM，仅看院校层次标签
    # 匹配含 985/211/双一流 的任意 tag 格式 (如 "211院校", "985院校", "双一流院校" 等)
    import re as _re
    _TIER_RE = _re.compile(r'985|211|双一流')
    if strategy == 'school_only':
        tier_tags = candidate.school_tier_tags or []
        has_tier = any(_TIER_RE.search(t) for t in tier_tags)
        if has_tier:
            resume.status = "passed"
            resume.greet_status = "pending_greet"
            db.commit()
            log_event(
                f_stage="F3_evaluate", action="should_greet",
                entity_type="resume", entity_id=resume.id,
                input_payload={"boss_id": candidate.boss_id, "tier_tags": tier_tags, "strategy": "school_only"},
                reviewer_id=user_id,
            )
            return RecruitDecision(
                decision="should_greet",
                resume_id=resume.id,
                reason=f"985/211/双一流院校: {', '.join(tier_tags)}",
            )
        else:
            resume.status = "rejected"
            resume.reject_reason = "school_only: 非985/211/双一流院校"
            db.commit()
            log_event(
                f_stage="F3_evaluate", action="rejected_low_score",
                entity_type="resume", entity_id=resume.id,
                input_payload={"boss_id": candidate.boss_id, "tier_tags": tier_tags, "strategy": "school_only"},
                reviewer_id=user_id,
            )
            return RecruitDecision(
                decision="rejected_low_score",
                resume_id=resume.id,
                reason=f"非985/211/双一流院校 (tags={tier_tags})",
            )

    # 6. F2 匹配打分 (school_only已在上面返回，到这里必须有competency_model)
    if not job.competency_model:
        log_event(
            f_stage="F3_evaluate", action="error_no_competency",
            entity_type="job", entity_id=job_id,
            input_payload={"boss_id": candidate.boss_id},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="error_no_competency",
            reason=f"job {job_id} 能力模型未生成",
        )
    svc = MatchingService(db)
    try:
        result = await svc.score_pair(resume.id, job.id, triggered_by="F3")
    except Exception as e:
        logger.exception(f"F3 score_pair failed: {e}")
        log_event(
            f_stage="F3_evaluate", action="error_scoring",
            entity_type="resume", entity_id=resume.id,
            input_payload={"boss_id": candidate.boss_id},
            output_payload={"error": str(e)},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="error_scoring",
            resume_id=resume.id,
            reason=f"打分异常: {e}",
        )

    threshold = job.greet_threshold
    score = int(result.total_score)

    # 6. 阈值判定 + 更新 resume
    if score >= threshold:
        resume.status = "passed"
        resume.greet_status = "pending_greet"
        db.commit()
        log_event(
            f_stage="F3_evaluate", action="should_greet",
            entity_type="resume", entity_id=resume.id,
            input_payload={"boss_id": candidate.boss_id, "score": score, "threshold": threshold},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="should_greet",
            resume_id=resume.id, score=score, threshold=threshold,
            reason=f"分 {score} ≥ 阈值 {threshold}",
        )
    else:
        resume.status = "rejected"
        resume.reject_reason = f"F3 分{score}低于阈值{threshold}"
        db.commit()
        log_event(
            f_stage="F3_evaluate", action="rejected_low_score",
            entity_type="resume", entity_id=resume.id,
            input_payload={"boss_id": candidate.boss_id, "score": score, "threshold": threshold},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="rejected_low_score",
            resume_id=resume.id, score=score, threshold=threshold,
            reason=f"分 {score} < 阈值 {threshold}",
        )


def record_greet_sent(
    db: Session, user_id: int, resume_id: int,
    success: bool, error_msg: str = "",
) -> None:
    """记录打招呼动作结果. 幂等: 已 greeted 的 resume 再调 success=True 不动 greeted_at."""
    resume = (
        db.query(Resume)
        .filter(Resume.id == resume_id, Resume.user_id == user_id)
        .first()
    )
    if not resume:
        raise ValueError(f"resume {resume_id} not found for user {user_id}")

    now = datetime.now(timezone.utc)

    if success:
        if resume.greet_status != "greeted":
            resume.greet_status = "greeted"
            resume.greeted_at = now
            db.commit()
        log_event(
            f_stage="F3_greet_sent", action="greet_sent",
            entity_type="resume", entity_id=resume_id,
            input_payload={"boss_id": resume.boss_id},
            reviewer_id=user_id,
        )
    else:
        resume.greet_status = "failed"
        db.commit()
        log_event(
            f_stage="F3_greet_failed", action="greet_failed",
            entity_type="resume", entity_id=resume_id,
            input_payload={"boss_id": resume.boss_id},
            output_payload={"error": error_msg},
            reviewer_id=user_id,
        )
