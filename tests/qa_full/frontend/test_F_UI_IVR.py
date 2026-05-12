"""F-UI-IVR-01..04 — Interviewers `/interviewers` 前端 UI 测试 (4 项)。

参考: docs/QA-系统功能清单-v1.md 21.8
注意: 新建表单截图弹窗 + 校验 hint。
"""
import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot
from tests.qa_full.frontend._seeds import seed_for_interviews


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_IVR_01_table_columns(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/interviewers")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-IVR-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-IVR-01",
        feature_desc="Interviewers 表格 (姓名/部门/手机/邮箱/飞书 ID)",
        expected_visible=["面试官管理", "姓名", "部门", "手机号", "邮箱"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_IVR_02_create_form(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/interviewers")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 打开"添加面试官"弹窗,截图含 hint
    try:
        page.get_by_role("button", name="添加面试官").click(timeout=3000)
        page.wait_for_timeout(400)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-IVR-02")
    res = verify_screenshot(
        shot,
        test_id="F-UI-IVR-02",
        feature_desc="Interviewers 新建/编辑表单 (三项至少填一; 手机 11 位; 邮箱合法)",
        expected_visible=["添加面试官", "姓名", "手机号", "邮箱", "至少填写一项"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_IVR_03_feishu_id_lookup_hint(page, frontend_base, artifacts_dir):
    page.goto(f"{frontend_base}/interviewers")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 打开弹窗以露出"留空时由后端按手机/邮箱反查"提示
    try:
        page.get_by_role("button", name="添加面试官").click(timeout=3000)
        page.wait_for_timeout(400)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-IVR-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-IVR-03",
        feature_desc="Interviewers 飞书 ID 自动反查 (留空时由后端按手机/邮箱反查)",
        expected_visible=["飞书ID", "自动"],
        expected_absent=["错误", "401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_IVR_04_delete_conflict(page, frontend_base, artifacts_dir, qa_db_path):
    # 灌一个面试官 + 关联面试, 让表格非空且 '删除' 列按钮存在
    seed_for_interviews(qa_db_path)
    page.goto(f"{frontend_base}/interviewers")
    page.wait_for_load_state("networkidle", timeout=15000)
    try:
        page.wait_for_selector(".el-table__row", timeout=8000)
    except Exception:
        pass
    shot = shoot(page, artifacts_dir, "F-UI-IVR-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-IVR-04",
        feature_desc="Interviewers 列表行 含 删除 按钮 (有待面试时点击会触发 409 友好提示)",
        expected_visible=["面试官管理", "删除"],
        expected_absent=["401", "500"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
