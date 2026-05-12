"""F-UI-SET-01..06 — 第 21.11 章 Settings 页面 (`/settings`)。

QA 系统功能清单 v1, line 620-628。
"""
from __future__ import annotations

import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SET_01_ai_tab(page, frontend_base, artifacts_dir):
    """AI 配置 Tab: AI 状态(已启用/未启用) + 模型 + 检测按钮。"""
    page.goto(f"{frontend_base}/settings")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    # 默认进入第一个 Tab = AI 配置
    shot = shoot(page, artifacts_dir, "F-UI-SET-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SET-01",
        feature_desc="设置 → AI 配置 Tab: 显示 AI 状态(已启用/未启用 + 已配置/未配置)、模型字段、检测按钮",
        expected_visible=["设置", "AI 配置", "AI 状态", "模型", "检测"],
        expected_absent=["错误", "401", "Failed"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SET_02_weights_tab(page, frontend_base, artifacts_dir):
    """评分权重 Tab: 5 维输入(技能匹配/工作经验/职位级别/教育背景/行业经验)+ 进度条 + 总和=100。"""
    page.goto(f"{frontend_base}/settings")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 切到 '候选人评分权重' Tab
    page.locator(".el-tabs__item:has-text('评分权重')").first.click()
    page.wait_for_timeout(400)
    shot = shoot(page, artifacts_dir, "F-UI-SET-02")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SET-02",
        feature_desc="评分权重 Tab: 5 个维度(技能匹配/工作经验/职位级别/教育背景/行业经验)各带数字输入与进度条,合计 100%",
        expected_visible=["技能匹配", "工作经验", "职位级别", "教育背景", "行业经验", "合计", "100"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SET_03_save_disabled_when_not_100(page, frontend_base, artifacts_dir):
    """总和 ≠ 100 时保存按钮 disabled。

    el-input-number 用 fill 修改不会触发 change 事件 (Vue v-model + 控件内部 input 转换);
    需通过点击增量按钮或直接 dispatchEvent('change').
    """
    page.goto(f"{frontend_base}/settings")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator(".el-tabs__item:has-text('评分权重')").first.click()
    page.wait_for_timeout(600)
    # 通过点击 increase 按钮多次抬高第一维 (默认 35 → 75 让总 > 100)
    increase_btns = page.locator(".weight-input .el-input-number__increase")
    if increase_btns.count() > 0:
        for _ in range(8):  # 35 + 8*5 = 75
            try:
                increase_btns.first.click(timeout=1000)
            except Exception:
                break
            page.wait_for_timeout(50)
    page.wait_for_timeout(400)
    shot = shoot(page, artifacts_dir, "F-UI-SET-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SET-03",
        feature_desc="将第一维改为 80 后总和 > 100,合计行变红 '需等于 100%',保存按钮置灰 disabled",
        expected_visible=["合计", "需等于 100", "保存"],
        expected_absent=["评分权重已保存", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SET_04_reset_to_defaults(page, frontend_base, artifacts_dir):
    """点击 '恢复默认' 重置 5 维(默认: 35/30/15/10/10 = 100)。"""
    page.goto(f"{frontend_base}/settings")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator(".el-tabs__item:has-text('评分权重')").first.click()
    page.wait_for_timeout(400)
    # 先扰动: 第一维改 80
    inputs = page.locator(".weight-input input")
    if inputs.count() > 0:
        first = inputs.first
        first.click()
        first.fill("")
        first.type("80")
        first.press("Tab")
    page.wait_for_timeout(200)
    # 点击 '恢复默认'
    page.locator("button:has-text('恢复默认')").first.click()
    page.wait_for_timeout(400)
    shot = shoot(page, artifacts_dir, "F-UI-SET-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SET-04",
        feature_desc="点击 '恢复默认' 后,5 维回到默认权重 35/30/15/10/10,合计 = 100% (绿色)",
        expected_visible=["合计", "100", "技能匹配", "工作经验"],
        expected_absent=["需等于 100", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SET_05_boss_tab(page, frontend_base, artifacts_dir):
    """Boss 直聘 Tab: 适配器状态(adapter_type) + 今日操作次数(operations_today/max)。"""
    page.goto(f"{frontend_base}/settings")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator(".el-tabs__item:has-text('Boss')").first.click()
    page.wait_for_timeout(400)
    shot = shoot(page, artifacts_dir, "F-UI-SET-05")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SET-05",
        feature_desc="Boss 直聘 Tab: 显示适配器状态标签 + 今日操作次数 (used/cap)",
        expected_visible=["Boss", "适配器状态", "今日操作次数"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SET_06_feishu_tab(page, frontend_base, artifacts_dir):
    """飞书 Tab: 连接状态 + 检测按钮。"""
    page.goto(f"{frontend_base}/settings")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator(".el-tabs__item:has-text('飞书')").first.click()
    page.wait_for_timeout(400)
    shot = shoot(page, artifacts_dir, "F-UI-SET-06")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SET-06",
        feature_desc="飞书 Tab: 显示连接状态(已配置/未配置 标签) + 检测按钮",
        expected_visible=["飞书", "连接状态", "检测"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
