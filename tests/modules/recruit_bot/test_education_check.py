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
