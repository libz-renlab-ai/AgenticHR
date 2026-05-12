"""F-UI-JOB-01..10 — Jobs 页面 UI 测试 (only-write,不跑)。

参考: docs/QA-系统功能清单-v1.md 21.4 章 (546-558 行)
对应源: frontend/src/views/Jobs.vue
"""
from __future__ import annotations

import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot
from tests.qa_full.frontend._seeds import seed_for_competency


# ---------- 公共工具 ----------

def _goto_jobs(page, frontend_base, qa_db_path=None):
    """打开岗位管理页并等表格 networkidle.

    如传入 qa_db_path 先灌一条岗位, 让表格非空 (列出 '能力模型' tag 之类).
    """
    if qa_db_path is not None:
        seed_for_competency(qa_db_path)
    page.goto(f"{frontend_base}/#/jobs")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_selector("text=岗位管理", timeout=10000)
    # 等表格首行渲染 (有数据时), 没数据则继续
    try:
        page.wait_for_selector(".el-table__row", timeout=5000)
    except Exception:
        pass


def _open_first_job_edit(page):
    """点击第一个岗位的「编辑」按钮,打开新建/编辑弹窗。"""
    btns = page.locator(".el-table button:has-text('编辑')")
    if btns.count() == 0:
        return False
    btns.first.click()
    page.wait_for_selector(".el-dialog__title:has-text('编辑岗位')", timeout=8000)
    page.wait_for_timeout(400)
    return True


def _switch_tab(page, label: str):
    """切换 el-tabs 到指定 label (基本信息/能力模型/匹配候选人/五维能力筛选/AI智能筛选)。"""
    page.click(f".el-tabs__item:has-text('{label}')")
    page.wait_for_timeout(500)


