"""学校等级分类与门槛比较单元测试"""
import pytest

from app.modules.im_intake.school_tier import (
    classify_school,
    tier_value,
    meets_school_tier,
    meets_education,
    SCHOOLS_985,
    SCHOOLS_211,
    SCHOOLS_QS_TOP200,
)


class TestClassifySchool:
    def test_empty_returns_empty(self):
        assert classify_school("") == ""
        assert classify_school(None) == ""
        assert classify_school("   ") == ""

    def test_unknown_returns_empty(self):
        assert classify_school("某某不知名学院") == ""

    def test_985_school(self):
        assert classify_school("清华大学") == "985"
        assert classify_school("北京大学") == "985"
        assert classify_school("浙江大学") == "985"

    def test_211_only_school(self):
        assert classify_school("北京邮电大学") == "211"
        assert classify_school("华东理工大学") == "211"

    def test_qs_top200_overseas(self):
        assert classify_school("麻省理工学院") == "qs_top200"
        assert classify_school("斯坦福大学") == "qs_top200"
        assert classify_school("剑桥大学") == "qs_top200"

    def test_alias_short_form(self):
        assert classify_school("清华") == "985"
        assert classify_school("北大") == "985"
        assert classify_school("人大") == "985"

    def test_whitespace_trim(self):
        assert classify_school("  清华大学  ") == "985"
        assert classify_school("\t清华大学\n") == "985"

    def test_985_takes_priority_over_211(self):
        # 985 必为 211，结果应为 985
        for s in list(SCHOOLS_985)[:5]:
            assert classify_school(s) == "985"

    def test_overseas_english_name_not_matched(self):
        # 简历解析按中文出，英文名不必识别
        assert classify_school("Massachusetts Institute of Technology") == ""


class TestTierValue:
    def test_ordering(self):
        assert tier_value("") < tier_value("qs_top200")
        assert tier_value("qs_top200") < tier_value("211")
        assert tier_value("211") < tier_value("985")

    def test_unknown_treated_as_empty(self):
        assert tier_value("foo") == 0


class TestMeetsSchoolTier:
    def test_no_requirement_passes_all(self):
        assert meets_school_tier("", "") is True
        assert meets_school_tier("985", "") is True
        assert meets_school_tier("", "") is True

    def test_candidate_no_school_fails_when_required(self):
        assert meets_school_tier("", "qs_top200") is False
        assert meets_school_tier("", "211") is False
        assert meets_school_tier("", "985") is False

    def test_qs_top200_meets_qs_requirement(self):
        assert meets_school_tier("qs_top200", "qs_top200") is True

    def test_qs_top200_fails_211(self):
        assert meets_school_tier("qs_top200", "211") is False

    def test_211_meets_qs_top200(self):
        assert meets_school_tier("211", "qs_top200") is True

    def test_985_meets_all_lower(self):
        assert meets_school_tier("985", "qs_top200") is True
        assert meets_school_tier("985", "211") is True
        assert meets_school_tier("985", "985") is True

    def test_211_fails_985(self):
        assert meets_school_tier("211", "985") is False


class TestMeetsEducation:
    def test_no_requirement_passes(self):
        assert meets_education("", "") is True
        assert meets_education("本科", "") is True

    def test_candidate_no_education_fails(self):
        assert meets_education("", "本科") is False

    def test_higher_meets_lower(self):
        assert meets_education("硕士", "本科") is True
        assert meets_education("博士", "硕士") is True
        assert meets_education("博士", "本科") is True

    def test_lower_fails_higher(self):
        assert meets_education("本科", "硕士") is False
        assert meets_education("大专", "本科") is False

    def test_equal_passes(self):
        assert meets_education("本科", "本科") is True


class TestResearchInstitutes985Equiv:
    """BUG-125: 中科院系统 + 国家级实验室视同 985 档."""

    def test_ucas_full_name(self):
        assert classify_school("中国科学院大学") == "985"

    def test_ucas_aliases(self):
        for alias in ("国科大", "中科院大学", "UCAS", "中国科学院"):
            assert classify_school(alias) == "985", f"alias {alias!r} 未命中 985"

    def test_cas_research_institute(self):
        assert classify_school("中国科学院深圳先进技术研究院") == "985"
        assert classify_school("中国科学院自动化研究所") == "985"
        assert classify_school("中国科学院计算技术研究所") == "985"

    def test_national_lab_985_equiv(self):
        assert classify_school("深圳湾实验室") == "985"
        assert classify_school("鹏城实验室") == "985"
        assert classify_school("之江实验室") == "985"
        assert classify_school("上海人工智能实验室") == "985"

    def test_smaller_alias_resolution(self):
        assert classify_school("深先进") == "985"
        assert classify_school("智源") == "985"


class TestListIntegrity:
    def test_985_subset_of_211(self):
        assert SCHOOLS_985.issubset(SCHOOLS_211), "所有 985 学校应同时为 211"

    def test_985_count(self):
        # 39 所 985
        assert len(SCHOOLS_985) == 39

    def test_211_count(self):
        # 116 所 211（含 985）
        assert len(SCHOOLS_211) == 116

    def test_qs_top200_disjoint_from_211(self):
        # QS top 200 海外清单不与中国大陆 211 重叠
        assert SCHOOLS_QS_TOP200.isdisjoint(SCHOOLS_211)

    def test_qs_top200_size(self):
        assert len(SCHOOLS_QS_TOP200) >= 100  # 海外学校保底 100+
