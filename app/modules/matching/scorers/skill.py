"""技能匹配 scorer — canonical_id 精确匹配 + bge-m3 向量相似度两段式."""
import logging
from typing import Any

from app.config import settings
from app.core.vector.service import cosine_similarity, unpack_vector

logger = logging.getLogger(__name__)

_EXACT_THRESHOLD = getattr(settings, "matching_skill_sim_exact", 0.75)
_EDGE_THRESHOLD = getattr(settings, "matching_skill_sim_edge", 0.60)


def _parse_resume_skills(resume_skills_text: str) -> list[str]:
    """'Python, Go, FastAPI' → ['Python', 'Go', 'FastAPI'], 去空"""
    if not resume_skills_text:
        return []
    return [s.strip() for s in resume_skills_text.split(",") if s.strip()]


def _lookup_resume_canonicals(resume_skill_names: list[str], db_session=None) -> set[int]:
    """简历侧技能名 → 技能库 canonical_id 集合. db_session=None 时返回空集合（测试用）.

    skills 表实际列名是 canonical_name (不是 name); 历史 SQL 用 name 全静默走
    except 兜底 → 简历技能恒命不中 canonical, skill_score 全 0.
    """
    if not db_session or not resume_skill_names:
        return set()
    from sqlalchemy import text
    placeholders = ",".join(":n" + str(i) for i in range(len(resume_skill_names)))
    params = {f"n{i}": n for i, n in enumerate(resume_skill_names)}
    # skills 表 id 就是 canonical_id (table 不存 canonical_id 列, model 用 id 当 canonical 主键)
    query = text(f"SELECT DISTINCT id FROM skills WHERE canonical_name IN ({placeholders})")
    try:
        rows = db_session.execute(query, params).fetchall()
        return {r[0] for r in rows if r[0] is not None}
    except Exception as e:
        # BUG-099: SQL 错误 (e.g. 列名漂移) 不应静默, 用 ERROR 级别 + 异常类型暴露
        logger.error(
            "lookup canonicals SQL failed (possible schema drift): %s: %s",
            type(e).__name__, e,
        )
        return set()


def _fetch_resume_embeddings(resume_skill_names: list[str], db_session) -> dict[str, bytes]:
    """BUG-025: 批量查询简历侧所有技能的 embedding，1 条 SQL 替代 N 条.
    返回 {skill_name: raw_embedding_bytes} 字典."""
    if not resume_skill_names or not db_session:
        return {}
    from sqlalchemy import text
    placeholders = ",".join(":n" + str(i) for i in range(len(resume_skill_names)))
    params = {f"n{i}": n for i, n in enumerate(resume_skill_names)}
    try:
        rows = db_session.execute(
            text(f"SELECT canonical_name, embedding FROM skills WHERE canonical_name IN ({placeholders}) AND embedding IS NOT NULL"),
            params,
        ).fetchall()
        return {r[0]: r[1] for r in rows if r[1]}
    except Exception as e:
        # BUG-099: SQL 错误升 ERROR 级别
        logger.error(
            "batch fetch resume embeddings SQL failed (possible schema drift): %s: %s",
            type(e).__name__, e,
        )
        return {}


def _max_vector_similarity(skill_name: str, resume_skill_names: list[str], db_session=None,
                           _resume_emb_cache: dict | None = None) -> float:
    """技能名对所有简历侧技能名的最大 cosine. 默认走 skills 表 embedding 列.

    _resume_emb_cache: 预取的 {skill_name: raw_bytes} 字典（BUG-025 优化路径）；
                       为 None 时回退到逐条查询（向后兼容）。
    """
    if not resume_skill_names or not db_session:
        return 0.0
    from sqlalchemy import text
    try:
        row = db_session.execute(
            text("SELECT embedding FROM skills WHERE canonical_name = :n LIMIT 1"),
            {"n": skill_name},
        ).fetchone()
        if not row or not row[0]:
            return 0.0
        hs_vec = unpack_vector(row[0])

        best = 0.0
        if _resume_emb_cache is not None:
            # 批量预取路径：直接用缓存，零额外查询
            for rn in resume_skill_names:
                raw = _resume_emb_cache.get(rn)
                if raw:
                    sim = cosine_similarity(hs_vec, unpack_vector(raw))
                    if sim > best:
                        best = sim
        else:
            # 兜底路径（未传缓存）：逐条查询（原有行为）
            for rn in resume_skill_names:
                r = db_session.execute(
                    text("SELECT embedding FROM skills WHERE canonical_name = :n LIMIT 1"),
                    {"n": rn},
                ).fetchone()
                if r and r[0]:
                    sim = cosine_similarity(hs_vec, unpack_vector(r[0]))
                    if sim > best:
                        best = sim
        return best
    except Exception as e:
        # BUG-099: SQL/向量失败升 ERROR 级别
        logger.error(
            "vector similarity SQL failed for %s (possible schema drift): %s: %s",
            skill_name, type(e).__name__, e,
        )
        return 0.0


def score_skill(
    hard_skills: list[dict],
    resume_skills_text: str,
    db_session: Any = None,
) -> tuple[float, list[str]]:
    """返回 (skill_score 0-100, missing_must_haves: list[str]).

    hard_skills: list of dicts from competency_model['hard_skills'], 每个含
                 name/weight/must_have/canonical_id/level.
    resume_skills_text: Resume.skills 列（逗号分隔字符串）.
    db_session: 供 skills 表 canonical_id 和 embedding 查询；None 时降级到纯名字匹配.
    """
    if not hard_skills:
        return 100.0, []

    resume_skill_names = _parse_resume_skills(resume_skills_text)
    resume_canonicals = _lookup_resume_canonicals(resume_skill_names, db_session)
    # BUG-025: 预取所有简历侧 embedding，避免 score_skill 内 O(N×M) 条查询
    resume_emb_cache = _fetch_resume_embeddings(resume_skill_names, db_session)

    total_weight = 0
    weighted_coverage = 0.0
    missing_must_haves: list[str] = []

    for hs in hard_skills:
        weight = int(hs.get("weight", 5))
        total_weight += weight

        coverage = 0.0
        cid = hs.get("canonical_id")
        if cid is not None and cid in resume_canonicals:
            coverage = 1.0
        else:
            sim = _max_vector_similarity(hs["name"], resume_skill_names, db_session,
                                         _resume_emb_cache=resume_emb_cache)
            if sim >= _EXACT_THRESHOLD:
                coverage = sim
            elif sim >= _EDGE_THRESHOLD:
                coverage = sim * 0.5
            else:
                coverage = 0.0
                if hs.get("must_have"):
                    missing_must_haves.append(hs["name"])

        weighted_coverage += weight * coverage

    if total_weight == 0:
        return 100.0, missing_must_haves

    return round(weighted_coverage / total_weight * 100.0, 2), missing_must_haves
