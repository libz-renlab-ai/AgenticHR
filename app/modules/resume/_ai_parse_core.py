"""统一的简历 AI 解析核心 —— 单条端点与批量 worker 共用的单一实现。

根因背景:
  /resumes 简历库列表来自 IntakeCandidate 表 (intake_view_service.list_resume_library),
  但旧的批量 worker `_do_parse_all` 只查 Resume 表 → 点"手动启动内容解析"对页面零效果。
  更深一层: 单条端点 (router.ai_parse_single) 与批量 worker 各自维护了一份会漂移的
  parse 实现, 两边各缺对方的 bugfix。此模块把"解析一个 target"收敛成单一权威实现:
    - query_pending_targets(): 批量待办查询, 覆盖 IntakeCandidate (主) + 孤儿 Resume (兜底)
    - ai_parse_target():       解析一个 target, 同时认 Resume 与 IntakeCandidate
    - _apply_parsed_fields():  字段落库, 合 BUG-060/126/143 全部历史修复
"""
from __future__ import annotations

import logging
import os
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.resume import pdf_parser
from app.modules.resume.models import Resume

# 经由模块属性访问 (pdf_parser.ai_parse_resume) 而非绑定导入 —— 测试 monkeypatch
# 的是 app.modules.resume.pdf_parser.* , 绑定导入会拿到 patch 前的旧引用。

logger = logging.getLogger(__name__)


def _coerce_work_years(val) -> int:
    """BUG-143: LLM 偶尔返 'work_years': '5' 字符串而非数字, 或 "5 年" 含单位短语。

    支持 int / float / 数字 string / 含数字短语; 不可解析时返 0 让上游兜底。
    bool 是 int 子类, 显式拒绝。
    """
    if isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        try:
            return int(val)
        except (TypeError, ValueError, OverflowError):
            return 0
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return 0
        try:
            return int(float(s))
        except (TypeError, ValueError):
            pass
        m = re.search(r"\d+", s)
        if m:
            try:
                return int(m.group(0))
            except (TypeError, ValueError):
                return 0
    return 0


def _s(v) -> str:
    """BUG-060: LLM 偶尔把字段返成 dict/list, 统一 str() 兜底。"""
    return str(v) if isinstance(v, (dict, list)) else (v or "")


def _apply_parsed_fields(target, parsed: dict) -> None:
    """把 AI 解析结果写到 target (Resume 或 IntakeCandidate)。两种 target 字段同名。

    合并策略 (与历史 router._apply_parsed_fields 一致, 另补 worker 侧的 126/143 修复):
      - name/phone/email/job_intention: 仅在原值空/"未知"时填, 不覆盖人工编辑
      - education: BUG-126 规范化为单值 (大专/本科/硕士/博士); 不可识别落 "" 让下游兜底
      - work_years: BUG-143 容错字符串 / 含单位短语
      - 其余结构化字段: 解析值非空即写 (首次解析时原值本就为空)
      - BUG-060: dict/list 一律 _s() 兜底
    """
    if parsed.get("name") and (not target.name or target.name == "未知"):
        target.name = _s(parsed["name"])
    if parsed.get("phone") and not target.phone:
        target.phone = _s(parsed["phone"])
    if parsed.get("email") and not target.email:
        target.email = _s(parsed["email"])
    if parsed.get("education"):
        # BUG-126/132: 规范化到单值; normalize 失败返 "" 主动清空, 不落 raw LLM 值
        target.education = pdf_parser.normalize_education(_s(parsed["education"]))
    if parsed.get("bachelor_school"):
        target.bachelor_school = _s(parsed["bachelor_school"])
    if parsed.get("master_school"):
        target.master_school = _s(parsed["master_school"])
    if parsed.get("phd_school"):
        target.phd_school = _s(parsed["phd_school"])
    if parsed.get("work_years"):
        target.work_years = _coerce_work_years(parsed["work_years"])
    if parsed.get("skills"):
        target.skills = _s(parsed["skills"])
    if parsed.get("work_experience"):
        target.work_experience = _s(parsed["work_experience"])
    if parsed.get("project_experience"):
        target.project_experience = _s(parsed["project_experience"])
    if parsed.get("self_evaluation"):
        target.self_evaluation = _s(parsed["self_evaluation"])
    if parsed.get("job_intention") and not target.job_intention:
        target.job_intention = _s(parsed["job_intention"])
    target.seniority = _s(parsed.get("seniority")).strip()
    target.ai_parsed = "yes"


def query_pending_targets(db: Session, user_id: int = 0) -> list:
    """批量解析待办: ai_parsed='no' 且有可解析输入的 target。

    主集: IntakeCandidate —— 简历库页面 (/resumes) 的真实数据源。
    兜底: 无 owning IntakeCandidate 的遗留 Resume 行 (intake_candidate_id 为空)。
          已 promote 的 Resume 不单独入列 —— 解析其 owning candidate 时会同步过去,
          单列会导致同一人被解析两次。

    user_id 真值 → 仅该用户 (手动触发); user_id=0 → 全部用户 (启动自动续跑)。
    只看 'no', 不回头重试 'failed' (避免无限循环); 手动重试走单条端点。
    """
    cand_q = (
        db.query(IntakeCandidate)
        .filter(IntakeCandidate.ai_parsed == "no")
        .filter(
            or_(
                (IntakeCandidate.pdf_path.isnot(None)) & (IntakeCandidate.pdf_path != ""),
                (IntakeCandidate.raw_text.isnot(None)) & (IntakeCandidate.raw_text != ""),
            )
        )
    )
    res_q = (
        db.query(Resume)
        .filter(Resume.ai_parsed == "no")
        .filter(Resume.intake_candidate_id.is_(None))
        .filter(
            or_(
                (Resume.raw_text.isnot(None)) & (Resume.raw_text != ""),
                (Resume.pdf_path.isnot(None)) & (Resume.pdf_path != ""),
            )
        )
    )
    if user_id:
        cand_q = cand_q.filter(IntakeCandidate.user_id == user_id)
        res_q = res_q.filter(Resume.user_id == user_id)
    return list(cand_q.all()) + list(res_q.all())


