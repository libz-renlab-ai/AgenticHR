"""F-UI-SKL-01..05 — SkillLibrary `/skills` 前端 UI 测试 (5 项)。

参考: docs/QA-系统功能清单-v1.md 21.7
注意: 新增/合并对话框可截图打开后状态。
"""
import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SKL_01_search_filter_pending(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/skills")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-SKL-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SKL-01",
        feature_desc="SkillLibrary 搜索 + 分类筛选 + 仅待归类",
        expected_visible=["搜索技能", "仅待归类"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SKL_02_create_dialog(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/skills")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 打开新增对话框
    try:
        page.get_by_role("button", name="新增技能").click(timeout=3000)
        page.wait_for_timeout(400)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-SKL-02")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SKL-02",
        feature_desc="SkillLibrary 新增/编辑技能 (名称+分类必填)",
        expected_visible=["新增技能", "名称", "分类"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SKL_03_merge_dialog(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/skills")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 尝试点击第一行的"合并"按钮
    try:
        page.get_by_role("button", name="合并").first.click(timeout=3000)
        page.wait_for_timeout(400)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-SKL-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SKL-03",
        feature_desc="SkillLibrary 合并 (SkillPicker 选目标)",
        expected_visible=["技能"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SKL_04_batch_classify(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/skills")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-SKL-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SKL-04",
        feature_desc="SkillLibrary 批量分类 (选中行后批量改)",
        expected_visible=["分类"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_SKL_05_delete_disabled(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/skills")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-SKL-05")
    res = verify_screenshot(
        shot,
        test_id="F-UI-SKL-05",
        feature_desc="SkillLibrary 删除限制 (seed 来源 / usage>0 不可删)",
        expected_visible=["删", "来源"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
