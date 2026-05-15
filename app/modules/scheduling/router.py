"""面试安排 API 路由"""
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


async def _cleanup_interview_external(interview) -> dict:
    """清理一场面试对应的外部状态：腾讯会议 + 飞书日历。

    Best-effort：任何一步失败都不抛异常，只记录结果。调用方根据返回值决定
    是否把失败信息写进 notes / 返回给前端。

    Returns: {"tencent": "cancelled|skipped|failed:...", "feishu": "deleted|skipped|failed:..."}
    """
    result = {"tencent": "skipped", "feishu": "skipped"}

    # 1) 腾讯会议
    if interview.meeting_id and interview.meeting_account:
        try:
            from app.adapters.tencent_meeting_web import cancel_meeting
            r = await cancel_meeting(interview.meeting_id, account_label=interview.meeting_account)
            if r.get("success"):
                result["tencent"] = "cancelled"
                logger.info(f"Cancelled Tencent meeting {interview.meeting_id}")
            else:
                result["tencent"] = f"failed: {r.get('error', 'unknown')}"
                logger.warning(f"Cancel Tencent meeting failed: {r.get('error')}")
        except Exception as e:
            result["tencent"] = f"failed: {e}"
            logger.error(f"Cancel Tencent meeting exception: {e}")

    # 2) 飞书日历事件
    if interview.feishu_event_id:
        try:
            from app.adapters.feishu import FeishuAdapter
            feishu = FeishuAdapter()
            if await feishu.delete_calendar_event(interview.feishu_event_id):
                result["feishu"] = "deleted"
                logger.info(f"Deleted Feishu calendar event {interview.feishu_event_id}")
            else:
                result["feishu"] = "failed: delete_calendar_event returned False"
        except Exception as e:
            result["feishu"] = f"failed: {e}"
            logger.error(f"Delete Feishu event exception: {e}")

    return result


async def _create_feishu_event_for_interview(interview, interviewer, resume) -> str:
    """给一场面试（重新）创建飞书日历事件。返回 event_id 或空字符串。"""
    if not interviewer or not interviewer.feishu_user_id:
        return ""
    try:
        from app.adapters.feishu import FeishuAdapter
        feishu = FeishuAdapter()
        beijing_start = interview.start_time + timedelta(hours=8)
        beijing_end = interview.end_time + timedelta(hours=8)
        summary = f"面试 - {resume.name if resume else '候选人'}"
        description = (
            f"候选人：{resume.name if resume else '候选人'}\n"
            f"会议链接：{interview.meeting_link or '(待创建)'}"
        )
        return await feishu.create_calendar_event(
            summary=summary,
            description=description,
            start_timestamp=int(beijing_start.timestamp()),
            end_timestamp=int(beijing_end.timestamp()),
            attendee_open_id=interviewer.feishu_user_id,
        )
    except Exception as e:
        logger.error(f"Create Feishu event failed: {e}")
        return ""

from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.scheduling.service import SchedulingService
from app.modules.scheduling.schemas import (
    InterviewerCreate,
    InterviewerResponse,
    InterviewerListResponse,
    AvailabilityCreate,
    AvailabilityResponse,
    InterviewCreate,
    InterviewUpdate,
    InterviewResponse,
    InterviewListResponse,
    MatchSlotsRequest,
    MatchSlotsResponse,
)

router = APIRouter()


def get_scheduling_service(db: Session = Depends(get_db)) -> SchedulingService:
    return SchedulingService(db)


# ── Interviewers ──


async def _ensure_feishu_id(data: InterviewerCreate) -> InterviewerCreate:
    """如果 feishu_user_id 没填，尝试按 phone/email 从飞书反查并回填。

    校验规则：
    - 至少要填 phone / email / feishu_user_id 中的一个
    - 若 feishu_user_id 已填，直接返回
    - 若未填但能查到 → 回填
    - 若未填且查不到 → 抛 400 带明确原因
    """
    if data.feishu_user_id:
        return data
    if not data.phone and not data.email:
        raise HTTPException(
            status_code=422,
            detail="必须填写手机号、邮箱或飞书 open_id 中的至少一项",
        )
    from app.adapters.feishu import FeishuAdapter
    feishu = FeishuAdapter()
    if not feishu.is_configured():
        raise HTTPException(
            status_code=400,
            detail="飞书未配置，无法自动反查 open_id，请手动填写",
        )
    uid = await feishu.lookup_user_id(phone=data.phone, email=data.email)
    if not uid:
        hint = f"手机号 {data.phone}" if data.phone else f"邮箱 {data.email}"
        raise HTTPException(
            status_code=400,
            detail=f"在飞书通讯录里未找到 {hint} 对应的用户。请确认该用户已加入贵司飞书，或手动填写 open_id",
        )
    return data.model_copy(update={"feishu_user_id": uid})


