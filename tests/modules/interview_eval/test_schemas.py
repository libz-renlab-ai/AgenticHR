"""Pydantic schema 边界 + 校验."""
import pytest
from pydantic import ValidationError


def test_start_request_minimal():
    from app.modules.interview_eval.schemas import StartJobRequest
    req = StartJobRequest(interview_id=42)
    assert req.interview_id == 42


def test_scorecard_output_dimensions_score_bounds():
    from app.modules.interview_eval.schemas import ScorecardOutput
    valid = {
        "dimensions": [{
            "name": "技术深度", "score": 8, "reasoning": "证据充分",
            "evidence": [{"start_ms": 0, "end_ms": 1000, "speaker": "candidate", "text": "我用过 Spring"}],
        }],
        "hire_recommendation": "hire",
        "strengths": ["扎实"], "risks": [], "followups": [],
    }
    out = ScorecardOutput(**valid)
    assert out.dimensions[0].score == 8

    bad = dict(valid)
    bad["dimensions"] = [dict(valid["dimensions"][0], score=11)]
    with pytest.raises(ValidationError):
        ScorecardOutput(**bad)


def test_scorecard_output_recommendation_enum():
    from app.modules.interview_eval.schemas import ScorecardOutput
    bad = {
        "dimensions": [{
            "name": "X", "score": 5, "reasoning": "y",
            "evidence": [{"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "z"}],
        }],
        "hire_recommendation": "yes_pls",
        "strengths": [], "risks": [], "followups": [],
    }
    with pytest.raises(ValidationError):
        ScorecardOutput(**bad)


def test_evidence_speaker_enum():
    from app.modules.interview_eval.schemas import EvidenceSegment
    EvidenceSegment(start_ms=0, end_ms=1, speaker="candidate", text="x")
    EvidenceSegment(start_ms=0, end_ms=1, speaker="interviewer", text="x")
    with pytest.raises(ValidationError):
        EvidenceSegment(start_ms=0, end_ms=1, speaker="random", text="x")


def test_evidence_end_ms_must_ge_start_ms():
    """IE-009: evidence end_ms < start_ms 应被 model_validator 拒绝."""
    from app.modules.interview_eval.schemas import EvidenceSegment
    # 合法
    EvidenceSegment(start_ms=100, end_ms=200, speaker="candidate", text="x")
    EvidenceSegment(start_ms=100, end_ms=100, speaker="candidate", text="x")  # 等长 OK
    # 非法：反序
    with pytest.raises(ValidationError):
        EvidenceSegment(start_ms=500, end_ms=100, speaker="candidate", text="x")


def test_strengths_max_5():
    from app.modules.interview_eval.schemas import ScorecardOutput
    bad = {
        "dimensions": [{
            "name": "X", "score": 5, "reasoning": "y",
            "evidence": [{"start_ms": 0, "end_ms": 1, "speaker": "candidate", "text": "z"}],
        }],
        "hire_recommendation": "hire",
        "strengths": ["a", "b", "c", "d", "e", "f"],
        "risks": [], "followups": [],
    }
    with pytest.raises(ValidationError):
        ScorecardOutput(**bad)


# IE-026: 放宽 evidence max_length 到 5（LLM 偶尔输出 4-5 个证据）
def test_dimension_accepts_up_to_5_evidence():
    from app.modules.interview_eval.schemas import DimensionScore
    d = DimensionScore(
        name="技术深度", score=8, reasoning="x",
        evidence=[{"start_ms": i * 100, "end_ms": i * 100 + 50,
                   "speaker": "candidate", "text": f"e{i}"} for i in range(5)],
    )
    assert len(d.evidence) == 5


def test_dimension_rejects_6_evidence():
    from app.modules.interview_eval.schemas import DimensionScore
    with pytest.raises(ValidationError):
        DimensionScore(
            name="技术深度", score=8, reasoning="x",
            evidence=[{"start_ms": i * 100, "end_ms": i * 100 + 50,
                       "speaker": "candidate", "text": f"e{i}"} for i in range(6)],
        )
