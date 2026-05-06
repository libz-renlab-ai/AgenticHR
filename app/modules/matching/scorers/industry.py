"""行业 scorer — 关键词包含 + 向量相似度 fallback."""
import logging
from typing import Any

from app.config import settings
from app.core.vector.service import cosine_similarity

logger = logging.getLogger(__name__)

_SIM_THRESHOLD = getattr(settings, "matching_industry_sim", 0.70)


def _vector_match(industry: str, work_experience: str, db_session: Any = None) -> bool:
    """行业名 vs 工作经历文本前 500 字的 bge-m3 相似度 >= 阈值. db_session=None → False."""
    if not db_session or not industry or not work_experience:
        return False
    try:
        from sqlalchemy import text
        row_ind = db_session.execute(
            text("SELECT embedding FROM skills WHERE canonical_name = :n LIMIT 1"),
            {"n": industry},
        ).fetchone()
        if not row_ind or not row_ind[0]:
            return False
        # V1: work_experience 没有预存 embedding, 实时 embed 需调 LLM API；
        # 留 hook 在此函数签名里，V2 再接通 core/llm embedding 服务。暂时不命中。
        return False
    except Exception as e:
        logger.warning(f"industry vector match failed: {e}")
        return False


def score_industry(
    resume_work_experience: str,
    industries: list[str],
    db_session: Any = None,
) -> float:
    """返回 0-100 分."""
    if not industries:
        return 100.0
    if not resume_work_experience:
        return 0.0

    work_lower = resume_work_experience.lower()
    hits = 0
    for industry in industries:
        if not industry:
            continue
        if industry.lower() in work_lower:
            hits += 1
        elif _vector_match(industry, resume_work_experience, db_session):
            hits += 1

    return round(hits / len(industries) * 100.0, 2)
