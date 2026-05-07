"""BUG-124: effective_education_min 统一两条筛选路径的学历门槛读取口径."""
from types import SimpleNamespace

from app.modules.screening.job_helpers import effective_education_min


def test_prefers_competency_model_education():
    job = SimpleNamespace(
        competency_model={"education": {"min_level": "硕士"}},
        education_min="本科",
    )
    assert effective_education_min(job) == "硕士"


def test_falls_back_to_education_min_when_cm_empty():
    job = SimpleNamespace(competency_model=None, education_min="本科")
    assert effective_education_min(job) == "本科"


def test_falls_back_when_cm_missing_education_field():
    job = SimpleNamespace(competency_model={"hard_skills": []}, education_min="大专")
    assert effective_education_min(job) == "大专"


def test_falls_back_when_cm_education_min_level_blank():
    job = SimpleNamespace(
        competency_model={"education": {"min_level": ""}},
        education_min="本科",
    )
    assert effective_education_min(job) == "本科"


def test_returns_empty_when_both_missing():
    job = SimpleNamespace(competency_model=None, education_min="")
    assert effective_education_min(job) == ""


def test_strips_whitespace():
    job = SimpleNamespace(
        competency_model={"education": {"min_level": "  硕士  "}},
        education_min="",
    )
    assert effective_education_min(job) == "硕士"


def test_none_job_safe():
    assert effective_education_min(None) == ""


def test_bug154_education_field_as_list_falls_back_to_flat():
    """BUG-154: cm.education 是 list 时不应 AttributeError 500, 兜底走扁平字段."""
    job = SimpleNamespace(
        competency_model={"education": ["本科", "硕士"]},  # LLM 偶尔输出 list
        education_min="本科",
    )
    assert effective_education_min(job) == "本科"


def test_bug154_education_field_as_str_falls_back_to_flat():
    """BUG-154: cm.education 是 str 时也不应 crash."""
    job = SimpleNamespace(
        competency_model={"education": "硕士"},  # 老数据格式
        education_min="本科",
    )
    assert effective_education_min(job) == "本科"


def test_bug154_education_field_as_int_falls_back_to_flat():
    """BUG-154: 任意非 dict 类型都不应 crash."""
    job = SimpleNamespace(
        competency_model={"education": 0},
        education_min="大专",
    )
    assert effective_education_min(job) == "大专"
