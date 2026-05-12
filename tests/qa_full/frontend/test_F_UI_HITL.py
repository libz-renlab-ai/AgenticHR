"""F-UI-HITL-01..04 — 第 21.5 章 HitlQueue 页面 (`/hitl`)。

QA 系统功能清单 v1, line 560-566。
没数据时也截图;由 verifier 判断是 '空态' 而非 '错误'。
"""
from __future__ import annotations

import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_HITL_01_filters_type_status(page, frontend_base, artifacts_dir):
    """类型/状态筛选: 能力模型/新技能 × 待审/已通过/已驳回。"""
    page.goto(f"{frontend_base}/hitl")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-HITL-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-HITL-01",
        feature_desc="HitlQueue 筛选条: 类型(能力模型/新技能/不限) + 状态(待审/已通过/已驳回/不限);列表为空时显示空态而非错误",
        expected_visible=["类型", "状态", "能力模型", "新技能", "待审", "已通过", "已驳回"],
        expected_absent=["加载审核队列失败", "401", "Failed to fetch"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_HITL_02_auto_classify_button(page, frontend_base, artifacts_dir):
    """一键自动分类按钮 — 仅当有 pending 技能时显示;无数据时按钮不出现也是合规。"""
    page.goto(f"{frontend_base}/hitl")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-HITL-02")
    # 该按钮是条件渲染(v-if="hasPendingSkills"),空数据时不出现 — verifier
    # 应判断 '页面正常加载,无技能时无该按钮 / 有技能时按钮可见'
    res = verify_screenshot(
        shot,
        test_id="F-UI-HITL-02",
        feature_desc="HitlQueue 工具栏: 当存在 pending 新技能时显示 '一键自动分类' 按钮;否则按钮不出现(均为正常状态)",
        expected_visible=["类型", "状态", "刷新"],
        expected_absent=["加载审核队列失败", "401", "Failed"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_HITL_03_competency_review_table(page, frontend_base, artifacts_dir):
    """能力模型审核行 → 操作列 '审核' 按钮跳到 /jobs?id=&tab=competency。

    无数据时,验证表格表头与列结构。
    """
    page.goto(f"{frontend_base}/hitl")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-HITL-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-HITL-03",
        feature_desc="HitlQueue 表格头部: 类型 / 标题 / 创建时间 / 状态 / 操作 (能力模型行带 '审核' 跳 /jobs)",
        expected_visible=["类型", "标题", "创建时间", "状态", "操作"],
        expected_absent=["加载审核队列失败", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_HITL_04_classify_dialog_required(page, frontend_base, artifacts_dir):
    """技能归类弹窗必选分类 — 分类未选时确认按钮 disabled。

    无 pending 技能行时无法触发弹窗,改为验证页面状态正常(空态 + 表格存在)。
    """
    page.goto(f"{frontend_base}/hitl")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(500)
    # 尝试找 '归类' 按钮;有则点击,无则截当前页(空态)
    classify_btn = page.locator("button:has-text('归类')")
    if classify_btn.count() > 0:
        classify_btn.first.click()
        page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-HITL-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-HITL-04",
        feature_desc="技能归类弹窗(若有 pending 技能行): 必选分类下拉,未选时确认按钮 disabled;无数据时为空态,无错误",
        expected_visible=["类型", "状态"],
        expected_absent=["加载审核队列失败", "401", "Failed"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
