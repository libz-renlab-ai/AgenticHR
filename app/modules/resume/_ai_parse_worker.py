"""AI 简历解析后台工作线程

在独立线程中逐个解析未被 AI 解析的简历，支持中断恢复和运行期间新增。
"""
import asyncio
import logging
import os
import re

from app.database import SessionLocal
from app.modules.resume.models import Resume

logger = logging.getLogger(__name__)


def _coerce_work_years(val) -> int:
    """BUG-143: LLM 偶尔返 'work_years': '5' 字符串而非数字, 这里统一容错。

    支持: int / float / 数字 string / 含数字的中文短语 ("5 年" / "五年" 不解析,
    回 0 让上游兜底). 不可解析时返 0。
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
        # 直接 int 转换 (覆盖 "5" / "5.0" 等)
        try:
            return int(float(s))
        except (TypeError, ValueError):
            pass
        # regex 抽数字 (覆盖 "5 年" / "5年工作经验")
        m = re.search(r"\d+", s)
        if m:
            try:
                return int(m.group(0))
            except (TypeError, ValueError):
                return 0
    return 0

# 全局状态
_status = {
    "running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current": "",
}


def get_parse_status() -> dict:
    return {**_status}


def start_ai_parse_worker(user_id: int = 0):
    """在后台线程中运行 AI 解析（同步入口，内部用 asyncio）

    Args:
        user_id: 限定解析该用户的简历，0 表示不限制（向后兼容）
    """
    if _status["running"]:
        logger.info("AI 解析任务已在运行中，跳过")
        return

    _status["running"] = True
    _status["completed"] = 0
    _status["failed"] = 0
    _status["current"] = ""

    try:
        asyncio.run(_do_parse_all(user_id=user_id))
    except Exception as e:
        logger.error(f"AI 解析任务异常退出: {e}")
    finally:
        _status["running"] = False
        _status["current"] = ""
        logger.info(f"AI 解析任务结束: 完成 {_status['completed']}, 失败 {_status['failed']}")


def maybe_start_worker_thread():
    """如果 AI 已配置且 worker 没在跑，就在后台线程里启动它。幂等。"""
    if _status["running"]:
        return
    try:
        from app.config import settings
        if not settings.ai_enabled:
            return
        from app.adapters.ai_provider import AIProvider
        if not AIProvider().is_configured():
            return
    except Exception:
        return

    import threading
    thread = threading.Thread(target=start_ai_parse_worker, daemon=True)
    thread.start()
    logger.info("AI 解析后台任务已自动启动")


async def _do_parse_all(user_id: int = 0):
    from app.adapters.ai_provider import AIProvider
    from app.modules.resume.pdf_parser import ai_parse_resume, ai_parse_resume_vision, is_image_pdf

    ai = AIProvider()
    db = SessionLocal()

    def _query_pending():
        # 只看 'no'，不回头重试 'failed'（避免无限循环）；手动重试用单条接口
        q = (
            db.query(Resume)
            .filter(Resume.ai_parsed == "no")
            .filter((Resume.raw_text != "") | (Resume.pdf_path != ""))
        )
        if user_id:
            q = q.filter(Resume.user_id == user_id)
        return q.all()

    def _s(v):
        return str(v) if isinstance(v, (dict, list)) else (v or "")

    try:
        # 启动时先把卡在 'parsing' 状态的简历重置回 'no'，
        # 避免 worker 上次异常退出（Ctrl+C/OOM/重载）导致简历永久卡住。
        # BUG-146: 不再按 user_id 过滤 — 跨用户的 stale 都要清, 否则 user A 的 worker
        # 崩溃后必须等 server 重启才能恢复; 由 user B 触发的 worker 也应顺便清掉 A 的 stale。
        # 在 _status['running'] guard 之后才会到这里, 所以不会与活跃 worker 冲突。
        stale_q = db.query(Resume).filter(Resume.ai_parsed == "parsing")
        stale = stale_q.all()
        if stale:
            logger.info(f"发现 {len(stale)} 份卡在 parsing 的简历，重置为 no 重新排队")
            for r in stale:
                r.ai_parsed = "no"
            db.commit()

        round_idx = 0
        while True:
            pending = _query_pending()
            if not pending:
                break
            round_idx += 1
            if round_idx == 1:
                _status["total"] = len(pending)
                logger.info(f"AI 解析任务启动: {len(pending)} 份待解析")
            else:
                _status["total"] += len(pending)
                logger.info(f"第 {round_idx} 轮: 发现 {len(pending)} 份新加入的待解析简历")

            for i, resume in enumerate(pending):
                _status["current"] = resume.name
                logger.info(f"[轮{round_idx} {i+1}/{len(pending)}] 正在解析: {resume.name}")

                resume.ai_parsed = "parsing"
                db.commit()

                try:
                    use_vision = False
                    if resume.pdf_path and os.path.exists(resume.pdf_path):
                        if not resume.raw_text or len(resume.raw_text.strip()) < 50:
                            use_vision = True
                            logger.info(f"  图片版PDF，使用视觉模型")
                        elif is_image_pdf(resume.pdf_path):
                            use_vision = True
                            logger.info(f"  检测为图片版PDF，使用视觉模型")

                    if use_vision:
                        parsed = await ai_parse_resume_vision(resume.pdf_path, ai)
                        if parsed:
                            resume.raw_text = f"[AI视觉解析] 姓名:{parsed.get('name','')} 技能:{parsed.get('skills','')} 经历:{parsed.get('work_experience','')}"
                    else:
                        parsed = await ai_parse_resume(resume.raw_text, ai)

                    if not parsed:
                        resume.ai_parsed = "failed"
                        _status["failed"] += 1
                        db.commit()
                        continue

                    if parsed.get("name") and resume.name == "未知":
                        resume.name = _s(parsed["name"])
                    if parsed.get("phone") and not resume.phone:
                        resume.phone = _s(parsed["phone"])
                    if parsed.get("email") and not resume.email:
                        resume.email = _s(parsed["email"])
                    if parsed.get("education") and not resume.education:
                        # BUG-126: LLM 偶尔输出 "研究生|硕士" / "本科/硕士" 等非规范值,
                        # 在落库前规范化为单值 (大专/本科/硕士/博士).
                        # BUG-132: normalize 失败时不再回填原始 LLM 值 ("中专"/"高中"等),
                        # 落库 "" 让下游用 job.education_min 兜底, 而不是塞入无法识别值
                        # 让 EDUCATION_LEVELS lookup miss → 0 静默筛掉候选。
                        from app.modules.resume.pdf_parser import normalize_education
                        norm = normalize_education(_s(parsed["education"]))
                        resume.education = norm  # norm = "" 时主动清空, 等待 HR 手动补
                    if parsed.get("bachelor_school") and not resume.bachelor_school:
                        resume.bachelor_school = _s(parsed["bachelor_school"])
                    if parsed.get("master_school") and not resume.master_school:
                        resume.master_school = _s(parsed["master_school"])
                    if parsed.get("phd_school") and not resume.phd_school:
                        resume.phd_school = _s(parsed["phd_school"])
                    if parsed.get("work_years") and not resume.work_years:
                        # BUG-143: LLM 偶尔返字符串 "5" / "5 年", 之前 isinstance 判 (int,float)
                        # 不命中直接 0, 5 年经验候选被错归应届。改 _coerce_work_years 容错。
                        resume.work_years = _coerce_work_years(parsed["work_years"])
                    if parsed.get("skills"):
                        resume.skills = _s(parsed["skills"])
                    if parsed.get("work_experience"):
                        resume.work_experience = _s(parsed["work_experience"])
                    if parsed.get("project_experience"):
                        resume.project_experience = _s(parsed["project_experience"])
                    if parsed.get("self_evaluation"):
                        resume.self_evaluation = _s(parsed["self_evaluation"])
                    if parsed.get("job_intention") and not resume.job_intention:
                        resume.job_intention = _s(parsed["job_intention"])
                    # BUG-144: 加 `and not resume.seniority` guard 与其他字段对齐,
                    # 避免重新 parse 覆盖 HR 在 PATCH /api/resumes/{id} 上做的人工修正。
                    if parsed.get("seniority") and not resume.seniority:
                        resume.seniority = (parsed.get("seniority") or "").strip()

                    resume.ai_parsed = "yes"
                    _status["completed"] += 1
                    db.commit()
                    logger.info(f"  解析成功: {resume.name}")

                    # F2 T1 trigger: score against all active+approved jobs
                    # Use a fresh session so the trigger's DB work is independent
                    # of the worker's long-lived session.
                    try:
                        from app.modules.matching.triggers import on_resume_parsed
                        _t1_db = SessionLocal()
                        try:
                            await on_resume_parsed(_t1_db, resume.id)
                        finally:
                            _t1_db.close()
                    except Exception as _t1_err:
                        logger.warning(f"F2 T1 trigger failed (non-fatal): {_t1_err}")

                except Exception as e:
                    logger.error(f"  解析失败 [{resume.name}]: {e}")
                    resume.ai_parsed = "failed"
                    _status["failed"] += 1
                    db.commit()

    finally:
        db.close()
