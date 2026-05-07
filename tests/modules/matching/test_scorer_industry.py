from unittest.mock import patch
from app.modules.matching.scorers.industry import score_industry


def test_empty_industries_full_score():
    assert score_industry("任意工作经历", []) == 100.0


def test_keyword_full_hit():
    assert score_industry("曾在某互联网公司任职 5 年", ["互联网"]) == 100.0


def test_keyword_case_insensitive():
    assert score_industry("worked at a FinTech firm", ["fintech"]) == 100.0


def test_partial_hit():
    # 2 行业要求, 命中 1 个
    score = score_industry("在互联网公司任职", ["互联网", "教育"])
    assert score == 50.0


def test_no_hit_no_vector_fallback():
    with patch("app.modules.matching.scorers.industry._vector_match", return_value=False):
        score = score_industry("在汽车工厂工作", ["金融"])
    assert score == 0.0


def test_vector_fallback_hit():
    with patch("app.modules.matching.scorers.industry._vector_match", return_value=True):
        # 关键词未命中，向量命中 → 算 1 hit
        score = score_industry("曾在教培机构", ["教育"])
    assert score == 100.0


def test_empty_work_experience():
    assert score_industry("", ["互联网"]) == 0.0


# --- Round 10 fixes ---

class TestBug131WordBoundary:
    """BUG-131: word-boundary 不应过严, "5 年金融工作经验" 等真实简历应能命中。"""

    def test_finance_in_real_resume_phrases_with_work_keyword(self):
        # 候选人写 "5 年金融工作经验" — "金融" 后是 "工作"
        assert score_industry("5 年金融工作经验", ["金融"]) == 100.0

    def test_finance_in_real_resume_phrases_with_project_keyword(self):
        # 候选人写 "金融项目经验丰富" — "金融" 后是 "项目"
        assert score_industry("金融项目经验丰富", ["金融"]) == 100.0

    def test_finance_in_real_resume_phrases_with_experience_keyword(self):
        # 候选人写 "金融经验五年" — "金融" 后是 "经验"
        assert score_industry("金融经验五年", ["金融"]) == 100.0

    def test_negative_case_finance_in_unrelated_compound_still_blocked(self):
        # 反例: "金融奖励" 这种产品名/系统名不应命中 (BUG-096 真正想防御的)
        with patch("app.modules.matching.scorers.industry._vector_match", return_value=False):
            assert score_industry("金融奖励系统是我开发的", ["金融"]) == 0.0


class TestBug152AllEmptyIndustries:
    """BUG-152: industries 中过滤掉空串后无有效要求时, 应返 100% (无要求即满足)。"""

    def test_only_empty_string_returns_full_score(self):
        # 正向: 仅有 "" 进 list → 等价于无行业要求 → 100%
        assert score_industry("任意工作经历", [""]) == 100.0

    def test_mixed_empty_and_valid_uses_only_valid(self):
        # 1 个有效 (互联网), 1 个空 → 仅按有效项计算
        assert score_industry("某互联网公司", ["", "互联网"]) == 100.0

    def test_only_empty_strings_returns_full_score(self):
        assert score_industry("任何文本", ["", "", ""]) == 100.0


class TestBug153CJKExtension:
    """BUG-153: CJK Extension B 字符 (U+20000+) 不应被识别为非中文/字母数字,
    word-boundary 判断不应漏掉它们。"""

    def test_cjk_extension_b_treated_as_alnum(self):
        from app.modules.matching.scorers.industry import _is_zh_or_alnum
        # 𠮷 (U+20BB7) 是 CJK Extension B 中的常用姓氏字
        assert _is_zh_or_alnum("𠮷") is True
        # 基本块字符也应仍然命中
        assert _is_zh_or_alnum("中") is True
        assert _is_zh_or_alnum("a") is True
        assert _is_zh_or_alnum("1") is True
        # 标点应是 False
        assert _is_zh_or_alnum(",") is False
        assert _is_zh_or_alnum(" ") is False
