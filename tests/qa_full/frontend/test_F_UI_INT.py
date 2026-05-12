"""F-UI-INT-01..08 — Intake `/intake` 前端 UI 测试 (8 项)。

参考: docs/QA-系统功能清单-v1.md 21.6
注意: 自动化总开关 toggle 可点击但不断言 toggle 后状态(避免改 user settings 污染)。
"""
import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_01_total_switch(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-INT-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-01",
        feature_desc="Intake 自动化总开关 (启动/暂停)",
        expected_visible=["目标候选人数"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_02_target_count(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-INT-02")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-02",
        feature_desc="Intake 目标候选人数 (0-1000)",
        expected_visible=["目标候选人数", "保存"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_03_progress_bar(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-INT-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-03",
        feature_desc="Intake 进度条 (complete/target)",
        expected_visible=["目标"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_04_daily_cap(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-INT-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-04",
        feature_desc="Intake 每日额度 (used/cap, 剩余)",
        expected_visible=["今日自动采集额度", "剩余"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_05_filter_search(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 触发状态下拉打开,展示筛选项
    try:
        page.get_by_placeholder("全部状态").click(timeout=3000)
        page.wait_for_timeout(400)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INT-05")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-05",
        feature_desc="Intake 列表筛选 (状态下拉 + 姓名/Boss ID 搜索)",
        expected_visible=["搜索"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_06_inline_status_dropdown(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-INT-06")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-06",
        feature_desc="Intake 行内状态下拉改 (PATCH .../status)",
        expected_visible=["状态"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_07_action_buttons(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-INT-07")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-07",
        feature_desc="Intake 操作按钮 (开始沟通/重抽/标完成/放弃/删除)",
        expected_visible=["操作"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_INT_08_expand_slots_panel(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/intake")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 尝试展开第一行 (若有数据)
    try:
        expander = page.locator("td.el-table__expand-column .el-table__expand-icon").first
        expander.click(timeout=3000)
        page.wait_for_timeout(600)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-INT-08")
    res = verify_screenshot(
        shot,
        test_id="F-UI-INT-08",
        feature_desc="Intake 展开行 SlotsPanel (见 21.17)",
        expected_visible=["姓名"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
