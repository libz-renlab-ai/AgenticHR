"""21.10 章 Notifications `/notifications` UI 测试 — F-UI-NOT-01..05 共 5 项。

QA 清单 docs/QA-系统功能清单-v1.md L611-L618。
"""
import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


def _goto_notifications(page, frontend_base):
    page.goto(f"{frontend_base}/notifications")
    page.wait_for_load_state("networkidle", timeout=15000)


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_NOT_01_table_columns(page, frontend_base, artifacts_dir):
    _goto_notifications(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-NOT-01")
    res = verify_screenshot(
        shot, "F-UI-NOT-01",
        "通知记录表格 含 接收人/类型/渠道/主题/状态/时间 列",
        ["接收人", "类型", "渠道", "主题", "状态", "时间"],
        ["错误", "401", "Network Error"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_NOT_02_status_tag_colors(page, frontend_base, artifacts_dir):
    _goto_notifications(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-NOT-02")
    res = verify_screenshot(
        shot, "F-UI-NOT-02",
        "通知状态 tag 颜色规则: sent=绿色 success / failed=红色 danger / generated=灰色 info",
        ["状态"],
        ["401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_NOT_03_view_dialog_pre(page, frontend_base, artifacts_dir):
    _goto_notifications(page, frontend_base)
    # 尝试点击第一个 '查看' 按钮; 没数据时跳过点击但仍截图
    try:
        page.click("button:has-text('查看')", timeout=3000)
        page.wait_for_selector(".el-dialog__title:has-text('通知内容')", timeout=5000)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-NOT-03")
    res = verify_screenshot(
        shot, "F-UI-NOT-03",
        "查看弹窗 用 <pre> 标签格式化展示通知正文(white-space: pre-wrap)",
        ["通知"],
        ["401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_NOT_04_clear_all_prompt(page, frontend_base, artifacts_dir):
    _goto_notifications(page, frontend_base)
    try:
        page.click("button:has-text('清空全部')", timeout=5000)
        page.wait_for_selector(".el-message-box", timeout=5000)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-NOT-04")
    res = verify_screenshot(
        shot, "F-UI-NOT-04",
        "清空全部 弹出 prompt 要求输入 '确认清空' 二次确认",
        ["确认清空", "危险操作"],
        ["401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_NOT_05_pagination_20(page, frontend_base, artifacts_dir):
    _goto_notifications(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-NOT-05")
    res = verify_screenshot(
        shot, "F-UI-NOT-05",
        "通知记录分页器 page-size=20 (>20 条时出现 el-pagination)",
        ["通知记录"],
        ["401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]
