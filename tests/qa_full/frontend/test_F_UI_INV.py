"""21.9 章 Interviews `/interviews` UI 测试 — F-UI-INV-01..10 共 10 项。

QA 清单 docs/QA-系统功能清单-v1.md L597-L609。

策略:
- 列表页直接 page.goto + 等待 networkidle + 全屏截图 → verifier 判定。
- 弹窗内的下拉/日历需先 openDialog,通过 page.click("button:has-text('+ 新建面试')")
  打开弹窗后再截图。
- 04/05 操作组按钮组依赖卡片本身存在;若库内无 scheduled/completed 数据,
  verifier 也能从空态文案给出通过/失败,这里把"暂无面试"也列入兼容关键词。
"""
import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


def _goto_interviews(page, frontend_base):
    page.goto(f"{frontend_base}/interviews")
    page.wait_for_load_state("networkidle", timeout=15000)


def _open_new_dialog(page):
    """点击 '+ 新建面试' 打开弹窗。"""
    page.click("button:has-text('新建面试')", timeout=5000)
    # 等待 el-dialog 渲染 (header 含 '新建面试')
    page.wait_for_selector(".el-dialog__title:has-text('新建面试')", timeout=5000)


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_01_cards_grouped(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-INV-01")
    res = verify_screenshot(
        shot, "F-UI-INV-01",
        "面试列表卡片按状态分组(scheduled/completed/cancelled)",
        ["面试", "新建面试"],
        ["错误", "401", "Network Error"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_02_card_header(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-INV-02")
    res = verify_screenshot(
        shot, "F-UI-INV-02",
        "卡片头部 含候选人姓名 + 状态标签 + 编辑/删除按钮",
        ["编辑", "删除"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_03_candidate_2x2_block(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-INV-03")
    res = verify_screenshot(
        shot, "F-UI-INV-03",
        "候选人信息 2x2 紧凑网格 显示 学校/学历/手机/邮箱",
        ["学校", "学历", "手机", "邮箱"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_04_action_group_scheduled(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-INV-04")
    res = verify_screenshot(
        shot, "F-UI-INV-04",
        "scheduled 卡片底部操作组 含 创建/重建腾讯会议 + 复制邀请 + 发送通知 + AI 面评 + 取消",
        ["腾讯会议", "复制邀请", "发送面试通知", "AI 面评", "取消面试"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_05_action_group_completed(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    shot = shoot(page, artifacts_dir, "F-UI-INV-05")
    res = verify_screenshot(
        shot, "F-UI-INV-05",
        "completed 卡片底部操作组 仅保留 AI 面评 按钮(不含创建会议/取消)",
        ["AI 面评"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_06_dialog_job_select_filterable(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    try:
        _open_new_dialog(page)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INV-06")
    res = verify_screenshot(
        shot, "F-UI-INV-06",
        "新建面试弹窗 - 目标岗位下拉为 filterable 可搜索 select",
        ["目标岗位", "请选择岗位"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_07_candidate_select_by_job(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    try:
        _open_new_dialog(page)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INV-07")
    res = verify_screenshot(
        shot, "F-UI-INV-07",
        "新建面试弹窗 - 候选人下拉基于 岗位通过状态 过滤(未选岗位时禁用)",
        ["候选人", "请先选择岗位"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_08_interviewer_select_filterable(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    try:
        _open_new_dialog(page)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INV-08")
    res = verify_screenshot(
        shot, "F-UI-INV-08",
        "新建面试弹窗 - 面试官下拉为 filterable 可搜索",
        ["面试官", "搜索面试官"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_09_calendar_5days(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    try:
        _open_new_dialog(page)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INV-09")
    res = verify_screenshot(
        shot, "F-UI-INV-09",
        "面试官 5 天日历 (FullCalendar timeGrid 5days,可拖拽选时间范围)",
        ["请先选择面试官", "拖拽"],
        ["错误", "401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INV_10_clear_all_confirm(page, frontend_base, artifacts_dir):
    _goto_interviews(page, frontend_base)
    # 点击 '清空全部' 弹出 prompt 二次确认对话框
    try:
        page.click("button:has-text('清空全部')", timeout=5000)
        page.wait_for_selector(".el-message-box", timeout=5000)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INV-10")
    res = verify_screenshot(
        shot, "F-UI-INV-10",
        "清空全部面试 弹出 二次确认对话框 要求输入 '确认清空'",
        ["确认清空", "危险操作"],
        ["401"],
        artifacts_dir,
    )
    assert res["passed"], res["reason"]
