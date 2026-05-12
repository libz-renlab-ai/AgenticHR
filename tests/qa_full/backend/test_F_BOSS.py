"""14 章 Boss 直聘自动化 (F-BOSS-01..05)。

QA 清单 docs/QA-系统功能清单-v1.md 第 374-382 行。

涵盖：
- F-BOSS-01: 自动打招呼 — adapter 未配置时返友好 message, 不抛 500
- F-BOSS-02: 批量采集简历 — 同上
- F-BOSS-03: status — 返 adapter 状态
- F-BOSS-04: 多用户隔离 (BUG-042) — 缺 JWT 401
- F-BOSS-05: Playwright 反检测 — 内部模块, 标 skip + 注明

注意：
- 默认所有用例打 @pytest.mark.boss; 跑时需 --boss 才会执行 (conftest.py 会跳过)
- 实际 boss adapter 在测试环境多为 None — 端点应优雅返回而非 500
"""
from __future__ import annotations

import pytest


# ============================================================================
# F-BOSS-01 自动打招呼
# ============================================================================

@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_01_greet_no_adapter(api_base, http, auth_headers):
    """F-BOSS-01: adapter=None 时返 200 + 友好 message, 不抛 500。"""
    body = {"job_id": "qa_job", "message": "qa hi", "max_count": 3}
    r = http.post(f"{api_base}/api/boss/greet", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "message" in data
    # adapter 未配置: greeted_count 默认 0
    assert data.get("greeted_count", 0) == 0


@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_01_greet_unauth(api_base, http):
    """F-BOSS-01b: 缺 JWT 应 401 (BUG-042 多租户基线)。"""
    body = {"job_id": "j", "message": "hi", "max_count": 1}
    r = http.post(f"{api_base}/api/boss/greet", json=body)
    assert r.status_code == 401, r.text


# ============================================================================
# F-BOSS-02 批量采集简历
# ============================================================================

@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_02_collect_no_adapter(api_base, http, auth_headers):
    """F-BOSS-02: adapter=None 时返 200 + 友好 message。"""
    r = http.post(f"{api_base}/api/boss/collect", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "message" in data
    assert data.get("collected_count", 0) == 0


@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_02_collect_unauth(api_base, http):
    """F-BOSS-02b: 缺 JWT 应 401。"""
    r = http.post(f"{api_base}/api/boss/collect")
    assert r.status_code == 401, r.text


# ============================================================================
# F-BOSS-03 status
# ============================================================================

@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_03_status(api_base, http, auth_headers):
    """F-BOSS-03: status 返 available/adapter_type/max_operations_per_day。"""
    r = http.get(f"{api_base}/api/boss/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("available", "adapter_type", "max_operations_per_day"):
        assert k in data, f"缺字段 {k}: {data}"
    # adapter=None → adapter_type="none", available=False
    assert isinstance(data["available"], bool)
    assert isinstance(data["max_operations_per_day"], int)


@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_03_status_unauth(api_base, http):
    """F-BOSS-03b: 缺 JWT 应 401。"""
    r = http.get(f"{api_base}/api/boss/status")
    assert r.status_code == 401, r.text


# ============================================================================
# F-BOSS-04 多用户隔离 (BUG-042)
# ============================================================================

@pytest.mark.api
@pytest.mark.boss
def test_F_BOSS_04_multi_tenant_unauth_blocks_all(api_base, http):
    """F-BOSS-04: BUG-042 回归 — 三个端点全部要求 JWT, 缺 token 应一律 401。"""
    endpoints = [
        ("POST", "/api/boss/greet", {"job_id": "j", "message": "hi", "max_count": 1}),
        ("POST", "/api/boss/collect", None),
        ("GET", "/api/boss/status", None),
    ]
    for method, path, body in endpoints:
        if method == "POST":
            r = http.post(f"{api_base}{path}", json=body)
        else:
            r = http.get(f"{api_base}{path}")
        assert r.status_code == 401, f"{method} {path} 缺 JWT 应 401, 实际 {r.status_code}: {r.text}"


# ============================================================================
# F-BOSS-05 Playwright 反检测 — 内部模块, 跳过
# ============================================================================

@pytest.mark.api
@pytest.mark.boss
@pytest.mark.skip(reason="F-BOSS-05 反检测属 Playwright adapter 内部 (init script / human delay), "
                  "无 HTTP 端点可触发; 需启 headed Chromium + 跑 stealth 检测网站验证")
def test_F_BOSS_05_anti_detection():
    pass
