"""F-UI-RES-01..14 — Resumes 页面 UI 测试 (only-write,不跑)。

参考: docs/QA-系统功能清单-v1.md 21.3 章 (528-544 行)
对应源: frontend/src/views/Resumes.vue
"""
from __future__ import annotations

import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


# ---------- 公共工具 ----------

def _goto_resumes(page, frontend_base):
    """打开简历库页面并等列表 networkidle。"""
    page.goto(f"{frontend_base}/#/resumes")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 顶部工具栏出现即可视为页面就绪
    page.wait_for_selector("text=简历库", timeout=10000)


def _expand_first_row(page):
    """展开列表第一行的详情卡 (toggleExpand)。返回 True/False。"""
    rows = page.locator(".resume-list-item .row-compact")
    if rows.count() == 0:
        return False
    rows.first.click()
    # 展开过渡 200ms
    page.wait_for_timeout(400)
    return True


# ---------- F-UI-RES-01 搜索条 ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_01_search_bar(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    # 输入关键词 + 选状态,触发筛选
    kw = page.locator("input[placeholder*='搜索姓名']")
    kw.fill("张三")
    page.wait_for_timeout(200)
    shot = shoot(page, artifacts_dir, "F-UI-RES-01")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-01",
        feature_desc="简历库顶部搜索条 keyword + status 筛选",
        expected_visible=["简历库", "搜索", "状态", "上传PDF简历"],
        expected_absent=["错误", "401", "Network Error"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-02 上传 PDF ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_02_upload_pdf(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    # 上传按钮可见即可,真正的文件上传留给 backend 接口测试
    upload_btn = page.locator("button:has-text('上传PDF简历')")
    assert upload_btn.count() >= 1, "缺上传按钮"
    shot = shoot(page, artifacts_dir, "F-UI-RES-02")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-02",
        feature_desc="简历库上传 PDF 按钮 (单文件,上传后入库)",
        expected_visible=["上传PDF简历"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-03 启动内容解析 ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_03_start_ai_parse(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    btn = page.locator("button:has-text('手动启动内容解析'), button:has-text('后台内容解析中')")
    assert btn.count() >= 1, "缺手动启动内容解析按钮"
    shot = shoot(page, artifacts_dir, "F-UI-RES-03")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-03",
        feature_desc="简历库手动启动内容解析按钮 (触发 ai-parse-all)",
        expected_visible=["内容解析"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-04 清空全部 (二次确认 prompt 输入「确认清空」) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_04_clear_all_confirm(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    page.click("button:has-text('清空全部')")
    # ElMessageBox.prompt 弹窗,标题"危险操作"
    page.wait_for_selector("text=危险操作", timeout=8000)
    page.wait_for_selector("text=请输入「确认清空」", timeout=2000)
    shot = shoot(page, artifacts_dir, "F-UI-RES-04")
    # 截图后取消,避免误删
    cancel = page.locator(".el-message-box button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-RES-04",
        feature_desc="清空全部对话框 — 必须输入「确认清空」才能继续",
        expected_visible=["危险操作", "确认清空"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-05 紧凑列表行 → 展开为详情卡 ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_05_compact_row_expand(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证展开")
    # 展开后应出现详情卡的字段标签,如 "求职意向" / "工作经历"
    page.wait_for_selector("text=工作经历", timeout=5000)
    shot = shoot(page, artifacts_dir, "F-UI-RES-05")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-05",
        feature_desc="紧凑列表行点击后展开为详情卡",
        expected_visible=["姓名", "工作经历"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-06 详情字段:姓名/求职意向/手机/邮箱/学历/年限/学校 ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_06_detail_fields(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证详情字段")
    page.wait_for_selector("text=工作经历", timeout=5000)
    shot = shoot(page, artifacts_dir, "F-UI-RES-06")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-06",
        feature_desc="详情卡七大字段:姓名/求职意向/手机号/邮箱/学历/工作年限/学校",
        expected_visible=["姓名", "求职意向", "手机号", "邮箱", "学历", "工作年限"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-07 二维码 (加载失败显示重试) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_07_qr_code(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证二维码")
    # 二维码区(.qr-box)应出现 — 加载中/成功/重试三态之一
    page.wait_for_selector(".qr-box", timeout=5000)
    page.wait_for_timeout(800)  # 给 fetch+blob 一点时间
    shot = shoot(page, artifacts_dir, "F-UI-RES-07")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-07",
        feature_desc="详情卡右侧二维码区 — 显示二维码图片或加载中或点击重试",
        expected_visible=["扫码看手机号"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-08 状态按钮 (通过/淘汰) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_08_status_buttons(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证状态按钮")
    page.wait_for_selector(".detail-footer", timeout=5000)
    shot = shoot(page, artifacts_dir, "F-UI-RES-08")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-08",
        feature_desc="详情卡底部状态按钮组:通过 / 淘汰",
        expected_visible=["通过", "淘汰"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-09 查看 PDF (fetch+token,blob URL,60s revoke) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_09_view_pdf_button(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证查看 PDF 按钮")
    page.wait_for_selector(".detail-footer", timeout=5000)
    # "查看PDF" 仅在 row.pdf_path 存在时出现 — 截详情卡即可
    shot = shoot(page, artifacts_dir, "F-UI-RES-09")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-09",
        feature_desc="详情卡底部 — 查看PDF / 简历内容解析 / AI评分 / 删除 等操作按钮",
        expected_visible=["简历内容解析", "AI评分"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-10 AI 评分 (单条,后台轮询) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_10_ai_score_single(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证 AI 评分按钮")
    page.wait_for_selector(".detail-footer", timeout=5000)
    btn = page.locator("button:has-text('AI评分')")
    assert btn.count() >= 1, "缺 AI评分 按钮"
    shot = shoot(page, artifacts_dir, "F-UI-RES-10")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-10",
        feature_desc="详情卡 AI评分 单条按钮 — 启动后台轮询打分任务",
        expected_visible=["AI评分"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-11 删除 (二次确认) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_11_delete_confirm(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证删除确认")
    page.wait_for_selector(".detail-footer", timeout=5000)
    del_btn = page.locator(".detail-footer button:has-text('删除')")
    if del_btn.count() == 0:
        pytest.skip("详情卡缺删除按钮")
    del_btn.first.click()
    page.wait_for_selector("text=确认删除", timeout=5000)
    shot = shoot(page, artifacts_dir, "F-UI-RES-11")
    # 截图后取消
    cancel = page.locator(".el-message-box button:has-text('取消')")
    if cancel.count() >= 1:
        cancel.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-RES-11",
        feature_desc="删除按钮二次确认对话框",
        expected_visible=["确认删除", "不可恢复"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-12 AI 面评弹窗 (总评+技能+项目+自评+原文) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_12_ai_eval_dialog(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证 AI 面评弹窗")
    page.wait_for_selector(".detail-footer", timeout=5000)
    more_btn = page.locator(".detail-footer button:has-text('更多详情')")
    if more_btn.count() == 0:
        pytest.skip("详情卡缺更多详情按钮")
    more_btn.first.click()
    # 弹窗 "简历详情"
    page.wait_for_selector(".el-dialog__title:has-text('简历详情')", timeout=8000)
    page.wait_for_timeout(800)  # matchingApi.listByResume 异步拉数据
    shot = shoot(page, artifacts_dir, "F-UI-RES-12")
    # 截图后关闭弹窗
    close_btn = page.locator(".el-dialog button:has-text('关闭')")
    if close_btn.count() >= 1:
        close_btn.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-RES-12",
        feature_desc="简历详情弹窗 — AI评分/技能/项目/自评/简历原文 + 面试 AI 评价",
        expected_visible=["简历详情"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-13 对接岗位分数表 (5 维分数) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_13_matching_table(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证对接岗位分数表")
    more_btn = page.locator(".detail-footer button:has-text('更多详情')")
    if more_btn.count() == 0:
        pytest.skip("详情卡缺更多详情按钮")
    more_btn.first.click()
    page.wait_for_selector(".el-dialog__title:has-text('简历详情')", timeout=8000)
    page.wait_for_timeout(1500)  # 等 listByResume 拿岗位匹配
    shot = shoot(page, artifacts_dir, "F-UI-RES-13")
    close_btn = page.locator(".el-dialog button:has-text('关闭')")
    if close_btn.count() >= 1:
        close_btn.first.click()
    res = verify_screenshot(
        shot, test_id="F-UI-RES-13",
        feature_desc="简历详情弹窗 — 对接岗位分数表 (岗位/总分/标签),即"
                     "matchingApi.listByResume 返回的 5 维加权得分",
        expected_visible=["简历详情"],
        expected_absent=["错误", "401"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


# ---------- F-UI-RES-14 手机/邮箱校验 (11 位中国号、标准邮箱) ----------

@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_RES_14_phone_email_validation(page, frontend_base, artifacts_dir):
    _goto_resumes(page, frontend_base)
    has_row = _expand_first_row(page)
    if not has_row:
        pytest.skip("简历库为空,无法验证手机校验")
    page.wait_for_selector(".detail-grid", timeout=5000)
    # 故意填一个不合法的手机号触发 ElMessage.warning
    phone_input = page.locator(".detail-grid input[placeholder*='扫右侧']")
    if phone_input.count() == 0:
        pytest.skip("详情卡缺手机号输入框")
    phone_input.first.click()
    phone_input.first.fill("12345")
    # blur 触发 saveField
    page.locator(".detail-grid").first.click(position={"x": 5, "y": 5})
    page.wait_for_timeout(800)
    shot = shoot(page, artifacts_dir, "F-UI-RES-14")
    res = verify_screenshot(
        shot, test_id="F-UI-RES-14",
        feature_desc="手机/邮箱前端校验 — 11 位中国号正则 / 标准邮箱正则,"
                     "不合法时 ElMessage 警告并阻止保存",
        expected_visible=["手机号"],
        expected_absent=["401", "崩溃"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
