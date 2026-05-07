"""BUG-126: LLM 输出的学历字符串规范化."""
from app.modules.resume.pdf_parser import normalize_education


def test_empty_returns_empty():
    assert normalize_education("") == ""
    assert normalize_education(None) == ""


def test_already_normalized_passthrough():
    assert normalize_education("本科") == "本科"
    assert normalize_education("硕士") == "硕士"
    assert normalize_education("博士") == "博士"
    assert normalize_education("大专") == "大专"


def test_pipe_separator_takes_highest():
    assert normalize_education("研究生|硕士") == "硕士"
    assert normalize_education("本科 / 硕士在读") == "硕士"
    assert normalize_education("学士|本科") == "本科"


def test_studying_or_pursuing_treated_as_target_level():
    assert normalize_education("硕士在读") == "硕士"
    assert normalize_education("博士在读") == "博士"


def test_chinese_synonyms():
    assert normalize_education("研究生") == "硕士"
    assert normalize_education("学士") == "本科"
    assert normalize_education("专科") == "大专"
    assert normalize_education("高职") == "大专"


def test_english_keywords():
    assert normalize_education("Bachelor of Science") == "本科"
    assert normalize_education("master") == "硕士"
    assert normalize_education("PhD") == "博士"
    assert normalize_education("MBA") == "硕士"


def test_unknown_returns_empty():
    assert normalize_education("无") == ""
    assert normalize_education("不详") == ""
    assert normalize_education("xxx") == ""


def test_picks_highest_when_multiple_levels_present():
    assert normalize_education("本科/博士") == "博士"
    assert normalize_education("大专 升 本科") == "本科"


def test_case_insensitive_english():
    assert normalize_education("MASTER") == "硕士"
    assert normalize_education("Phd Student") == "博士"