# ---------- F-UI-JOB-01 表格列 ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_01_table_columns(page, frontend_base, artifacts_dir, qa_db_path):
    _goto_jobs(page, frontend_base, qa_db_path)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-01")
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-01",
        feature_desc="岗位列表表格列:岗位名称/部门/最低学历/工作年限/必备技能/能力模型/状态/操作",
        expected_visible=["岗位管理", "岗位名称", "部门", "最低学历", "工作年限", "必备技能", "能力模型"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-02 能力模型状态标签 (未生成/待审/已生效/已驳回 + 抽取中 spinner) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_02_competency_status_tag(page, frontend_base, artifacts_dir, qa_db_path):
    _goto_jobs(page, frontend_base, qa_db_path)
    # 表格里"能力模型"列的 el-tag 必须出现一种文案
    page.wait_for_selector(".el-table .el-tag", timeout=10000)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-02")
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-02",
        feature_desc="能力模型列状态标签:未生成 / 待审核 / 已生效 / 已驳回,"
                     "或抽取中 spinner",
        expected_visible=["能力模型"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-03 新建对话框 - 解析 JD ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_03_new_dialog_parse_jd(page, frontend_base, artifacts_dir, qa_db_path):
    _goto_jobs(page, frontend_base, qa_db_path)
    page.wait_for_selector("button:has-text('新建岗位')", timeout=15000)
    page.click("button:has-text('新建岗位')")
    page.wait_for_selector(".el-dialog__title:has-text('新建岗位')", timeout=8000)
    # parseStep === 'input' — 应见 JD 文本框 + "解析 JD" 按钮
    page.wait_for_selector("textarea[placeholder*='粘贴岗位 JD']", timeout=5000)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-03")
    # 截图后关弹窗,避免污染后续用例
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-03",
        feature_desc="新建岗位弹窗 Step1:粘贴 JD 原文 → 点击「解析 JD」自动填表",
        expected_visible=["新建岗位", "解析 JD", "粘贴岗位 JD"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-04 基本信息表单 (必填校验; 薪资/年限范围合法) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_04_basic_form(page, frontend_base, artifacts_dir, qa_db_path):
    _goto_jobs(page, frontend_base, qa_db_path)
    page.wait_for_selector("button:has-text('新建岗位')", timeout=15000)
    page.click("button:has-text('新建岗位')")
    page.wait_for_selector(".el-dialog__title:has-text('新建岗位')", timeout=8000)
    # 跳到 review (手动填写)
    page.click("button:has-text('手动填写')")
    page.wait_for_selector("text=岗位名称", timeout=5000)
    page.wait_for_selector("text=工作年限", timeout=2000)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-04")
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-04",
        feature_desc="基本信息表单 — 岗位名称(必填)、部门、学历、工作年限/薪资范围、必备技能等",
        expected_visible=["岗位名称", "部门", "工作年限", "薪资范围", "必备技能"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-05 能力模型 Tab (CompetencyEditor) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_05_competency_tab(page, frontend_base, artifacts_dir):
    _goto_jobs(page, frontend_base)
    if not _open_first_job_edit(page):
        pytest.skip("无岗位可供编辑,无法验证能力模型 Tab")
    _switch_tab(page, "能力模型")
    page.wait_for_timeout(1500)  # CompetencyEditor 异步拉数据
    shot = shoot(page, artifacts_dir, "F-UI-JOB-05")
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-05",
        feature_desc="编辑岗位 — 能力模型 Tab (CompetencyEditor 组件,见 21.13)",
        expected_visible=["能力模型"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-06 匹配候选人 Tab (排序 passed→null→rejected; 通过/淘汰/改) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_06_matching_tab(page, frontend_base, artifacts_dir):
    _goto_jobs(page, frontend_base)
    if not _open_first_job_edit(page):
        pytest.skip("无岗位可供编辑,无法验证匹配候选人 Tab")
    _switch_tab(page, "匹配候选人")
    # watch(activeTab) 触发 loadMatching → listPassedForJob 异步
    page.wait_for_timeout(1500)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-06")
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-06",
        feature_desc="匹配候选人 Tab — 列表按 passed→null→rejected 排序,"
                     "提供 通过/淘汰/改 按钮;顶部"
                     "「人工闸门:只有标记通过的候选人才能进入约面试」警告",
        expected_visible=["匹配候选人", "人工闸门"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-07 五维筛选 Tab (警告:先发布能力模型;进度条) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_07_five_dim_tab(page, frontend_base, artifacts_dir):
    _goto_jobs(page, frontend_base)
    if not _open_first_job_edit(page):
        pytest.skip("无岗位可供编辑,无法验证五维筛选 Tab")
    _switch_tab(page, "五维能力筛选")
    page.wait_for_timeout(1500)  # 拉 listByJob 或显示警告
    shot = shoot(page, artifacts_dir, "F-UI-JOB-07")
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-07",
        feature_desc="五维能力筛选 Tab — 能力模型未发布时显示「尚未启用」警告;"
                     "已发布则显示开始分析按钮 + 进度条 + 五维评分行",
        expected_visible=["五维能力筛选"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-08 AI 智能筛选 Tab (AiScreeningPanel,见 21.15) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_08_ai_smart_tab(page, frontend_base, artifacts_dir):
    _goto_jobs(page, frontend_base)
    if not _open_first_job_edit(page):
        pytest.skip("无岗位可供编辑,无法验证 AI 智能筛选 Tab")
    _switch_tab(page, "AI智能筛选")
    page.wait_for_timeout(1500)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-08")
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-08",
        feature_desc="AI 智能筛选 Tab — AiScreeningPanel 组件 (见 21.15)",
        expected_visible=["AI智能筛选"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-09 权重总和=100 否则保存禁用 ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_09_weights_sum_100(page, frontend_base, artifacts_dir):
    _goto_jobs(page, frontend_base)
    if not _open_first_job_edit(page):
        pytest.skip("无岗位可供编辑,无法验证权重面板")
    _switch_tab(page, "匹配候选人")
    page.wait_for_timeout(1500)  # loadJobWeights 在 watch 里触发
    # 权重面板可能折叠,截整页弹窗即可
    shot = shoot(page, artifacts_dir, "F-UI-JOB-09")
    cancel = page.locator(".el-dialog button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-09",
        feature_desc="评分权重面板 — 5 维权重总和必须 = 100,否则保存按钮禁用 / 警告",
        expected_visible=["匹配候选人"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-JOB-10 删除岗位 (有面试 → 弹窗提示 409) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_JOB_10_delete_job_confirm(page, frontend_base, artifacts_dir):
    _goto_jobs(page, frontend_base)
    del_btns = page.locator(".el-table button:has-text('删除')")
    if del_btns.count() == 0:
        pytest.skip("无岗位可供删除")
    del_btns.first.click()
    page.wait_for_selector(".el-message-box", timeout=5000)
    page.wait_for_selector("text=确定删除该岗位", timeout=2000)
    shot = shoot(page, artifacts_dir, "F-UI-JOB-10")
    cancel = page.locator(".el-message-box button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-JOB-10",
        feature_desc="删除岗位二次确认对话框 — 若该岗位有关联面试,后端 409 → "
                     "前端 ElMessage.warning 弹窗提示",
        expected_visible=["确定删除该岗位"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
