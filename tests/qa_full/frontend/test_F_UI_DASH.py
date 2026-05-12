"""F-UI-DASH-01..04 — 第 21.2 章 Dashboard 页面 (`/`)。

QA 系统功能清单 v1, line 520-526。
"""
from __future__ import annotations

import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_DASH_01_stats_cards(page, frontend_base, artifacts_dir):
    """4 张统计卡片: 总简历/已通过/已淘汰/待面试。"""
    page.goto(f"{frontend_base}/")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-DASH-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-DASH-01",
        feature_desc="工作台顶部 4 张统计卡片: 总简历数 / 已通过 / 已淘汰 / 待面试",
        expected_visible=["工作台", "总简历", "已通过", "已淘汰", "待面试"],
        expected_absent=["错误", "401", "Failed"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_DASH_02_system_health_card(page, frontend_base, artifacts_dir):
    """系统状态卡: 飞书 / AI / 邮箱 / 腾讯会议 + 已配置/未配置 标签。"""
    page.goto(f"{frontend_base}/")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-DASH-02")
    res = verify_screenshot(
        shot,
        test_id="F-UI-DASH-02",
        feature_desc="系统状态卡显示 4 个服务及配置状态 (已配置/未配置 标签)",
        expected_visible=["系统状态", "飞书", "AI", "邮箱", "腾讯会议"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_DASH_03_quick_start_six_steps(page, frontend_base, artifacts_dir):
    """6 步快速开始: 配置→面试官→岗位→扩展→筛选→面试。"""
    page.goto(f"{frontend_base}/")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-DASH-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-DASH-03",
        feature_desc="快速开始卡片显示 6 步: 配置系统/添加面试官/创建岗位/安装扩展采集/筛选简历/安排面试",
        expected_visible=["快速开始", "配置系统", "添加面试官", "创建岗位", "筛选", "面试"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_DASH_04_quick_start_clickable(page, frontend_base, artifacts_dir):
    """点击快速开始第 1 步 '配置系统' → 跳到 /settings。"""
    page.goto(f"{frontend_base}/")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(300)
    # 第 1 步 = 配置系统 → /settings
    item = page.locator(".quick-start-item.clickable").first
    item.click()
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-DASH-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-DASH-04",
        feature_desc="点击 '配置系统' 步骤后跳转到 /settings,显示设置页 Tab",
        expected_visible=["设置", "AI", "评分权重"],
        expected_absent=["工作台", "401", "错误"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
