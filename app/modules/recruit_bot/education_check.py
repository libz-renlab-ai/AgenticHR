"""F3 学历门槛筛选 — 学历等级 + 名校标签的纯函数判定."""
import re
from typing import Literal
from pydantic import BaseModel, Field, model_validator

_EDU_ORD: dict[str, int] = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}

PrestigiousTag = Literal["985", "211", "双一流", "QS_TOP_100"]

_TIER_RE: dict[str, re.Pattern[str]] = {
    "985": re.compile(r"985"),
    "211": re.compile(r"211"),
    "双一流": re.compile(r"双一流"),
    # QS 前 100: 兼容 'QS前100' / 'QS TOP 100' / 'QS_TOP_100' / '世界排名前100'
    "QS_TOP_100": re.compile(
        r"QS[\s_]?(?:前|TOP[\s_]?)?100|世界排名前\s*100", re.IGNORECASE
    ),
}

# 39 所 985 工程院校。Boss 推荐卡片大部分时候 *不会* 给清北人复打 "985" / "211" 标签
# (只会给打 "QS前N院校"), 导致 require_prestigious=True 时这些显然名校全被淘。
# 用学校名字白名单做兜底判定: school 命中即视为 985 (隐含也是 211 / 双一流)。
_985_SCHOOLS: frozenset[str] = frozenset({
    "清华大学", "北京大学", "中国人民大学", "北京航空航天大学", "北京理工大学",
    "中国农业大学", "北京师范大学", "中央民族大学", "南开大学", "天津大学",
    "大连理工大学", "东北大学", "吉林大学", "哈尔滨工业大学", "复旦大学",
    "同济大学", "上海交通大学", "华东师范大学", "南京大学", "东南大学",
    "浙江大学", "中国科学技术大学", "厦门大学", "山东大学", "中国海洋大学",
    "武汉大学", "华中科技大学", "中南大学", "湖南大学", "国防科技大学",
    "中山大学", "华南理工大学", "四川大学", "重庆大学", "电子科技大学",
    "西安交通大学", "西北工业大学", "西北农林科技大学", "兰州大学",
    # 中国科学院大学虽非传统 985, 但同等地位, HR 通常视作 985
    "中国科学院大学",
})

# 211 工程院校 (含全部 39 所 985 + 其他 73 所). 这里只列 985 之外的 211 (避免重复定义)。
_211_ONLY_SCHOOLS: frozenset[str] = frozenset({
    "北京交通大学", "北京工业大学", "北京科技大学", "北京化工大学", "北京邮电大学",
    "北京林业大学", "北京中医药大学", "北京外国语大学", "中国传媒大学", "中央财经大学",
    "对外经济贸易大学", "北京体育大学", "中央音乐学院", "中国政法大学", "华北电力大学",
    "天津医科大学", "河北工业大学", "太原理工大学", "内蒙古大学", "辽宁大学",
    "大连海事大学", "延边大学", "东北师范大学", "哈尔滨工程大学", "东北农业大学",
    "东北林业大学", "华东理工大学", "东华大学", "上海财经大学", "上海外国语大学",
    "上海大学", "苏州大学", "南京航空航天大学", "南京理工大学", "中国矿业大学",
    "河海大学", "江南大学", "南京农业大学", "中国药科大学", "南京师范大学",
    "安徽大学", "合肥工业大学", "福州大学", "南昌大学", "中国石油大学",
    "郑州大学", "中国地质大学", "武汉理工大学", "华中农业大学", "华中师范大学",
    "中南财经政法大学", "湖南师范大学", "暨南大学", "华南师范大学", "广西大学",
    "海南大学", "西南交通大学", "西南财经大学", "西南大学", "四川农业大学",
    "贵州大学", "云南大学", "西藏大学", "西北大学", "陕西师范大学",
    "长安大学", "青海大学", "宁夏大学", "新疆大学", "石河子大学",
    "第二军医大学", "第四军医大学",
})