@router.post("/interviewers", response_model=InterviewerResponse, status_code=201)
async def create_interviewer(
    data: InterviewerCreate,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    # 防呆：手机号去重 — 必须按 user_id 隔离, 否则跨账号会误阻塞 (多租户隔离 bug)
    from app.modules.scheduling.models import Interviewer
    if data.phone:
        dup = (
            service.db.query(Interviewer)
            .filter(Interviewer.user_id == user_id, Interviewer.phone == data.phone)
            .first()
        )
        if dup:
            raise HTTPException(status_code=409, detail=f"手机号 {data.phone} 已被面试官「{dup.name}」使用")
    data = await _ensure_feishu_id(data)
    interviewer = service.create_interviewer(data)
    interviewer.user_id = user_id
    service.db.commit()
    service.db.refresh(interviewer)
    return interviewer


@router.get("/interviewers", response_model=InterviewerListResponse)
def list_interviewers(
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    from app.modules.scheduling.models import Interviewer as _IVWModel
    rows = service.db.query(_IVWModel).filter(_IVWModel.user_id == user_id).order_by(_IVWModel.id).all()
    return InterviewerListResponse(total=len(rows), items=rows)


@router.patch("/interviewers/{interviewer_id}", response_model=InterviewerResponse)
async def update_interviewer(
    interviewer_id: int,
    data: InterviewerCreate,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    from app.modules.scheduling.models import Interviewer
    interviewer = service.db.query(Interviewer).filter(Interviewer.id == interviewer_id).first()
    if not interviewer:
        raise HTTPException(status_code=404, detail="面试官不存在")
    if interviewer.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权修改该面试官")
    data = await _ensure_feishu_id(data)
    for key, val in data.model_dump().items():
        if val is not None:
            setattr(interviewer, key, val)
    service.db.commit()
    service.db.refresh(interviewer)
    return interviewer


@router.delete("/interviewers/{interviewer_id}", status_code=204)
def delete_interviewer(
    interviewer_id: int,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    # 防呆：检查关联面试
    from app.modules.scheduling.models import Interview, Interviewer, InterviewerAvailability
    from app.modules.notification.models import NotificationLog
    interviewer = service.db.query(Interviewer).filter(Interviewer.id == interviewer_id).first()
    if not interviewer:
        raise HTTPException(status_code=404, detail="面试官不存在")
    if interviewer.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权删除该面试官")
    linked = service.db.query(Interview).filter(
        Interview.interviewer_id == interviewer_id,
        Interview.status != "cancelled",
    ).count()
    if linked > 0:
        raise HTTPException(
            status_code=409,
            detail=f"该面试官有 {linked} 场待面试，请先取消或重新分配后再删除"
        )
    # 级联清 cancelled Interview + 其 NotificationLog + InterviewerAvailability,
    # 否则 FK (无 ondelete) 让 db.delete(interviewer) 挂 IntegrityError
    cancelled_iv_ids = [
        i for (i,) in service.db.query(Interview.id).filter(
            Interview.interviewer_id == interviewer_id,
            Interview.status == "cancelled",
        ).all()
    ]
    if cancelled_iv_ids:
        service.db.query(NotificationLog).filter(
            NotificationLog.interview_id.in_(cancelled_iv_ids)
        ).delete(synchronize_session=False)
        service.db.query(Interview).filter(
            Interview.id.in_(cancelled_iv_ids)
        ).delete(synchronize_session=False)
    service.db.query(InterviewerAvailability).filter(
        InterviewerAvailability.interviewer_id == interviewer_id
    ).delete(synchronize_session=False)
    if not service.delete_interviewer(interviewer_id):
        raise HTTPException(status_code=404, detail="面试官不存在")


# ── Availability ──

@router.post("/availability", response_model=AvailabilityResponse, status_code=201)
def add_availability(
    data: AvailabilityCreate,
    service: SchedulingService = Depends(get_scheduling_service),
):
    return service.add_availability(data)


@router.get("/availability/{interviewer_id}", response_model=list[AvailabilityResponse])
def get_availability(
    interviewer_id: int,
    service: SchedulingService = Depends(get_scheduling_service),
):
    return service.get_availability(interviewer_id)


# ── Slot matching ──

@router.post("/match-slots", response_model=MatchSlotsResponse)
def match_slots(
    data: MatchSlotsRequest,
    service: SchedulingService = Depends(get_scheduling_service),
):
    slots = service.match_slots(
        interviewer_id=data.interviewer_id,
        candidate_slots=data.candidate_slots,
        duration_minutes=data.duration_minutes,
    )
    return {"available_slots": slots}


# ── Interviews ──

@router.delete("/interviews/clear-all", status_code=200)
def clear_all_interviews(
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    """批量清空面试。

    两阶段：
    1. 先收集所有带外部状态的面试（meeting_id 或 feishu_event_id 非空），
       把它们的字段 snapshot 出来
    2. 立即 DB 清空，返回 count 让用户界面不阻塞
    3. 后台线程逐个 cancel Tencent meeting + delete Feishu event
    """
    from app.modules.scheduling.models import Interview

    # Step 1: snapshot 外部状态（仅当前用户的面试）
    snapshots = []
    for iv in service.db.query(Interview).filter(Interview.user_id == user_id).all():
        if iv.meeting_id or iv.feishu_event_id:
            snapshots.append({
                "id": iv.id,
                "meeting_id": iv.meeting_id,
                "meeting_account": iv.meeting_account,
                "feishu_event_id": iv.feishu_event_id,
            })

    user_interview_ids = [
        i for (i,) in service.db.query(Interview.id)
        .filter(Interview.user_id == user_id).all()
    ]
    count = len(user_interview_ids)
    if user_interview_ids:
        from app.modules.notification.models import NotificationLog
        service.db.query(NotificationLog).filter(
            NotificationLog.interview_id.in_(user_interview_ids)
        ).delete(synchronize_session=False)
        service.db.query(Interview).filter(
            Interview.id.in_(user_interview_ids)
        ).delete(synchronize_session=False)
    service.db.commit()

    # Step 2: 后台线程跑 cleanup，不阻塞响应
    if snapshots:
        import threading
        import asyncio as _aio

        def _bg_cleanup(items):
            async def _run():
                from app.adapters.tencent_meeting_web import cancel_meeting
                from app.adapters.feishu import FeishuAdapter
                feishu = FeishuAdapter()
                for it in items:
                    if it["meeting_id"] and it["meeting_account"]:
                        try:
                            r = await cancel_meeting(it["meeting_id"], account_label=it["meeting_account"])
                            logger.info(
                                f"[bg-cleanup] iv#{it['id']} tencent "
                                f"{'ok' if r.get('success') else 'fail: ' + str(r.get('error'))}"
                            )
                        except Exception as e:
                            logger.error(f"[bg-cleanup] iv#{it['id']} tencent exception: {e}")
                    if it["feishu_event_id"]:
                        try:
                            ok = await feishu.delete_calendar_event(it["feishu_event_id"])
                            logger.info(f"[bg-cleanup] iv#{it['id']} feishu {'ok' if ok else 'fail'}")
                        except Exception as e:
                            logger.error(f"[bg-cleanup] iv#{it['id']} feishu exception: {e}")
                logger.info(f"[bg-cleanup] finished {len(items)} items")

            try:
                _aio.run(_run())
            except Exception as e:
                logger.error(f"[bg-cleanup] top-level exception: {e}")

        threading.Thread(target=_bg_cleanup, args=(snapshots,), daemon=True).start()

    return {
        "deleted": count,
        "background_cleanup": len(snapshots),
        "message": (
            f"已清空 {count} 份面试"
            + (f"，{len(snapshots)} 场有腾讯会议/飞书日程，正在后台清理" if snapshots else "")
        ),
    }


def _enrich_interview(db: Session, iv) -> dict:
    """BUG-076 修复：把 Interview 转成 InterviewResponse-shape，附 resume_name / candidate_id / interviewer_name。"""
    from app.modules.resume.models import Resume as _R
    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.scheduling.models import Interviewer as _Iver

    resume_name = ""
    candidate_id = None
    if iv.resume_id:
        r = db.query(_R).filter_by(id=iv.resume_id).first()
        if r:
            resume_name = r.name or ""
            cand = db.query(IntakeCandidate).filter_by(promoted_resume_id=r.id).first()
            if cand:
                candidate_id = cand.id
    interviewer_name = ""
    if iv.interviewer_id:
        ier = db.query(_Iver).filter_by(id=iv.interviewer_id).first()
        if ier:
            interviewer_name = ier.name or ""
    return {
        "id": iv.id,
        "resume_id": iv.resume_id,
        "resume_name": resume_name,
        "candidate_id": candidate_id,
        "interviewer_id": iv.interviewer_id,
        "interviewer_name": interviewer_name,
        "job_id": iv.job_id,
        "start_time": iv.start_time,
        "end_time": iv.end_time,
        "meeting_topic": iv.meeting_topic or "",
        "meeting_link": iv.meeting_link or "",
        "meeting_password": iv.meeting_password or "",
        "meeting_account": getattr(iv, "meeting_account", "") or "",
        "meeting_id": getattr(iv, "meeting_id", "") or "",
        "status": iv.status or "",
        "notes": iv.notes or "",
        "created_at": iv.created_at,
        "updated_at": iv.updated_at,
    }


@router.post("/interviews", response_model=InterviewResponse, status_code=201)
def create_interview(
    data: InterviewCreate,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    from app.modules.scheduling.models import Interview

    # 防呆：不能安排过去的时间
    if data.start_time.astimezone(timezone.utc).replace(tzinfo=None) < datetime.utcnow():
        raise HTTPException(status_code=400, detail="面试时间不能早于当前时间")

    # 前端简历库以 IntakeCandidate.id 为行键，但 Interview.resume_id 必须是 Resume.id。
    # 翻译策略：优先 candidate（避免与 Resume 表 id 撞数值时找错人），缺失则按需 promote。
    # BUG-066 修复：先做 duplicate / 时段 / 归属 检查，全部通过后再 promote 出 Resume，
    # 避免 promote 后续失败时残留副作用。
    # BUG-065 修复：异常细节仅写日志，对外不暴露 SQL/Python 错误。
    from app.modules.resume.models import Resume as _ResumeModel
    from app.modules.im_intake.candidate_model import IntakeCandidate

    resume = None
    pending_promote_candidate = None
    cand = service.db.query(IntakeCandidate).filter_by(id=data.resume_id, user_id=user_id).first()
    if cand is not None:
        if cand.promoted_resume_id:
            resume = service.db.query(_ResumeModel).filter_by(id=cand.promoted_resume_id).first()
            if resume is not None and resume.user_id != user_id:
                resume = None  # FK 腐化，拒绝
        if resume is None:
            pending_promote_candidate = cand  # 推迟到所有校验通过再 promote
    else:
        resume = service.db.query(_ResumeModel).filter_by(id=data.resume_id, user_id=user_id).first()

    if resume is None and pending_promote_candidate is None:
        raise HTTPException(status_code=404, detail="简历不存在")

    if resume is not None and resume.user_id != user_id:
        raise HTTPException(status_code=404, detail="简历不存在")

    # 防呆：同一候选人是否已有待面试安排
    # BUG-079 修复：completed/cancelled 都不阻塞新面试，允许多轮
    if resume is not None:
        existing_for_candidate = service.db.query(Interview).filter(
            Interview.resume_id == resume.id,
            ~Interview.status.in_(["cancelled", "completed"]),
        ).first()
        if existing_for_candidate:
            raise HTTPException(
                status_code=409,
                detail=f"该候选人已有待面试安排（面试ID: {existing_for_candidate.id}），请先取消旧面试或编辑现有面试"
            )

    # 校验全部通过 → 此时才 promote（确保失败时不留 Resume 副作用）
    if pending_promote_candidate is not None:
        try:
            from app.modules.im_intake.promote import promote_to_resume
            resume = promote_to_resume(service.db, pending_promote_candidate, user_id=user_id)
            service.db.commit()
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).error(f"promote_to_resume failed: {_e}", exc_info=True)
            service.db.rollback()
            raise HTTPException(status_code=500, detail="无法落库简历，请稍后重试")
        # promote 后再做一次 duplicate 检查（极小概率 race）
        existing_for_candidate = service.db.query(Interview).filter(
            Interview.resume_id == resume.id,
            ~Interview.status.in_(["cancelled", "completed"]),
        ).first()
        if existing_for_candidate:
            raise HTTPException(status_code=409, detail="该候选人已有待面试安排")

    data.resume_id = resume.id
    interview = service.create_interview(data, user_id=user_id)
    if interview is None:
        raise HTTPException(status_code=409, detail="该时段与面试官的其他面试冲突，请选择其他时间")
    return _enrich_interview(service.db, interview)


@router.get("/interviews", response_model=InterviewListResponse)
def list_interviews(
    interviewer_id: int | None = None,
    resume_id: int | None = None,
    status: str | None = None,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    result = service.list_interviews(
        interviewer_id=interviewer_id, resume_id=resume_id, status=status, user_id=user_id
    )
    if isinstance(result, dict) and "items" in result:
        result["items"] = [_enrich_interview(service.db, iv) for iv in result["items"]]
    return result


@router.get("/interviews/{interview_id}", response_model=InterviewResponse)
def get_interview(
    interview_id: int,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    interview = service.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试不存在")
    # BUG-018 修复：他人资源与不存在均返 404
    if interview.user_id != user_id:
        raise HTTPException(status_code=404, detail="面试不存在")
    return _enrich_interview(service.db, interview)


@router.patch("/interviews/{interview_id}", response_model=InterviewResponse)
async def update_interview(
    interview_id: int,
    data: InterviewUpdate,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    """更新面试。

    特殊逻辑：如果本次请求改动了 start_time 或 end_time，且该面试已经有
    绑定的腾讯会议，则走 reschedule 流水线：
      1. 先用新时间从账号池挑一个可用账号
      2. 在腾讯会议网页上创建新会议（与旧会议并存）
      3. 成功后再写入 DB 并尝试取消旧会议（best-effort，失败记 notes）
      4. 任一关键步骤失败则整体回滚，DB 不动
    这样保证：任何时候用户都拥有至少一场可用的会议。
    """
    from app.modules.scheduling.models import Interview
    interview = service.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试不存在")
    if interview.user_id != user_id:
        raise HTTPException(status_code=404, detail="面试不存在")

    # 判断时间是否真的变了（payload 里的时间 vs DB 里的时间）
    _from_req = data.model_dump(exclude_none=True)
    new_start = _from_req.get("start_time")
    new_end = _from_req.get("end_time")
    old_start = interview.start_time
    old_end = interview.end_time

    def _naive_utc(dt):
        if dt is None:
            return None
        if dt.tzinfo is not None:
            from datetime import timezone as _tz
            return dt.astimezone(_tz.utc).replace(tzinfo=None)
        return dt

    new_start_cmp = _naive_utc(new_start)
    new_end_cmp = _naive_utc(new_end)

    time_changed = (
        (new_start_cmp is not None and new_start_cmp != old_start) or
        (new_end_cmp is not None and new_end_cmp != old_end)
    )
    has_existing_meeting = bool(interview.meeting_link)

    if time_changed and has_existing_meeting:
        # 走 reschedule 流水线
        from datetime import timedelta, timezone as tz
        from app.modules.meeting.account_pool import pick_available_account
        from app.adapters.tencent_meeting_web import create_meeting, cancel_meeting
        from app.modules.resume.models import Resume

        effective_start = new_start_cmp or old_start
        effective_end = new_end_cmp or old_end

        # Step 1: 挑账号（exclude 自己，因为自己的旧时段不算冲突）
        try:
            account = pick_available_account(
                service.db, effective_start, effective_end, exclude_interview_id=interview.id
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"挑选账号失败: {e}")

        # Step 2: 渲染 topic + 时间字符串（北京时间）
        resume = service.db.query(Resume).filter(Resume.id == interview.resume_id).first()
        from app.modules.scheduling.models import Interviewer
        interviewer = service.db.query(Interviewer).filter(Interviewer.id == interview.interviewer_id).first()
        candidate_name = resume.name if resume else "候选人"
        interviewer_name = interviewer.name if interviewer else "面试官"
        beijing_start = effective_start + timedelta(hours=8)
        beijing_end = effective_end + timedelta(hours=8)
        topic = f"面试-{candidate_name}-{interviewer_name}"

        # Step 3: 先创建新会议（如果失败，不动 DB，旧会议保留可用）
        new_result = await create_meeting(
            topic,
            beijing_start.strftime("%Y/%m/%d"), beijing_start.strftime("%H:%M"),
            beijing_end.strftime("%Y/%m/%d"), beijing_end.strftime("%H:%M"),
            account_label=account,
        )
        if not new_result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=f"时间已修改但新会议创建失败：{new_result.get('error', '未知')}。"
                       "旧会议仍可用，请稍后重试或手动处理。",
            )

        # Step 4: 记下旧会议信息 + 旧飞书日程，用于稍后取消
        old_meeting_id = interview.meeting_id
        old_meeting_account = interview.meeting_account
        old_feishu_event_id = interview.feishu_event_id

        # Step 5: DB 批量更新（时间 + 新会议信息 + 其他 payload 字段）
        for key, val in _from_req.items():
            setattr(interview, key, val)
        interview.meeting_link = new_result["link"]
        interview.meeting_password = new_result.get("password", "")
        interview.meeting_account = account
        interview.meeting_id = new_result.get("meeting_id", "")

        tz8 = tz(timedelta(hours=8))
        now_str = datetime.now(tz8).strftime("%m-%d %H:%M")
        reschedule_note = (
            f"[{now_str}] 会议已改期到 {beijing_start.strftime('%m-%d %H:%M')}，"
            f"新会议账号={account} ID={new_result.get('meeting_id', '')}"
        )
        interview.notes = f"{interview.notes}\n{reschedule_note}" if interview.notes else reschedule_note
        service.db.commit()
        service.db.refresh(interview)

        # Step 6: best-effort 取消旧会议；失败只记 notes 不抛
        if old_meeting_id and old_meeting_account:
            try:
                cancel_result = await cancel_meeting(old_meeting_id, account_label=old_meeting_account)
                if cancel_result.get("success"):
                    extra = f"[{now_str}] 旧会议 {old_meeting_id} 已在腾讯会议上取消"
                else:
                    extra = (
                        f"[{now_str}] ⚠️ 旧会议 {old_meeting_id} 自动取消失败"
                        f"（{cancel_result.get('error', '未知')}）。请手动在腾讯会议（账号 {old_meeting_account}）里取消。"
                    )
                interview.notes = f"{interview.notes}\n{extra}"
                service.db.commit()
                service.db.refresh(interview)
            except Exception as e:
                logger.error(f"cancel old meeting exception: {e}")

        # Step 7: 同步更新飞书日历：删旧事件 + 新时间建新事件
        if old_feishu_event_id:
            try:
                from app.adapters.feishu import FeishuAdapter
                feishu = FeishuAdapter()
                await feishu.delete_calendar_event(old_feishu_event_id)
                logger.info(f"Deleted old Feishu event {old_feishu_event_id}")
            except Exception as e:
                logger.error(f"Delete old Feishu event failed: {e}")
            interview.feishu_event_id = ""

        new_event_id = await _create_feishu_event_for_interview(interview, interviewer, resume)
        if new_event_id:
            interview.feishu_event_id = new_event_id
            cal_extra = f"[{now_str}] 飞书日程已同步到新时间 (event_id={new_event_id})"
        else:
            cal_extra = f"[{now_str}] ⚠️ 飞书日程同步失败，候选人日历可能仍显示旧时间"
        interview.notes = f"{interview.notes}\n{cal_extra}"
        service.db.commit()
        service.db.refresh(interview)

        return _enrich_interview(service.db, interview)

    # 普通更新路径（没改时间，或原本就没会议）
    interview = service.update_interview(interview_id, data)
    if not interview:
        raise HTTPException(status_code=404, detail="面试不存在")
    return _enrich_interview(service.db, interview)


@router.delete("/interviews/{interview_id}", status_code=204)
async def delete_interview(
    interview_id: int,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    """删除面试 + 清理它绑定的外部状态（腾讯会议、飞书日历）。

    外部清理是 best-effort —— 哪怕取消腾讯会议失败也会继续删 DB 记录，
    避免本地数据和远程数据永远不一致。失败信息只走日志。
    """
    interview = service.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试不存在")
    if interview.user_id != user_id:
        raise HTTPException(status_code=404, detail="面试不存在")

    cleanup = await _cleanup_interview_external(interview)
    logger.info(f"Delete interview {interview_id} external cleanup: {cleanup}")

    # 同时清 NotificationLog 软引用（无 FK，否则留孤儿行）
    from app.modules.notification.models import NotificationLog
    service.db.query(NotificationLog).filter(
        NotificationLog.interview_id == interview_id
    ).delete(synchronize_session=False)
    service.db.delete(interview)
    service.db.commit()


@router.get("/interviewers/{interviewer_id}/freebusy")
async def get_interviewer_freebusy(
    interviewer_id: int,
    days: int = 5,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    """查询面试官未来 N 天的飞书日历忙碌时段"""
    from app.modules.scheduling.models import Interviewer
    interviewer = service.db.query(Interviewer).filter(Interviewer.id == interviewer_id).first()
    if not interviewer:
        raise HTTPException(status_code=404, detail="面试官不存在")
    # 多租户隔离: 不允许跨账号读取面试官日历 (返回 404 而非 403,避免泄露存在性)
    if interviewer.user_id != user_id:
        raise HTTPException(status_code=404, detail="面试官不存在")
    if not interviewer.feishu_user_id:
        return {"interviewer": interviewer.name, "busy_slots": [], "message": "未配置飞书ID，无法查询日历"}

    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)

    from app.adapters.feishu import FeishuAdapter
    feishu = FeishuAdapter()

    import httpx
    token = await feishu._get_token()
    if not token:
        return {"interviewer": interviewer.name, "busy_slots": [], "message": "获取飞书token失败"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{feishu.BASE_URL}/calendar/v4/freebusy/list",
            headers={"Authorization": f"Bearer {token}"},
            params={"user_id_type": "open_id"},
            json={
                "time_min": start.isoformat(),
                "time_max": end.isoformat(),
                "user_id": interviewer.feishu_user_id,
            },
        )
        data = resp.json()

    busy_slots = []
    if data.get("code") == 0:
        for item in data.get("data", {}).get("freebusy_list", []):
            busy_slots.append({
                "start": item.get("start_time", ""),
                "end": item.get("end_time", ""),
            })

    # 也查询系统内已安排的面试 (按 user_id 隔离, 不读跨账号 Interview)
    from app.modules.scheduling.models import Interview
    existing = (
        service.db.query(Interview)
        .filter(
            Interview.interviewer_id == interviewer_id,
            Interview.user_id == user_id,
            Interview.status != "cancelled",
            Interview.start_time >= start,
            Interview.start_time <= end,
        )
        .all()
    )
    for iv in existing:
        busy_slots.append({
            "start": (iv.start_time + timedelta(hours=8)).isoformat(),
            "end": (iv.end_time + timedelta(hours=8)).isoformat(),
            "type": "interview",
        })

    return {
        "interviewer": interviewer.name,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "busy_slots": busy_slots,
    }


@router.post("/interviews/{interview_id}/ask-time")
async def ask_interviewer_time(
    interview_id: int,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    """向面试官发送飞书消息确认面试时间"""
    interview = service.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试不存在")
    if interview.user_id != user_id:
        raise HTTPException(status_code=404, detail="面试不存在")

    from app.modules.scheduling.models import Interviewer
    from app.modules.resume.models import Resume
    interviewer = service.db.query(Interviewer).filter(Interviewer.id == interview.interviewer_id).first()
    resume = service.db.query(Resume).filter(Resume.id == interview.resume_id).first()

    if not interviewer or not interviewer.feishu_user_id:
        raise HTTPException(status_code=400, detail="面试官未配置飞书ID")

    from datetime import timedelta, timezone
    beijing_time = interview.start_time + timedelta(hours=8)
    time_str = beijing_time.strftime("%Y-%m-%d %H:%M")
    candidate_name = resume.name if resume else "候选人"

    from app.adapters.feishu import FeishuAdapter
    feishu = FeishuAdapter()

    msg = (
        f"【面试时间确认】\n\n"
        f"候选人：{candidate_name}\n"
        f"拟定时间：{time_str}\n\n"
        f"请直接回复：\n"
        f"・回复「有空」确认此时间\n"
        f"・回复「没空，XX时间可以」告知方便的时间\n\n"
        f"您的回复会自动同步到HR的面试安排系统中。"
    )

    success = await feishu.send_message(interviewer.feishu_user_id, msg)
    if not success:
        raise HTTPException(status_code=500, detail="飞书消息发送失败")

    # 记录到面试备注
    from datetime import datetime as dt
    now_str = dt.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    note = f"[{now_str}] 已发送时间确认给{interviewer.name}"
    interview.notes = f"{interview.notes}\n{note}" if interview.notes else note
    service.db.commit()

    return {"status": "sent", "message": f"已发送时间确认给 {interviewer.name}"}


@router.post("/interviews/{interview_id}/cancel", response_model=InterviewResponse)
async def cancel_interview(
    interview_id: int,
    service: SchedulingService = Depends(get_scheduling_service),
    user_id: int = Depends(get_current_user_id),
):
    """取消面试：先立即把 DB 状态置为 cancelled 并返回；
    然后后台清理腾讯会议 + 飞书日历，避免 Playwright 耗时导致前端超时。

    不删除 DB 记录，只把 status 置为 cancelled，保留审计痕迹。
    """
    interview = service.get_interview(interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="面试不存在")
    if interview.user_id != user_id:
        raise HTTPException(status_code=404, detail="面试不存在")

    # Step 1: 立即更新 DB —— 前端马上能看到 "已取消" 状态
    from datetime import timezone as _tz
    tz8 = _tz(timedelta(hours=8))
    now_str = datetime.now(tz8).strftime("%m-%d %H:%M")

    has_tencent = bool(interview.meeting_id and interview.meeting_account)
    has_feishu = bool(interview.feishu_event_id)

    if has_tencent or has_feishu:
        pending_note = f"[{now_str}] 面试已取消，正在后台清理外部资源（腾讯会议/飞书日历）…"
    else:
        pending_note = f"[{now_str}] 面试已取消"
    interview.notes = f"{interview.notes}\n{pending_note}" if interview.notes else pending_note

    # 保存清理需要用的信息到本地变量，DB 里先清掉飞书引用（避免重复删除）
    snapshot = {
        "id": interview.id,
        "meeting_id": interview.meeting_id,
        "meeting_account": interview.meeting_account,
        "feishu_event_id": interview.feishu_event_id,
    }
    interview.feishu_event_id = ""
    interview.status = "cancelled"
    service.db.commit()
    service.db.refresh(interview)

    # Step 2: 后台线程做腾讯会议取消 + 飞书日历删除 + 更新 notes
    if has_tencent or has_feishu:
        import threading
        import asyncio as _aio
        from app.database import SessionLocal
        from app.modules.scheduling.models import Interview

        def _bg_cleanup(snap):
            async def _run():
                tencent_res = "skipped"
                feishu_res = "skipped"
                if snap["meeting_id"] and snap["meeting_account"]:
                    try:
                        from app.adapters.tencent_meeting_web import cancel_meeting
                        r = await cancel_meeting(
                            snap["meeting_id"], account_label=snap["meeting_account"]
                        )
                        tencent_res = "cancelled" if r.get("success") else f"failed: {r.get('error','unknown')}"
                        logger.info(f"[cancel bg] iv#{snap['id']} tencent {tencent_res}")
                    except Exception as e:
                        tencent_res = f"failed: {e}"
                        logger.error(f"[cancel bg] iv#{snap['id']} tencent exception: {e}")
                if snap["feishu_event_id"]:
                    try:
                        from app.adapters.feishu import FeishuAdapter
                        feishu = FeishuAdapter()
                        ok = await feishu.delete_calendar_event(snap["feishu_event_id"])
                        feishu_res = "deleted" if ok else "failed: delete returned False"
                        logger.info(f"[cancel bg] iv#{snap['id']} feishu {feishu_res}")
                    except Exception as e:
                        feishu_res = f"failed: {e}"
                        logger.error(f"[cancel bg] iv#{snap['id']} feishu exception: {e}")

                # 写回 notes
                try:
                    db2 = SessionLocal()
                    iv = db2.query(Interview).filter(Interview.id == snap["id"]).first()
                    if iv:
                        now2 = datetime.now(tz8).strftime("%m-%d %H:%M")
                        final_note = f"[{now2}] 外部清理完成 (腾讯: {tencent_res}, 飞书: {feishu_res})"
                        iv.notes = f"{iv.notes}\n{final_note}" if iv.notes else final_note
                        db2.commit()
                    db2.close()
                except Exception as e:
                    logger.error(f"[cancel bg] iv#{snap['id']} notes update exception: {e}")

            try:
                _aio.run(_run())
            except Exception as e:
                logger.error(f"[cancel bg] iv#{snap['id']} top-level exception: {e}")

        threading.Thread(target=_bg_cleanup, args=(snapshot,), daemon=True).start()

    return _enrich_interview(service.db, interview)
