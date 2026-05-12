"""Playwright page fixture + 截图辅助。"""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, Page


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance):
    b = playwright_instance.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture
def page(browser, auth_token):
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
        proxy={"server": "direct://"},  # 绕本地代理
    )
    # 注入 token 到 localStorage,前端走 axios 自动带上
    ctx.add_init_script(f"window.localStorage.setItem('token', '{auth_token}');")
    p = ctx.new_page()
    yield p
    p.close()
    ctx.close()


def shoot(page: Page, artifacts_dir: Path, test_id: str) -> Path:
    """统一截图函数,文件名按 test_id。"""
    out = artifacts_dir / "screenshots" / f"{test_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), full_page=True)
    return out
