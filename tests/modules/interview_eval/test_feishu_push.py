"""feishu_push 推送测试（T8）."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def test_push_card_to_hr_and_interviewer():
    """flag 打开时 HR + 面试官都收到卡片."""
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
         patch.object(feishu_push.settings, "feishu_app_id", "fake-app-id"), \
         patch.object(feishu_push.settings, "feishu_notify_trigger_hr", True):
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
         patch.object(feishu_push.settings, "feishu_app_id", "fake-app-id"), \
         patch.object(feishu_push.settings, "feishu_notify_trigger_hr", True):
        # 不应抛
        feishu_push.push(iv, sc)


def test_build_card_url_uses_getattr_fallback():
    """BUG-IE-008: settings 缺失 app_host/app_port 时不应抛 AttributeError，url 回退默认值."""
    from app.modules.interview_eval import feishu_push

    iv = MagicMock()
    iv.id = 42
    sc = MagicMock()
    sc.dimensions_json = [{"score": 8}]
    sc.hire_recommendation = "hire"

    # 用一个完全没有 app_host/app_port 的对象代替 settings
    class _StubSettings:
        feishu_app_id = "x"

    with patch.object(feishu_push, "settings", _StubSettings()):
        card = feishu_push._build_card(iv, sc)

    # 找到 action button 的 url
    button_url = None
    for el in card["elements"]:
        if el.get("tag") == "action":
            button_url = el["actions"][0]["url"]
            break
    assert button_url is not None
    assert "127.0.0.1" in button_url
    assert "8000" in button_url
    assert "id=42" in button_url


def test_push_skips_hr_when_flag_off():
    """BUG-IE-015: 默认 feishu_notify_trigger_hr=False 时 HR 不收卡片，仅推送给面试官."""
    from app.modules.interview_eval import feishu_push

    iv = MagicMock()
    iv.id = 1
    iv.user_id = 1
    iv.interviewer_id = 2
    sc = MagicMock()
    sc.dimensions_json = [{"score": 7}]
    sc.hire_recommendation = "hire"

    with patch.object(feishu_push, "_send_card") as send, \
         patch.object(feishu_push, "_resolve_hr_feishu_id", return_value="hr-uid"), \
         patch.object(feishu_push, "_resolve_interviewer_feishu_id", return_value="iv-uid"), \
         patch.object(feishu_push.settings, "feishu_app_id", "fake-app-id"), \
         patch.object(feishu_push.settings, "feishu_notify_trigger_hr", False):
        feishu_push.push(iv, sc)
        assert send.call_count == 1
        # 仅给面试官推送
        called_uid = send.call_args_list[0][0][0]
        assert called_uid == "iv-uid"


def test_push_includes_hr_when_flag_on():
    """BUG-IE-015: feishu_notify_trigger_hr=True 时 HR + 面试官都收到（兼容老行为）."""
    from app.modules.interview_eval import feishu_push

    iv = MagicMock()
    iv.id = 1
    iv.user_id = 1
    iv.interviewer_id = 2
    sc = MagicMock()
    sc.dimensions_json = [{"score": 7}]
    sc.hire_recommendation = "hire"

    with patch.object(feishu_push, "_send_card") as send, \
         patch.object(feishu_push, "_resolve_hr_feishu_id", return_value="hr-uid"), \
         patch.object(feishu_push, "_resolve_interviewer_feishu_id", return_value="iv-uid"), \
         patch.object(feishu_push.settings, "feishu_app_id", "fake-app-id"), \
         patch.object(feishu_push.settings, "feishu_notify_trigger_hr", True):
        feishu_push.push(iv, sc)
        assert send.call_count == 2
