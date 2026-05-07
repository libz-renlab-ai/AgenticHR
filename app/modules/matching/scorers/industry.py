"""行业 scorer — 关键词包含 + 向量相似度 fallback."""
import logging
import re
from typing import Any

from app.config import settings
from app.core.vector.service import cosine_similarity

logger = logging.getLogger(__name__)

_SIM_THRESHOLD = getattr(settings, "matching_industry_sim", 0.70)

# BUG-096: industry 命中时, 后接以下 anchor 提高可信度
# BUG-131: 真实简历里 "金融工作经验" / "金融项目" / "金融经验" / "金融经历" 这些
# 高频用法 anchor 之前被漏掉, 必须扩展, 否则真实金融候选人行业分被错算 0。
# 注: "系统" / "奖励" 这类产品/活动后缀仍不在列表, 保持 BUG-096 防御
# ("金融奖励系统" 这类无关行业的命中仍被拒)。
_POS_ANCHORS = (
    "行业", "业", "领域", "公司", "集团", "板块", "市场",
    "工作", "项目", "经验", "经历", "相关", "方面", "业务",
)


def _is_zh_or_alnum(ch: str) -> bool:
    return ch.isalnum() or ("一" <= ch <= "鿿")


def _industry_word_match(industry: str, text_lower: str) -> bool:
    """BUG-096: 子串匹配的 word-boundary 防御。

    industry 命中位置的前/后字符不能同时是中文或字母数字 (即必须有边界/标点/anchor),
    或者紧跟 industry 之后是 "行业/业/领域/公司" 等正面 anchor。

    例:
      "金融" in "金融奖励系统"  → 后跟 "奖" (中文 + 非 anchor) → False
      "金融" in "金融行业经验"  → 后跟 "行业" (anchor)        → True
      "金融" in "在某金融公司工作" → 后跟 "公司" (anchor)        → True
      "IT"   in "家住IT园区"   → 后跟 "园" (中文非 anchor)    → False
      "IT"   in "IT 行业 5 年" → 前缀边界 + 后跟 "行业"        → True
    """
    if not industry or not text_lower:
        return False
    pat = re.compile(re.escape(industry.lower()))
    for m in pat.finditer(text_lower):
        s, e = m.span()
        before_ok = (s == 0) or (not _is_zh_or_alnum(text_lower[s - 1]))
        if e >= len(text_lower):
            after_ok = True
        else:
            tail = text_lower[e:]
            if tail.startswith(_POS_ANCHORS):
                return True
            after_ok = not _is_zh_or_alnum(tail[0])
        if before_ok and after_ok:
            return True
    return False


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
        # BUG-099: 静默失败仅 warning, 这里保留是因为行业向量匹配是次级 fallback,
        # 但 SQL 错误本身值得 ERROR 级别便于监控发现 schema drift。
        logger.error(
            "industry vector match SQL failed (possible schema drift): %s", e,
        )
        return False


def score_industry(
    resume_work_experience: str,
    industries: list[str],
    db_session: Any = None,
) -> float:
    """返回 0-100 分.

    BUG-096: 子串匹配改 word-boundary aware match, 避免短行业名 (金融/IT) 命中长字符串中无关行业。
    BUG-152: industries 中过滤空串后无有效要求时返 100% (无要求即满足),
    与 industries=[] 等价, 不再返回 0/n=0%。
    """
    # BUG-152: 先过滤空串, 用 effective_industries 决定有无要求。
    effective_industries = [ind for ind in (industries or []) if ind]
    if not effective_industries:
        return 100.0
    if not resume_work_experience:
        return 0.0

    work_lower = resume_work_experience.lower()
    hits = 0
    for industry in effective_industries:
        if _industry_word_match(industry, work_lower):
            hits += 1
        elif _vector_match(industry, resume_work_experience, db_session):
            hits += 1

    return round(hits / len(effective_industries) * 100.0, 2)
