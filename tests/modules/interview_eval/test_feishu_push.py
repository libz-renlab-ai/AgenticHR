"""feishu_push 推送测试（T8）."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def test_push_card_to_hr_and_interviewer():
    from app.modules.interview_eval import feishu_push
    from app.modules.scheduling.models import Interview

    iv = MagicMock(spec=Interview)
    iv.id = 1
    iv.user_id = 1
    iv.interviewer_id = 2
    iv.start_time = datetime.now(timezone.utc)

    sc = MagicMock()
    sc.dimensions_json = [{"name": "x", "score": 7}, {"name": "y", "score": 8}]
    sc.hire_recommendation = "hire"

    with patch.object(feishu_push, "_send_card") as send, \
         patch.object(feishu_push, "_resolve_hr_feishu_id", return_value="hr-uid"), \
         patch.object(feishu_push, "_resolve_interviewer_feishu_id", return_value="iv-uid"), \
         patch.object(feishu_push.settings, "feishu_app_id", "fake-app-id"):
        feishu_push.push(iv, sc)
        assert send.call_count == 2  # HR + interviewer 各一次


def test_push_failure_does_not_raise():
    from app.modules.interview_eval import feishu_push

    iv = MagicMock()
    iv.id = 1
    iv.user_id = 1
    iv.interviewer_id = 2

    sc = MagicMock()
    sc.dimensions_json = []
    sc.hire_recommendation = "hire"

    with patch.object(feishu_push, "_send_card", side_effect=RuntimeError("飞书 down")), \
         patch.object(feishu_push, "_resolve_hr_feishu_id", return_value="hr"), \
         patch.object(feishu_push, "_resolve_interviewer_feishu_id", return_value="iv"), \
         patch.object(feishu_push.settings, "feishu_app_id", "fake-app-id"):
        # 不应抛
        feishu_push.push(iv, sc)
