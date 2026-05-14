"""简历管理 API 路由"""
import logging
import shutil
import time
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings

from app.database import get_db
from app.modules.auth.deps import get_current_user_id
from app.modules.resume.service import ResumeService
from app.modules.resume.schemas import (
    ResumeCreate,
    ResumeUpdate,
    ResumeResponse,
    ResumeListResponse,
)

router = APIRouter()


def get_resume_service(db: Session = Depends(get_db)) -> ResumeService:
    return ResumeService(db)


@router.delete("/clear-all", status_code=200)
def clear_all_resumes(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """清空当前用户的所有简历 + 候选人 + 槽位 + 面试 + 通知 + PDF 文件 + 匹配结果 + 出箱。
    BUG-062 修复：之前只清 Resume 表，IntakeCandidate/IntakeSlot/intake_outbox 残留导致刷新后列表回填。
    BUG-084 修复：os.remove 加 storage_root 边界校验，防注入路径穿越删系统文件。
    """
    from app.modules.resume.models import Resume
    from app.modules.resume.cascade import purge_resumes_with_deps
    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.im_intake.models import IntakeSlot
    from app.modules.im_intake.outbox_model import IntakeOutbox

    user_resume_ids = [r.id for r in db.query(Resume.id).filter(Resume.user_id == user_id).all()]

    # 收集 candidate 侧 PDF 路径与 ID
    cand_rows = db.query(IntakeCandidate).filter(IntakeCandidate.user_id == user_id).all()
    cand_ids = [c.id for c in cand_rows]
    cand_pdf_paths = [c.pdf_path for c in cand_rows if c.pdf_path]

    # 收集 Resume 侧 PDF 路径
    user_pdf_paths = [r.pdf_path for r in db.query(Resume.pdf_path).filter(
        Resume.user_id == user_id, Resume.pdf_path != None, Resume.pdf_path != ""
    ).all() if r.pdf_path]

    # 清候选人侧（slot/outbox/candidate）
    if cand_ids:
        db.query(IntakeSlot).filter(IntakeSlot.candidate_id.in_(cand_ids)).delete(synchronize_session=False)
        try:
            db.query(IntakeOutbox).filter(IntakeOutbox.candidate_id.in_(cand_ids)).delete(synchronize_session=False)
        except Exception:
            pass
        db.query(IntakeCandidate).filter(IntakeCandidate.user_id == user_id).delete(synchronize_session=False)

    purged = purge_resumes_with_deps(db, user_resume_ids)
    count = purged["resumes"]
    interview_count = purged["interviews"]
    notification_count = purged["notifications"]
    db.commit()

    # 删 PDF 文件，仅限 storage_root 之内
    storage_root = Path(settings.resume_storage_path).resolve()
    for path in set(user_pdf_paths + cand_pdf_paths):
        try:
            p = Path(path).resolve()
            if not str(p).startswith(str(storage_root)):
                continue  # 越界路径不删
            if p.exists():
                p.unlink()
        except OSError:
            pass
    return {
        "deleted_resumes": count,
        "deleted_candidates": len(cand_ids),
        "deleted_interviews": interview_count,
        "deleted_notifications": notification_count,
    }


@router.post("/", response_model=ResumeResponse, status_code=201)
def create_resume(
    data: ResumeCreate,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    resume, is_new = service.create(data, user_id=user_id)
    return resume


@router.post("/batch", status_code=201)
def batch_create_resumes(
    resumes: list[ResumeCreate],
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    if len(resumes) > 100:
        raise HTTPException(status_code=400, detail="单次批量导入不能超过100条")
    created = 0
    duplicates = 0
    for data in resumes:
        resume, is_new = service.create(data, user_id=user_id)
        if is_new:
            created += 1
        else:
            duplicates += 1
    return {"created": created, "duplicates": duplicates, "total": len(resumes)}


@router.post("/upload", response_model=ResumeResponse, status_code=201)
def upload_pdf_resume(
    file: UploadFile = File(...),
    candidate_name: str = Form(""),
    candidate_phone: str = Form(""),
    candidate_email: str = Form(""),
    candidate_education: str = Form(""),
    candidate_work_years: int = Form(0),
    candidate_job: str = Form(""),
    candidate_boss_id: str = Form(""),
    candidate_source: str = Form("boss_zhipin"),
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    """spec 0429 阶段 B: 手动上传统一走 IntakeCandidate → promote → Resume

    步骤:
      1. 读 PDF 字节 + 类型校验
      2. ensure_candidate (boss_id 空时用 sha256 surrogate)
      3. 落盘 PDF + 写 candidate.pdf_path/raw_text/字段
      4. 三 hard slot 兜底填充 (manual_upload sentinel)，使简历库可见
      5. promote_to_resume 出 Resume 锚点 (matching/interview FK 用)
      6. 渲染 candidate 视图返回 (与 GET /api/resumes/ 一致)
    """
    is_pdf = (file.filename or "").lower().endswith(".pdf") or (file.content_type or "").startswith("application/pdf")
    if not is_pdf:
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    # 读完整字节供 sha256 surrogate + 落盘
    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")

    import hashlib
    from datetime import datetime
    from app.modules.im_intake.candidate_model import IntakeCandidate as _IC
    from app.modules.im_intake.models import IntakeSlot as _Slot
    from app.modules.im_intake.templates import HARD_SLOT_KEYS as _HARD
    from app.modules.im_intake.promote import promote_to_resume as _promote

    # surrogate boss_id 防孤儿且同文件天然去重
    surrogate_boss_id = candidate_boss_id or (
        "manual_" + hashlib.sha256(file_bytes).hexdigest()[:16]
    )

    storage_dir = Path(settings.resume_storage_path)
    storage_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = (candidate_name or "未知").replace("/", "_").replace("\\", "_")
    safe_job = (candidate_job or "未知职位").replace("/", "_").replace("\\", "_")
    filename = f"{date_str}_{safe_name}_{safe_job}.pdf"
    file_path = storage_dir / filename

    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # PDF 解析（图片型 PDF 等抽不到文本时报 422 + 删文件）
    from app.modules.resume.pdf_parser import parse_pdf, extract_resume_fields, parse_boss_filename
    raw_text = parse_pdf(str(file_path).replace("\\", "/"))
    if not raw_text:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="PDF 解析失败，无法提取内容")

    pdf_fields = extract_resume_fields(raw_text)
    filename_fields = parse_boss_filename(file.filename or "")

    name = candidate_name or filename_fields.get("name") or pdf_fields.get("name") or "未知"
    phone = candidate_phone or filename_fields.get("phone") or pdf_fields.get("phone") or ""
    email = candidate_email or filename_fields.get("email") or pdf_fields.get("email") or ""
    education = candidate_education or pdf_fields.get("education") or ""

    # ensure_candidate: (user_id, boss_id) 唯一索引天然 dedup
    candidate = (service.db.query(_IC)
                 .filter_by(user_id=user_id, boss_id=surrogate_boss_id).first())
    now = datetime.now(timezone.utc)
    if candidate is None:
        candidate = _IC(
            user_id=user_id,
            boss_id=surrogate_boss_id,
            name=name,
            phone=phone,
            email=email,
            education=education,
            work_years=candidate_work_years or 0,
            job_intention=candidate_job or "",
            skills=pdf_fields.get("skills", "") or "",
            work_experience=pdf_fields.get("work_experience", "") or "",
            source="manual_upload" if not candidate_boss_id else candidate_source,
            pdf_path=str(file_path).replace("\\", "/"),
            raw_text=raw_text,
            intake_status="collecting",
            intake_started_at=now,
        )
        service.db.add(candidate)
        service.db.commit()
        service.db.refresh(candidate)
    else:
        # 同 surrogate 重复上传：刷新 PDF + raw_text，不改 boss_id
        if name and not candidate.name:
            candidate.name = name
        if phone and not candidate.phone:
            candidate.phone = phone
        if email and not candidate.email:
            candidate.email = email
        candidate.pdf_path = str(file_path).replace("\\", "/")
        candidate.raw_text = raw_text

    # 三 hard slot 兜底（手动上传场景认为信息已收齐 → 简历库可见）
    existing_slots = {s.slot_key: s for s in
                      service.db.query(_Slot).filter_by(candidate_id=candidate.id).all()}
    for k in _HARD:
        if k not in existing_slots:
            service.db.add(_Slot(
                candidate_id=candidate.id, slot_key=k, slot_category="hard",
                value="manual_upload", source="manual_upload", ask_count=0,
                answered_at=now,
            ))
        elif not existing_slots[k].value:
            existing_slots[k].value = "manual_upload"
            existing_slots[k].source = "manual_upload"
            existing_slots[k].answered_at = now
    service.db.commit()
    service.db.refresh(candidate)

    # promote 出 Resume 锚点
    try:
        _promote(service.db, candidate, user_id=user_id)
        service.db.commit()
        service.db.refresh(candidate)
    except Exception as e:
        # promote 失败不阻断上传 (candidate 已存)；但 matching/interview 链路要等修复
        logger = logging.getLogger(__name__)
        logger.warning("upload_pdf_resume promote failed: %s", e)

    return _target_to_response_dict(candidate, service.db)


@router.get("/settings/storage-path")
def get_storage_path():
    """获取当前 PDF 存储路径"""
    return {"path": settings.resume_storage_path}


class _CheckBossIdsIn(BaseModel):
    boss_ids: list[str] = Field(max_length=1000)


@router.post("/check-boss-ids")
def check_boss_ids(
    body: _CheckBossIdsIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """检查给定的 boss_ids 中哪些已在当前用户的简历库中"""
    if not body.boss_ids:
        return {"existing": []}
    from app.modules.resume.models import Resume
    rows = (
        db.query(Resume.boss_id)
        .filter(
            Resume.boss_id.in_(body.boss_ids),
            Resume.user_id == user_id,
            Resume.boss_id != "",
        )
        .all()
    )
    return {"existing": [r.boss_id for r in rows]}


@router.get("/", response_model=ResumeListResponse)
def list_resumes(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: str | None = None,
    # BUG-082 修复：keyword 限长 64 防 LIKE 全表扫 DoS
    keyword: str | None = Query(None, max_length=64),
    source: str | None = None,
    intake_status: str | None = None,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    """简历库: 四项齐全(三 hard slot + PDF) 的 IntakeCandidate"""
    from app.modules.resume.intake_view_service import list_resume_library
    return list_resume_library(
        service.db,
        user_id=user_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        source=source,
        status=status,
    )


@router.get("/ai-parse-status")
def ai_parse_status():
    """查询 AI 解析进度"""
    from app.modules.resume._ai_parse_worker import get_parse_status
    return get_parse_status()


def _target_to_response_dict(target, db: Session | None = None):
    """Resume 或 IntakeCandidate -> ResumeResponse 兼容 dict。"""
    from app.modules.im_intake.candidate_model import IntakeCandidate
    if isinstance(target, IntakeCandidate):
        from app.modules.resume.intake_view_service import candidate_to_resume_dict
        return candidate_to_resume_dict(target, db)
    return {
        "id": target.id,
        "name": target.name or "",
        "phone": target.phone or "",
        "email": target.email or "",
        "education": target.education or "",
        "bachelor_school": target.bachelor_school or "",
        "master_school": target.master_school or "",
        "phd_school": target.phd_school or "",
        "qr_code_path": target.qr_code_path or "",
        "work_years": target.work_years or 0,
        "expected_salary_min": target.expected_salary_min or 0.0,
        "expected_salary_max": target.expected_salary_max or 0.0,
        "job_intention": target.job_intention or "",
        "skills": target.skills or "",
        "work_experience": target.work_experience or "",
        "project_experience": target.project_experience or "",
        "self_evaluation": target.self_evaluation or "",
        "source": target.source or "",
        "raw_text": target.raw_text or "",
        "pdf_path": target.pdf_path or "",
        "status": target.status or "passed",
        "ai_parsed": target.ai_parsed or "no",
        "ai_score": target.ai_score,
        "ai_summary": target.ai_summary or "",
        "reject_reason": getattr(target, "reject_reason", "") or "",
        "seniority": target.seniority or "",
        "intake_status": getattr(target, "intake_status", "") or "",
        "boss_id": target.boss_id or "",
        "school_tier": getattr(target, "school_tier", "") or "",
        "created_at": target.created_at,
        "updated_at": target.updated_at,
    }


# spec 0429 阶段 A 后 IntakeCandidate 也有 status/reject_reason 列，写入不再被 skip。
# 但 PATCH 这两个字段时 candidate 仍需 promote 出 Resume 作 matching/interview FK 锚点。
_PROMOTE_TRIGGER_FIELDS = {"status", "reject_reason"}


def _resolve_owned_or_404(service: ResumeService, resume_id: int, user_id: int):
    """统一鉴权 + 解析。BUG-056 修复：他人资源与不存在均返 404，不暴露存在性。"""
    target = _resolve_resume_target(service, resume_id)
    if not target or target.user_id != user_id:
        raise HTTPException(status_code=404, detail="简历不存在")
    return target


@router.get("/{resume_id}", response_model=ResumeResponse)
def get_resume(
    resume_id: int,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    target = _resolve_owned_or_404(service, resume_id, user_id)
    return _target_to_response_dict(target, service.db)


@router.patch("/{resume_id}", response_model=ResumeResponse)
def update_resume(
    resume_id: int,
    data: ResumeUpdate,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    target = _resolve_owned_or_404(service, resume_id, user_id)

    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.resume.models import Resume as _R
    is_candidate = isinstance(target, IntakeCandidate)

    update_data = data.model_dump(exclude_none=True)
    has_promote_trigger = any(k in _PROMOTE_TRIGGER_FIELDS for k in update_data)

    # BUG-057 修复 + spec 0429: candidate 入口收到 status/reject_reason 但
    # promoted_resume_id 缺失时自动 promote，确保 matching/interview FK 有 Resume 锚点。
    if is_candidate and has_promote_trigger and not target.promoted_resume_id:
        try:
            from app.modules.im_intake.promote import promote_to_resume
            promote_to_resume(service.db, target, user_id=user_id)
            service.db.commit()
            service.db.refresh(target)
        except Exception as _e:
            raise HTTPException(status_code=500, detail="无法同步简历状态，请稍后重试")

    for key, value in update_data.items():
        # spec 0429 阶段 A: candidate 也有 status/reject_reason，不再 skip
        if hasattr(target, key):
            setattr(target, key, value)

    # candidate 入口：同步到 promoted Resume（matching 读 Resume 表）
    if is_candidate and target.promoted_resume_id:
        r = service.db.query(_R).filter_by(id=target.promoted_resume_id).first()
        # BUG-058 修复：跨用户 FK 腐化时拒绝写入，防止越权改他人简历
        if r is not None and r.user_id == user_id:
            for key, value in update_data.items():
                if hasattr(r, key):
                    setattr(r, key, value)

    service.db.commit()
    service.db.refresh(target)
    return _target_to_response_dict(target, service.db)


@router.delete("/{resume_id}", status_code=204)
def delete_resume(
    resume_id: int,
    db: Session = Depends(get_db),
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    target = _resolve_owned_or_404(service, resume_id, user_id)

    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.im_intake.models import IntakeSlot
    from app.modules.im_intake.outbox_model import IntakeOutbox
    from app.modules.resume.models import Resume as _R

    if isinstance(target, IntakeCandidate):
        # BUG-058 修复：删 promoted Resume 前校验 user 归属，防止跨用户 FK 腐化误删他人
        if target.promoted_resume_id:
            r_owner = db.query(_R.user_id).filter_by(id=target.promoted_resume_id).scalar()
            if r_owner == user_id:
                service.delete(target.promoted_resume_id)
        # BUG-063 修复：候选人入口同时清 outbox + slots + candidate，避免出箱处理器悬挂
        try:
            db.query(IntakeOutbox).filter_by(candidate_id=target.id).delete(synchronize_session=False)
        except Exception:
            pass
        db.query(IntakeSlot).filter_by(candidate_id=target.id).delete(synchronize_session=False)
        db.delete(target)
        db.commit()
        return

    # Resume 侧入口（legacy id）
    candidate = db.query(IntakeCandidate).filter_by(promoted_resume_id=resume_id, user_id=user_id).first()
    service.delete(resume_id)
    if candidate:
        try:
            db.query(IntakeOutbox).filter_by(candidate_id=candidate.id).delete(synchronize_session=False)
        except Exception:
            pass
        db.query(IntakeSlot).filter_by(candidate_id=candidate.id).delete(synchronize_session=False)
        db.delete(candidate)
        db.commit()


@router.post("/ai-parse-all")
async def ai_parse_all_resumes(user_id: int = Depends(get_current_user_id)):
    """启动后台线程，逐个 AI 解析当前用户所有未解析的简历"""
    from app.config import settings
    if not settings.ai_enabled:
        raise HTTPException(status_code=400, detail="AI 功能未开启，请在设置中启用")

    from app.adapters.ai_provider import AIProvider
    ai = AIProvider()
    if not ai.is_configured():
        raise HTTPException(status_code=400, detail="AI 未配置，请在 .env 中设置 API Key")

    from app.modules.resume._ai_parse_worker import start_ai_parse_worker, _status
    if _status.get("running"):
        return {"status": "already_running", "message": "AI解析任务已在运行中，请勿重复启动"}

    import threading
    thread = threading.Thread(target=start_ai_parse_worker, args=(user_id,), daemon=True)
    thread.start()

    return {"status": "started", "message": "AI 解析任务已在后台启动"}


# _apply_parsed_fields 已迁入 _ai_parse_core (单条端点与批量 worker 的单一权威实现)。
# 再导出供历史 import 路径 (tests/chaos/test_chaos_round3.py) 继续可用。
from app.modules.resume._ai_parse_core import _apply_parsed_fields  # noqa: E402,F401


@router.post("/{resume_id}/ai-parse", response_model=ResumeResponse)
async def ai_parse_single(
    resume_id: int,
    background_tasks: BackgroundTasks,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    """AI 解析单条简历。支持 Resume.id 与 IntakeCandidate.id 两种入参。

    解析核心 (vision/text 检测、字段落库、candidate→Resume promote、F2 锚点)
    走 _ai_parse_core.ai_parse_target，与批量 worker 同一实现。本端点只负责
    HTTP 策略层: 鉴权、前置校验、错误码映射、F2 触发派发。
    """
    import os

    from app.config import settings as cfg
    if not cfg.ai_enabled:
        raise HTTPException(status_code=400, detail="AI 功能未开启")

    target = _resolve_owned_or_404(service, resume_id, user_id)

    from app.adapters.ai_provider import AIProvider
    from app.modules.resume._ai_parse_core import ai_parse_target

    ai = AIProvider()
    if not ai.is_configured():
        raise HTTPException(status_code=400, detail="AI 未配置")

    # BUG-085 修复：无 PDF 也无 raw_text 时返 400（用户无可解析输入），而非 500 通用错
    has_pdf = bool(target.pdf_path and os.path.exists(target.pdf_path))
    has_text = bool(target.raw_text)
    if not has_pdf and not has_text:
        raise HTTPException(status_code=400, detail="没有 PDF 或聊天文本可解析")

    # ai_parse_target 内部 commit，失败时已标 ai_parsed='failed' (BUG-061 双入口一致)。
    status, score_resume_id = await ai_parse_target(target, ai, service.db)
    if status != "yes":
        raise HTTPException(status_code=500, detail="AI 解析失败")

    service.db.refresh(target)

    # F2 T1 trigger: 用 Resume.id 评分（matching 表以 Resume 为外键基础）
    if score_resume_id:
        try:
            async def _t1_bg():
                from app.database import SessionLocal
                from app.modules.matching.triggers import on_resume_parsed
                _db = SessionLocal()
                try:
                    await on_resume_parsed(_db, score_resume_id)
                finally:
                    _db.close()
            background_tasks.add_task(_t1_bg)
        except Exception as _t1_err:
            import logging as _log
            _log.getLogger(__name__).warning(f"F2 T1 trigger failed (non-fatal): {_t1_err}")

    return _target_to_response_dict(target, service.db)


def _resolve_resume_target(service: ResumeService, target_id: int):
    """简历库列表 (intake_view_service) 现以 IntakeCandidate.id 为行键暴露给前端，
    /qr 与 /pdf 端点必须先按 candidate 解析；未命中再回落到旧 Resume 表（兼容历史 id）。"""
    from app.modules.im_intake.candidate_model import IntakeCandidate
    cand = service.db.query(IntakeCandidate).filter(IntakeCandidate.id == target_id).first()
    if cand:
        return cand
    return service.get_by_id(target_id)


@router.get("/{resume_id}/qr")
def get_resume_qr(
    resume_id: int,
    regen: int = 0,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    """返回简历左上角裁剪出的二维码图片。

    自愈机制：如果磁盘上没有缓存的 QR 图（被误删、从未生成过等），
    尝试从 PDF 现场裁剪一张回来。成功则写回 DB、返回图片。

    `?regen=1`：强制忽略缓存，立即重跑算法（用于算法升级后让旧缓存重生成）。
    """
    target = _resolve_owned_or_404(service, resume_id, user_id)

    # 先尝试用 DB 记录的路径；不存在或 regen=1 则从 PDF 即时生成
    qr_path = target.qr_code_path
    need_regen = regen == 1 or (not qr_path) or (not Path(qr_path).exists())
    if need_regen and target.pdf_path and Path(target.pdf_path).exists():
        from app.modules.resume.pdf_parser import extract_boss_qr
        qr_out = Path("data/qrcodes") / f"{target.id}.png"
        # 强制重生成时先删旧文件
        if regen == 1 and qr_out.exists():
            try:
                qr_out.unlink()
            except Exception:
                pass
        qr_path = extract_boss_qr(target.pdf_path, str(qr_out))
        if qr_path:
            target.qr_code_path = qr_path
            service.db.commit()

    if not qr_path or not Path(qr_path).exists():
        raise HTTPException(status_code=404, detail="二维码不存在")

    # 关键：禁掉浏览器缓存。一旦磁盘上的 QR 被重新生成（文件内容变了但 URL 没变），
    # 浏览器必须拿到最新版本，而不能从本地缓存里掏一份过期的（甚至缓存的 404）
    return FileResponse(
        str(qr_path),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/{resume_id}/pdf")
def get_resume_pdf(
    resume_id: int,
    service: ResumeService = Depends(get_resume_service),
    user_id: int = Depends(get_current_user_id),
):
    """下载/查看候选人的 PDF 简历"""
    target = _resolve_owned_or_404(service, resume_id, user_id)
    if not target.pdf_path:
        raise HTTPException(status_code=404, detail="该候选人没有 PDF 简历")

    import os as _os
    pdf_file = Path(target.pdf_path).resolve()
    storage_root = Path(settings.resume_storage_path).resolve()
    # BUG-078 修复：Windows NTFS 不区分大小写，比较前统一小写
    pf, sr = str(pdf_file), str(storage_root)
    if _os.name == "nt":
        pf, sr = pf.lower(), sr.lower()
    if not pf.startswith(sr):
        raise HTTPException(status_code=403, detail="非法文件路径")
    if not pdf_file.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")

    return FileResponse(
        str(pdf_file),
        media_type="application/pdf",
        filename=f"{target.name}_简历.pdf",
        content_disposition_type="inline",  # 浏览器中直接打开，不下载
    )
