"""候选人到岗位的二段分类: exact match 优先, LLM 兜底."""
from __future__ import annotations
import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.ai_provider import AIProvider
from app.config import settings
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)


def _active_approved_jobs(db: Session, user_id: int) -> list[Job]:
    return (
        db.query(Job)
        .filter(
            Job.user_id == user_id,
            Job.is_active == True,
            Job.competency_model_status == "approved",
        )
        .order_by(Job.id.asc())
        .all()
    )


def _exact_match(intent: str, jobs: list[Job]) -> Optional[Job]:
    intent = (intent or "").strip()
    if not intent:
        return None
    for j in jobs:
        if (j.title or "").strip() == intent:
            return j
    return None


def _build_llm_prompt(c: IntakeCandidate, jobs: list[Job]) -> str:
    job_lines = []
    for j in jobs:
        cm = j.competency_model or {}
        hard = cm.get("hard_skills") or []
        skill_names = ", ".join(s.get("name", "") for s in hard[:10] if s.get("name"))
        job_lines.append(f"- id={j.id} | 标题={j.title} | 核心技能={skill_names or '未定义'}")
    jobs_block = "\n".join(job_lines)

    return f"""你是 HR 助手。请把以下候选人分类到最匹配的岗位 id, 或返 null 表示无明显匹配。

候选人:
- 姓名: {c.name or ''}
- 求职意向: {c.job_intention or ''}
- 技能: {(c.skills or '')[:300]}
- 工作经验摘要: {(c.work_experience or '')[:200]}
- 学历: {c.education or ''}

候选岗位:
{jobs_block}

只返 JSON, 不要任何额外说明:
{{"job_id": <候选岗位 id 之一, 或 null>, "confidence": "high"|"medium"|"low", "reason": "<1 句话>"}}"""


async def _llm_classify(c: IntakeCandidate, jobs: list[Job]) -> tuple[Optional[int], str]:
    import httpx
    model = settings.ai_model_intake or settings.ai_model
    provider = AIProvider(model=model)
    if not provider.is_configured():
        return None, "llm_not_configured"

    prompt = _build_llm_prompt(c, jobs)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": provider.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        logger.warning("classifier llm invalid json: %s", e)
        return None, "llm_invalid_json"
    except Exception as e:
        logger.warning("classifier llm error: %s", e)
        return None, "llm_error"

    raw_jid = data.get("job_id")
    if raw_jid is None:
        return None, f"llm_no_match: {data.get('reason', '')}"

    try:
        jid = int(raw_jid)
    except (TypeError, ValueError):
        return None, "llm_invalid_job_id"

    if jid not in {j.id for j in jobs}:
        logger.warning("classifier llm returned cross-user job_id=%s, rejecting", jid)
        return None, "llm_cross_user_rejected"

    return jid, f"llm_{data.get('confidence', 'unknown')}: {data.get('reason', '')}"


async def classify_candidate_to_job(
    db: Session, candidate: IntakeCandidate, *, user_id: int
) -> tuple[Optional[int], str]:
    """返 (job_id_or_None, reason_code).

    流程:
      1. 取 user 名下 active + approved 岗位
      2. exact title match — 命中直接写
      3. LLM 兜底 — 失败/异常不阻塞, 返 (None, error_code)

    注: 调用方负责 db.commit(). 本函数只 set candidate.job_id 不 commit.
    """
    jobs = _active_approved_jobs(db, user_id)
    if not jobs:
        return None, "no_active_jobs"

    exact = _exact_match(candidate.job_intention or "", jobs)
    if exact is not None:
        candidate.job_id = exact.id
        return exact.id, "exact_match"

    jid, reason = await _llm_classify(candidate, jobs)
    if jid is not None:
        candidate.job_id = jid
    return jid, reason