def reset_stale_parsing(db: Session) -> int:
    """把卡在 'parsing' 的 target 重置回 'no' 重新排队。

    worker 上次异常退出 (Ctrl+C/OOM/重载) 会留下永久卡 'parsing' 的行 ——
    query_pending_targets 只看 'no' 拉不回来。两张表都要清。
    BUG-146: 不按 user_id 过滤 —— 跨用户 stale 都清, 由任意 worker 启动时顺带恢复。
    返回重置的行数。
    """
    count = 0
    for model in (IntakeCandidate, Resume):
        stale = db.query(model).filter(model.ai_parsed == "parsing").all()
        for row in stale:
            row.ai_parsed = "no"
            count += 1
    if count:
        db.commit()
        logger.info(f"重置 {count} 份卡在 parsing 的简历为 no 重新排队")
    return count


async def ai_parse_target(target, ai, db: Session) -> tuple[str, int | None]:
    """解析一个 target (Resume 或 IntakeCandidate) —— 单条端点与批量 worker 共用。

    流程: vision/text 检测 → 调 LLM → _apply_parsed_fields → 若是 candidate 则
    promote 出 Resume 并同步字段 → commit。

    权属取 target.user_id 自身 —— 单条端点已 _resolve_owned_or_404 保证 target 归
    调用者; 批量 worker 各 target 自带真实 owner。无需调用方再传 user_id。

    Returns (status, score_resume_id):
      status:          'yes' | 'failed'
      score_resume_id: 解析成功时供 F2 T1 触发用的 Resume.id (candidate 走 promoted
                       Resume.id; Resume 入口走自身 id; 失败或无法落库时 None)

    内部 commit, 不抛异常 (批量 worker 要继续下一条) —— 调用方按 status 决策。
    F2 T1 触发交给调用方 (单条端点走 BackgroundTasks, worker 走 inline)。
    """
    is_candidate = isinstance(target, IntakeCandidate)
    name = getattr(target, "name", "?")
    owner_id = getattr(target, "user_id", 0) or 0

    has_pdf = bool(target.pdf_path and os.path.exists(target.pdf_path))
    has_text = bool(target.raw_text)
    if not has_pdf and not has_text:
        target.ai_parsed = "failed"
        db.commit()
        return "failed", None

    try:
        use_vision = False
        if has_pdf:
            if (
                not target.raw_text
                or len(target.raw_text.strip()) < 50
                or pdf_parser.is_image_pdf(target.pdf_path)
            ):
                use_vision = True

        if use_vision:
            parsed = await pdf_parser.ai_parse_resume_vision(target.pdf_path, ai)
            if parsed:
                target.raw_text = (
                    f"[AI视觉解析] 姓名:{parsed.get('name', '')} "
                    f"技能:{parsed.get('skills', '')} 经历:{parsed.get('work_experience', '')}"
                )
        else:
            parsed = await pdf_parser.ai_parse_resume(target.raw_text, ai)

        if not parsed:
            db.rollback()
            target.ai_parsed = "failed"
            db.commit()
            return "failed", None

        _apply_parsed_fields(target, parsed)

        # candidate 入口: 同步到 promoted Resume (matching 以 Resume 为 FK 锚点);
        # 未 promote 则强制 promote, 否则会出现 candidate.ai_parsed=yes 但 Resume 缺失。
        promoted_resume = None
        if is_candidate:
            if not target.promoted_resume_id:
                from app.modules.im_intake.promote import promote_to_resume

                promoted_resume = promote_to_resume(db, target, user_id=owner_id)
            else:
                promoted_resume = (
                    db.query(Resume).filter_by(id=target.promoted_resume_id).first()
                )
                # BUG-058: 跨用户 FK 腐化时不写他人 Resume
                if promoted_resume is not None and promoted_resume.user_id != owner_id:
                    promoted_resume = None
            if promoted_resume is not None:
                _apply_parsed_fields(promoted_resume, parsed)
                if use_vision and target.raw_text:
                    promoted_resume.raw_text = target.raw_text

        db.commit()

        if is_candidate:
            score_resume_id = promoted_resume.id if promoted_resume is not None else None
        else:
            score_resume_id = target.id
        logger.info(f"AI 解析成功: {name}")
        return "yes", score_resume_id

    except Exception as e:
        logger.error(f"AI 解析失败 [{name}]: {e}", exc_info=True)
        db.rollback()
        try:
            target.ai_parsed = "failed"
            db.commit()
        except Exception:
            db.rollback()
        return "failed", None