# 双一流 — 含全部 985 + 211 + 约 25 所新增 (世界一流学科建设高校). 这里只把"985+211"
# 之外的双一流新增校列出。HR 一般 require "双一流" 时也愿意接受 985/211; 反向不成立。
_SHUANG_YILIU_ONLY_SCHOOLS: frozenset[str] = frozenset({
    "中国科学院大学", "北京协和医学院", "首都师范大学", "上海海洋大学",
    "上海中医药大学", "南京邮电大学", "南京信息工程大学", "南京林业大学",
    "南京中医药大学", "中国美术学院", "中国音乐学院", "宁波大学",
    "河南大学", "湖南农业大学", "广州中医药大学", "成都中医药大学",
    "天津工业大学", "天津中医药大学", "中央戏剧学院", "中国矿业大学(北京)",
    "中国石油大学(北京)", "中国地质大学(北京)", "华北电力大学(保定)",
})


class EducationFilter(BaseModel):
    """HR 在扩展面板配置的学历门槛."""
    min_level: Literal["大专", "本科", "硕士", "博士"]
    prestigious_tags: list[PrestigiousTag] = Field(default_factory=list)
    require_prestigious: bool = False

    @model_validator(mode="after")
    def _validate_prestigious_tags_consistency(self) -> "EducationFilter":
        if self.require_prestigious and not self.prestigious_tags:
            raise ValueError(
                "require_prestigious=True 时 prestigious_tags 不可为空"
            )
        return self


class EducationCheckResult(BaseModel):
    passed: bool
    level_pass: bool
    prestigious_pass: bool
    matched_tiers: list[str]
    reason: str


def _school_implied_tiers(school: str) -> set[str]:
    """从 candidate_school 名字反推它隐含的所有 prestigious tier 标签.

    Boss 推荐卡片的 tier 标签 (school_tier_tags) 经常缺失或只给 'QS前N院校'
    等弱标签, 导致清华/北大被 require_prestigious=True 直接拒。本函数按学校名
    硬匹配 985/211/双一流, 不依赖 Boss 是否打标签。
    """
    if not school:
        return set()
    s = school.strip()
    tiers: set[str] = set()
    if s in _985_SCHOOLS:
        # 985 隐含 211 + 双一流
        tiers.update({"985", "211", "双一流"})
    elif s in _211_ONLY_SCHOOLS:
        # 211 隐含 双一流
        tiers.update({"211", "双一流"})
    elif s in _SHUANG_YILIU_ONLY_SCHOOLS:
        tiers.add("双一流")
    return tiers


def check_education_threshold(
    candidate_education: str,
    school_tier_tags: list[str],
    filter_: EducationFilter,
    candidate_school: str = "",
) -> EducationCheckResult:
    """对一名候选人判定是否满足学历 + 名校门槛.

    名校命中判定:
        1. 先按 Boss 卡片 ``school_tier_tags`` 字段 + ``_TIER_RE`` 正则匹配
        2. 兜底按 ``candidate_school`` 学校名查 985/211/双一流 白名单
        两者命中取 union, 任一命中即视为命中该 tier。
    """
    r = _EDU_ORD.get((candidate_education or "").strip(), 0)
    m = _EDU_ORD[filter_.min_level]
    level_pass = r >= m

    # 1. Boss tag 文本匹配
    matched: set[str] = set()
    for tag in filter_.prestigious_tags:
        pattern = _TIER_RE[tag]
        if any(pattern.search(t or "") for t in school_tier_tags):
            matched.add(tag)

    # 2. 学校名白名单兜底 (QS_TOP_100 没有学校白名单, 仅靠 tag)
    implied = _school_implied_tiers(candidate_school or "")
    for tag in filter_.prestigious_tags:
        if tag in implied:
            matched.add(tag)

    prestigious_pass = bool(matched) if filter_.require_prestigious else True

    passed = level_pass and prestigious_pass
    matched_sorted = sorted(matched)
    reason = _format_reason(
        candidate_education or "", filter_, level_pass, matched_sorted
    )
    return EducationCheckResult(
        passed=passed,
        level_pass=level_pass,
        prestigious_pass=prestigious_pass,
        matched_tiers=matched_sorted,
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
