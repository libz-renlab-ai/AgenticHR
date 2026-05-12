"""UI 测试模板。复制到 tests/qa_full/frontend/test_F_UI_<page>.py 后改名+填充。

使用模式:
    from tests.qa_full.fixtures.browser import shoot
    from tests.qa_full.runners.verifier import verify_screenshot

    def test_F_UI_PAGE_NN_xxx(page, frontend_base, artifacts_dir, http, api_base, auth_headers):
        page.goto(f"{frontend_base}/<route>")
        page.wait_for_load_state("networkidle", timeout=15000)
        # 交互...
        shot = shoot(page, artifacts_dir, "F-UI-PAGE-NN")
        res = verify_screenshot(
            shot,
            test_id="F-UI-PAGE-NN",
            feature_desc="<from QA spec>",
            expected_visible=["元素 1", "元素 2"],
            expected_absent=["错误", "401"],
            artifacts_dir=artifacts_dir,
        )
        assert res["passed"], res["reason"]
"""
