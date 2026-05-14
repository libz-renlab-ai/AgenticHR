"""IE-018 / IE-019: 配置参数下界校验，防 stale_threshold/period=0 或负数误杀活跃 worker."""
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_stale_threshold_rejects_zero():
    with pytest.raises(ValidationError):
        Settings(interview_eval_stale_threshold_seconds=0)


def test_stale_threshold_rejects_negative():
    with pytest.raises(ValidationError):
        Settings(interview_eval_stale_threshold_seconds=-1)


def test_stale_threshold_accepts_valid():
    s = Settings(interview_eval_stale_threshold_seconds=60)
    assert s.interview_eval_stale_threshold_seconds == 60


def test_reconcile_period_rejects_zero():
    with pytest.raises(ValidationError):
        Settings(interview_eval_reconcile_period_seconds=0)


def test_reconcile_period_rejects_negative():
    with pytest.raises(ValidationError):
        Settings(interview_eval_reconcile_period_seconds=-30)


def test_heartbeat_interval_rejects_too_small():
    with pytest.raises(ValidationError):
        Settings(interview_eval_heartbeat_interval_seconds=0)


def test_asr_max_duration_rejects_too_small():
    with pytest.raises(ValidationError):
        Settings(interview_eval_asr_max_duration_sec=59)


def test_asr_max_duration_default_and_valid():
    s = Settings()
    assert s.interview_eval_asr_max_duration_sec == 1680
    s2 = Settings(interview_eval_asr_max_duration_sec=600)
    assert s2.interview_eval_asr_max_duration_sec == 600
