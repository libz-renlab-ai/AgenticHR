"""education_check 纯函数 — 学历等级 + 名校标签判定."""
import pytest
from app.modules.recruit_bot.education_check import (
    check_education_threshold, EducationFilter, EducationCheckResult,
)


def _f(min_level="本科", tags=None, require=False):
    return EducationFilter(
        min_level=min_level,
        prestigious_tags=tags or [],
        require_prestigious=require,
    )


class TestLevelOrdering:
    def test_higher_level_passes(self):
        r = check_education_threshold("硕士", [], _f("本科"))
        assert r.passed and r.level_pass

    def test_equal_level_passes(self):
        r = check_education_threshold("本科", [], _f("本科"))
        assert r.passed and r.level_pass

    def test_lower_level_fails(self):
        r = check_education_threshold("大专", [], _f("本科"))
        assert not r.passed and not r.level_pass

    def test_empty_education_fails(self):
        r = check_education_threshold("", [], _f("本科"))
        assert not r.passed and not r.level_pass

    def test_unknown_education_treated_as_zero(self):
        r = check_education_threshold("中专", [], _f("本科"))
        assert not r.passed

    def test_whitespace_trimmed(self):
        r = check_education_threshold("  硕士  ", [], _f("本科"))
        assert r.passed


class TestPrestigiousMatching:
    def test_require_false_always_passes_tier(self):
        r = check_education_threshold("本科", [], _f("本科", [], False))
        assert r.passed and r.prestigious_pass
        assert r.matched_tiers == []

    def test_require_false_records_matched_tiers_for_evidence(self):
        r = check_education_threshold("本科", ["211院校"], _f("本科", ["211"], False))
        assert r.passed
        assert r.matched_tiers == ["211"]

    def test_require_true_with_match_passes(self):
        r = check_education_threshold(
            "本科", ["985院校"], _f("本科", ["985", "211"], True)
        )
        assert r.passed and r.prestigious_pass
        assert "985" in r.matched_tiers

    def test_require_true_no_match_fails(self):
        r = check_education_threshold(
            "本科", ["普通本科"], _f("本科", ["985"], True)
        )
        assert not r.passed and not r.prestigious_pass

    def test_or_semantics_across_multiple_tags(self):
        r = check_education_threshold(
            "本科", ["双一流院校"], _f("本科", ["985", "211", "双一流"], True)
        )
        assert r.passed
        assert "双一流" in r.matched_tiers

    def test_qs_top_100_pattern_variants(self):
        for tag in ["QS_TOP_100", "QS TOP 100", "QS100", "世界排名前100"]:
            r = check_education_threshold(
                "硕士", [tag], _f("硕士", ["QS_TOP_100"], True)
            )
            assert r.passed, f"failed for {tag}"


class TestCombinedSemantics:
    def test_level_pass_prestigious_fail(self):
        r = check_education_threshold(
            "硕士", ["普通本科"], _f("本科", ["985"], True)
        )
        assert not r.passed
        assert r.level_pass and not r.prestigious_pass

    def test_level_fail_prestigious_pass(self):
        r = check_education_threshold(
            "大专", ["985院校"], _f("本科", ["985"], True)
        )
        assert not r.passed
        assert not r.level_pass and r.prestigious_pass

    def test_reason_contains_diagnostic_info(self):
        r = check_education_threshold("大专", [], _f("本科"))
        assert "大专" in r.reason and "本科" in r.reason


class TestEducationFilterValidator:
    def test_require_prestigious_with_empty_tags_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            EducationFilter(min_level="本科", prestigious_tags=[], require_prestigious=True)

    def test_require_prestigious_with_tags_ok(self):
        f = EducationFilter(min_level="本科", prestigious_tags=["985"], require_prestigious=True)
        assert f.require_prestigious and f.prestigious_tags == ["985"]


class TestSchoolWhitelistFallback:
    """2026-05-18 现网数据观测: Boss 推荐卡片对清华/北大/人大经常不打 '985'/'211'
    标签 (只打 'QS前N院校'), 导致 require_prestigious=True 时 17/18 全淘。
    用 candidate_school 名字按 985/211/双一流 白名单兜底匹配。"""

    def test_qinghua_with_empty_tier_tags_passes_985_filter(self):
        """清华大学 + 标签全空 + 要求 985 → 应通过 (走白名单兜底)."""
        r = check_education_threshold(
            "本科", [], _f("本科", ["985"], True), candidate_school="清华大学"
        )
        assert r.passed and r.prestigious_pass
        assert "985" in r.matched_tiers

    def test_renda_implies_211_and_shuangyiliu(self):
        """中国人民大学是 985, 自动隐含 211 + 双一流."""
        r = check_education_threshold(
            "硕士", [], _f("本科", ["211"], True), candidate_school="中国人民大学"
        )
        assert r.passed
        assert "211" in r.matched_tiers

    def test_211_only_school_matches_211(self):
        """北京邮电大学是 211 (非 985) → 命中 211 不命中 985."""
        r211 = check_education_threshold(
            "本科", [], _f("本科", ["211"], True), candidate_school="北京邮电大学"
        )
        assert r211.passed and "211" in r211.matched_tiers

        r985 = check_education_threshold(
            "本科", [], _f("本科", ["985"], True), candidate_school="北京邮电大学"
        )
        assert not r985.passed, "211-only 学校不应命中 985"

    def test_211_implies_shuangyiliu(self):
        """211 学校自动隐含双一流."""
        r = check_education_threshold(
            "本科", [], _f("本科", ["双一流"], True), candidate_school="北京邮电大学"
        )
        assert r.passed and "双一流" in r.matched_tiers

    def test_non_whitelist_school_with_empty_tags_fails(self):
        """非白名单学校 + 标签空 → 仍然淘 (兜底不放行不在名单的学校)."""
        r = check_education_threshold(
            "本科", [], _f("本科", ["985"], True), candidate_school="某不知名民办学院"
        )
        assert not r.passed

    def test_school_whitelist_union_with_tag_match(self):
        """学校白名单 + Boss 自带 tag 取 union: 都不漏."""
        r = check_education_threshold(
            "硕士", ["985院校"], _f("本科", ["985", "211"], True),
            candidate_school="清华大学",
        )
        assert r.passed
        assert "985" in r.matched_tiers and "211" in r.matched_tiers

    def test_empty_school_no_whitelist_match(self):
        """candidate_school 为空字符串时, 不报错, 仅靠 tag 路径."""
        r = check_education_threshold(
            "本科", ["211院校"], _f("本科", ["211"], True), candidate_school=""
        )
        assert r.passed
        assert "211" in r.matched_tiers

    def test_school_kwarg_defaults_to_empty(self):
        """向后兼容: 不传 candidate_school 时旧调用方应继续工作."""
        r = check_education_threshold("本科", ["211院校"], _f("本科", ["211"], True))
        assert r.passed and "211" in r.matched_tiers
