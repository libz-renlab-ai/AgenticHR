"""ai_parse worker 单测 — Round 10 BUG-132/143/144/146 修复验证."""
from app.modules.resume._ai_parse_worker import _coerce_work_years


class TestBug143CoerceWorkYears:
    """BUG-143: LLM 偶尔返字符串/含数字短语, work_years 应能容错为 int."""

    def test_int_passthrough(self):
        assert _coerce_work_years(5) == 5

    def test_float_truncated(self):
        assert _coerce_work_years(5.7) == 5

    def test_string_digit(self):
        assert _coerce_work_years("5") == 5

    def test_string_with_unit(self):
        assert _coerce_work_years("5 年") == 5
        assert _coerce_work_years("5年工作经验") == 5

    def test_string_decimal(self):
        assert _coerce_work_years("5.5") == 5

    def test_empty_or_invalid_returns_zero(self):
        assert _coerce_work_years(None) == 0
        assert _coerce_work_years("") == 0
        assert _coerce_work_years("无") == 0
        assert _coerce_work_years({"key": 5}) == 0

    def test_bool_returns_zero(self):
        # bool is subclass of int — 显式拒绝
        assert _coerce_work_years(True) == 0
        assert _coerce_work_years(False) == 0


class TestBug132NormalizeEducationFallback:
    """BUG-132: normalize_education 失败时, _ai_parse_worker 应落库 ""
    而非 raw LLM 值. 通过验证 normalize 行为间接覆盖."""

    def test_unknown_education_normalizes_to_empty_string(self):
        from app.modules.resume.pdf_parser import normalize_education
        # 这些是 BUG-126 字典覆盖不到的非规范值
        assert normalize_education("中专") == ""
        assert normalize_education("高中") == ""
        assert normalize_education("大学") == ""  # 模糊词不取
