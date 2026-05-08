"""prompt 渲染稳定 + PROMPT_VERSION 锁."""
import pytest


def test_prompt_version_constant():
    from app.modules.interview_eval.prompts import PROMPT_VERSION
    assert PROMPT_VERSION == "interview_eval_v1"


def test_build_prompt_renders_assessment_dimensions():
    from app.modules.interview_eval.prompts import build_user_message

    interview_ctx = {
        "candidate_name": "张三",
        "candidate_education": "本科",
        "candidate_years": 3,
        "candidate_skills": "Python, MySQL",
        "job_title": "后端工程师",
        "assessment_dimensions": [
            {"name": "技术深度", "description": "Python/数据库/系统设计",
             "question_types": ["原理", "代码"]},
            {"name": "沟通表达", "description": "结构化与清晰度",
             "question_types": ["开放式"]},
        ],
    }
    transcript = [
        {"start_ms": 0, "end_ms": 1000, "speaker": "interviewer", "text": "你好"},
        {"start_ms": 1100, "end_ms": 3000, "speaker": "candidate", "text": "我用 Spring"},
    ]
    msg = build_user_message(interview_ctx, transcript)
    assert "技术深度" in msg
    assert "沟通表达" in msg
    assert "Spring" in msg
    assert "interviewer" in msg


def test_system_prompt_contains_compliance_guards():
    from app.modules.interview_eval.prompts import SYSTEM
    # 三条合规线必须都在
    assert "禁止编造" in SYSTEM or "证据" in SYSTEM
    assert "口音" in SYSTEM or "外貌" in SYSTEM or "情绪" in SYSTEM
    assert "JSON" in SYSTEM
