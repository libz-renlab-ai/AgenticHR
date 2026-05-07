"""Job 元字段读取的统一口径 (BUG-124).

历史代码两条路读学历门槛:
  - intake_view_service.list_matched_for_job → job.education_min  (扁平字段)
  - screening.screen_resumes (approved 时) → competency_model.education.min_level

competency_service.apply_competency_to_job 写时会双写两边, 但
- 老数据 / 手工改 db / 部分迁移路径会让两边漂移
- 调用方必须各自记得"用哪个字段"

把读取统一到一个 helper, 优先 cm.education.min_level (审核过的能力模型为权威),
回退 job.education_min (扁平字段兜底)。两条筛选路径都走它。
"""
from __future__ import annotations
from typing import Any


def effective_education_min(job: Any) -> str:
    """返回岗位生效的最低学历门槛字符串 ('' / '大专' / '本科' / '硕士' / '博士').

    Args:
        job: Job ORM 对象 (含 competency_model + education_min 字段).

    优先级:
      1. competency_model.education.min_level (若 cm.education 是 dict 且字段非空)
      2. job.education_min (扁平字段)
      3. '' (无门槛)

    BUG-154: cm.education 在历史数据 / 手工改 db / LLM 输出错误下可能是 list/str
    类型, 之前 `(cm.get("education") or {}).get("min_level")` 在 list 上抛
    AttributeError 让 screen_resumes / list_matched_for_job 500. 显式 isinstance(dict)
    检查后兜底 job.education_min。
    """
    if job is None:
        return ""
    cm = getattr(job, "competency_model", None)
    if isinstance(cm, dict):
        edu_field = cm.get("education")
        if isinstance(edu_field, dict):
            edu = edu_field.get("min_level")
            if edu:
                return str(edu).strip()
    return (getattr(job, "education_min", "") or "").strip()
