"""F3 学历门槛筛选 — 学历等级 + 名校标签的纯函数判定."""
import re
from typing import Literal
from pydantic import BaseModel, Field

_EDU_ORD: dict[str, int] = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}

PrestigiousTag = Literal["985", "211", "双一流", "QS_TOP_100"]

_TIER_RE: dict[str, re.Pattern[str]] = {
    "985": re.compile(r"985"),
    "211": re.compile(r"211"),
    "双一流": re.compile(r"双一流"),
    "QS_TOP_100": re.compile(
        r"QS[\s_]?(?:TOP[\s_]?)?100|世界排名前\s*100", re.IGNORECASE
    ),
}


class EducationFilter(BaseModel):
    """HR 在扩展面板配置的学历门槛."""
    min_level: Literal["大专", "本科", "硕士", "博士"]
    prestigious_tags: list[PrestigiousTag] = Field(default_factory=list)
    require_prestigious: bool = False


class EducationCheckResult(BaseModel):
    passed: bool
    level_pass: bool
    prestigious_pass: bool
    matched_tiers: list[str]
    reason: str


def check_education_threshold(
    candidate_education: str,
    school_tier_tags: list[str],
    filter_: EducationFilter,
) -> EducationCheckResult:
    """对一名候选人判定是否满足学历 + 名校门槛."""
    r = _EDU_ORD.get((candidate_education or "").strip(), 0)
    m = _EDU_ORD[filter_.min_level]
    level_pass = r >= m

    matched: list[str] = []
    for tag in filter_.prestigious_tags:
        pattern = _TIER_RE[tag]
        if any(pattern.search(t or "") for t in school_tier_tags):
            matched.append(tag)
    prestigious_pass = bool(matched) if filter_.require_prestigious else True

    passed = level_pass and prestigious_pass
    reason = _format_reason(
        candidate_education or "", filter_, level_pass, matched
    )
    return EducationCheckResult(
        passed=passed,
        level_pass=level_pass,
        prestigious_pass=prestigious_pass,
        matched_tiers=matched,
        reason=reason,
    )


def _format_reason(
    cand_edu: str, f: EducationFilter, level_pass: bool, matched: list[str]
) -> str:
    parts: list[str] = []
    parts.append(
        f"学历:{cand_edu or '空'}"
        + ("≥" if level_pass else "<")
        + f.min_level
    )
    if f.require_prestigious:
        if matched:
            parts.append(f"名校命中:{','.join(matched)}")
        else:
            parts.append(f"名校未命中(需{','.join(f.prestigious_tags) or '?'})")
    elif matched:
        parts.append(f"名校命中:{','.join(matched)}(参考)")
    return "; ".join(parts)
