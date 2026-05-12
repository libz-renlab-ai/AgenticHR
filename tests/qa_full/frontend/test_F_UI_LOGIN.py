"""F-UI-LOGIN-01..06 — 第 21.1 章 Login 页面 (`/login`)。

QA 系统功能清单 v1, line 510-518。
范式见 tests/qa_full/templates/ui_test_template.py。

Login 页面比较特殊:
- `/login` 不要求 token; 但 conftest 的 `page` fixture 已注入 token
- 注入 token 后访问 `/login`,前端不会强制重定向(路由仅对非 /login 路由检查)
- 因此可直接 goto `/login` 截图。
"""
from __future__ import annotations

import pytest

from tests.qa_full.fixtures.browser import shoot
from tests.qa_full.runners.verifier import verify_screenshot


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_LOGIN_01_auto_detect_init(page, frontend_base, artifacts_dir):
    """无用户时显示注册;有用户时显示登录(ensure_qa_user autouse 已建用户)。"""
    page.goto(f"{frontend_base}/login")
    page.wait_for_load_state("networkidle", timeout=15000)
    shot = shoot(page, artifacts_dir, "F-UI-LOGIN-01")
    res = verify_screenshot(
        shot,
        test_id="F-UI-LOGIN-01",
        feature_desc="Login 页根据 /api/auth/status 自动切换登录/注册形态(qa_user 已存在 → 登录形态)",
        expected_visible=["招聘助手", "用户名", "密码", "登录"],
        expected_absent=["错误", "401", "无法连接服务器"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_LOGIN_02_username_password_required(page, frontend_base, artifacts_dir):
    """用户名/密码必填;密码 < 6 位时显示错误(密码至少6位)。"""
    page.goto(f"{frontend_base}/login")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 用户名留空 → 点击登录,应有"请输入用户名"
    page.locator(".login-btn").click()
    page.wait_for_timeout(300)
    shot = shoot(page, artifacts_dir, "F-UI-LOGIN-02-empty")
    res = verify_screenshot(
        shot,
        test_id="F-UI-LOGIN-02-empty",
        feature_desc="用户名为空提交 → 显示红字 '请输入用户名'",
        expected_visible=["请输入用户名"],
        expected_absent=["登录成功"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]

    # 输入用户名 + 短密码 → '密码至少6位'
    page.locator(".form-item").nth(0).locator("input").fill("qa_user")
    page.locator(".form-item").nth(1).locator("input").fill("123")
    page.locator(".login-btn").click()
    page.wait_for_timeout(300)
    shot2 = shoot(page, artifacts_dir, "F-UI-LOGIN-02-short")
    res2 = verify_screenshot(
        shot2,
        test_id="F-UI-LOGIN-02-short",
        feature_desc="密码 < 6 位时显示红字 '密码至少6位'",
        expected_visible=["密码至少6位"],
        expected_absent=["登录成功"],
        artifacts_dir=artifacts_dir,
    )
    assert res2["passed"], res2["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_LOGIN_03_register_form(page, frontend_base, artifacts_dir):
    """点击 '注册新账号' 切换到注册形态;两次密码不一致时报错。"""
    page.goto(f"{frontend_base}/login")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 强制切到注册形态(无视后端 has_user 状态)
    switch = page.locator(".switch-link").first
    if switch.count() > 0:
        switch.click()
    page.wait_for_timeout(200)
    # 注册形态时表单变为 4 项
    page.locator(".form-item").nth(0).locator("input").fill("new_user")
    page.locator(".form-item").nth(1).locator("input").fill("abcdef")
    page.locator(".form-item").nth(2).locator("input").fill("xxxxxx")  # confirm 不一致
    page.locator(".form-item").nth(3).locator("input").fill("新HR")  # display_name
    page.locator(".login-btn").click()
    page.wait_for_timeout(300)
    shot = shoot(page, artifacts_dir, "F-UI-LOGIN-03")
    res = verify_screenshot(
        shot,
        test_id="F-UI-LOGIN-03",
        feature_desc="注册形态: 显示 '确认密码'、'显示名称' 字段;两次密码不一致 → 红字提示",
        expected_visible=["注册", "确认密码", "显示名称", "两次密码输入不一致"],
        expected_absent=["登录成功"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_LOGIN_04_enter_submits(page, frontend_base, artifacts_dir):
    """焦点在输入框时按 Enter 触发 submit (等价点击登录按钮);
    短密码先进 username 校验返 '请输入用户名' (Vue 顺序: 用户名 → 密码 → 长度).
    """
    page.goto(f"{frontend_base}/login")
    page.wait_for_load_state("networkidle", timeout=15000)
    # 先填用户名再填短密码, 这样按 Enter 才能命中 '密码至少6位' 校验
    user_input = page.locator(".form-item").nth(0).locator("input")
    user_input.fill("qa_user")
    pwd_input = page.locator(".form-item").nth(1).locator("input")
    pwd_input.fill("123")  # 短密码,触发 '密码至少6位'
    pwd_input.press("Enter")
    page.wait_for_timeout(300)
    shot = shoot(page, artifacts_dir, "F-UI-LOGIN-04")
    res = verify_screenshot(
        shot,
        test_id="F-UI-LOGIN-04",
        feature_desc="密码框焦点按 Enter 触发表单提交; 因密码 < 6 位显示错误 '密码至少6位'",
        expected_visible=["招聘助手", "密码至少6位"],
        expected_absent=["登录成功"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_LOGIN_05_error_message_red(page, frontend_base, artifacts_dir):
    """错误消息以红字显示(.error-msg 用 #f56c6c)。"""
    page.goto(f"{frontend_base}/login")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator(".form-item").nth(0).locator("input").fill("qa_user")
    page.locator(".form-item").nth(1).locator("input").fill("wrong_pwd_long")
    page.locator(".login-btn").click()
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-LOGIN-05")
    res = verify_screenshot(
        shot,
        test_id="F-UI-LOGIN-05",
        feature_desc="错误消息以红字提示(密码错或账号错)",
        expected_visible=["招聘助手", "登录"],
        expected_absent=["登录成功"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]


@pytest.mark.ui
@pytest.mark.needs_screenshot
def test_F_UI_LOGIN_06_success_hard_reload(page, frontend_base, artifacts_dir):
    """成功登录后调用 window.location.replace('/'),硬刷新到 Dashboard 加载新 token。"""
    page.goto(f"{frontend_base}/login")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.locator(".form-item").nth(0).locator("input").fill("qa_user")
    page.locator(".form-item").nth(1).locator("input").fill("qa_pwd_2026")
    page.locator(".login-btn").click()
    # 成功登录后会 window.location.replace('/'),等 URL 变化
    try:
        page.wait_for_url(lambda url: not url.endswith("/login"), timeout=10000)
    except Exception:
        pass
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(500)
    shot = shoot(page, artifacts_dir, "F-UI-LOGIN-06")
    res = verify_screenshot(
        shot,
        test_id="F-UI-LOGIN-06",
        feature_desc="登录成功后硬刷新到工作台/(Dashboard),不再显示 '招聘助手' 登录卡片",
        expected_visible=["工作台"],
        expected_absent=["登录卡片", "401", "无法连接"],
        artifacts_dir=artifacts_dir,
    )
    assert res["passed"], res["reason"]
