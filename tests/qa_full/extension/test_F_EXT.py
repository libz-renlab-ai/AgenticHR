"""20 章 Edge 浏览器扩展 (F-EXT-01..14)。

策略:
- 静态可测项 (manifest 解析、HTML 含登录区、selectors.js 含风控/付费墙文案):
  无 boss 标记,直接断言文件内容。
- 真实 Boss 页面交互项 (F-EXT-04..12): 标 @pytest.mark.boss + external_real,
  默认 skip (需 --boss + 真实账号 + Edge 加载扩展; 有封号风险)。
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
EXT_DIR = REPO_ROOT / "edge_extension"


# ═════════════════════ 静态可测 (无 boss 标记) ═════════════════════

@pytest.mark.extension
def test_F_EXT_01_extension_dir_exists():
    """F-EXT-01: 扩展目录存在 + 关键文件齐全 (edge://extensions 加载前置)"""
    assert EXT_DIR.exists() and EXT_DIR.is_dir(), f"缺扩展目录: {EXT_DIR}"
    must_have = [
        "manifest.json",
        "background.js",
        "content.js",
        "popup.html",
        "popup.js",
        "f3_selectors.js",
        "chat_scrape.js",
        "main_world_bridge.js",
        "styles.css",
    ]
    missing = [f for f in must_have if not (EXT_DIR / f).exists()]
    assert not missing, f"缺文件: {missing}"


@pytest.mark.extension
def test_F_EXT_02_manifest_v3_permissions():
    """F-EXT-02: V3 manifest + 关键权限 + host (zhipin.com + 127.0.0.1:*)"""
    m = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3, f"manifest_version != 3: {m.get('manifest_version')}"
    perms = set(m.get("permissions", []))
    required = {"activeTab", "storage", "downloads", "alarms", "tabs"}
    missing = required - perms
    assert not missing, f"缺权限: {missing}"
    hosts = m.get("host_permissions", [])
    assert any("zhipin.com" in h for h in hosts), f"host_permissions 缺 zhipin.com: {hosts}"
    assert any("127.0.0.1" in h for h in hosts), f"host_permissions 缺 127.0.0.1: {hosts}"
    # background service_worker (MV3 标志)
    assert "service_worker" in m.get("background", {}), "MV3 应使用 service_worker"


@pytest.mark.extension
def test_F_EXT_03_popup_has_login_section():
    """F-EXT-03: popup.html 含服务器 URL 设置 + 登录区域"""
    html = (EXT_DIR / "popup.html").read_text(encoding="utf-8")
    # 服务器 URL 输入框
    assert 'id="serverUrl"' in html, "popup 缺 serverUrl 输入"
    # 登录卡片
    assert 'id="loginSection"' in html, "popup 缺 loginSection"
    assert 'id="loginUsername"' in html and 'id="loginPassword"' in html, "缺登录用户名/密码"
    assert 'id="btnLogin"' in html, "缺登录按钮"
    # popup.js 应处理 token (通常写 localStorage 或 chrome.storage)
    js = (EXT_DIR / "popup.js").read_text(encoding="utf-8")
    assert "token" in js.lower(), "popup.js 应处理 token"


@pytest.mark.extension
def test_F_EXT_13_risk_selectors_present():
    """F-EXT-13: 风控检测 — selectors.js 含 captcha/verify/风控文案"""
    sel = (EXT_DIR / "f3_selectors.js").read_text(encoding="utf-8")
    # 关键 selector
    assert "captcha-wrap" in sel, "缺 captcha-wrap selector"
    assert "verify-dialog" in sel, "缺 verify-dialog selector"
    # 风控文案
    risk_texts = ["操作过于频繁", "请稍后再试", "账号异常", "人机验证"]
    missing = [t for t in risk_texts if t not in sel]
    assert not missing, f"selectors.js 缺风控文案: {missing}"


@pytest.mark.extension
def test_F_EXT_14_paywall_selectors_present():
    """F-EXT-14: 付费墙检测 — pay-dialog / upgrade-dialog selectors"""
    sel = (EXT_DIR / "f3_selectors.js").read_text(encoding="utf-8")
    assert "pay-dialog" in sel, "缺 pay-dialog selector"
    assert "upgrade-dialog" in sel, "缺 upgrade-dialog selector"
    # 付费墙文案
    paywall_texts = ["开通套餐", "升级会员"]
    missing = [t for t in paywall_texts if t not in sel]
    assert not missing, f"selectors.js 缺付费墙文案: {missing}"


# ═════════════════════ 真实 Boss 测试 (boss + external_real) ═════════════════════

@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_04_context_strip_recognition():
    """F-EXT-04: 上下文条 — 自动识别 recommend/chat/list/detail (需扩展加载)"""
    pytest.skip("需 Edge 加载扩展 + 手动切换 BOSS 页面观察 popup contextLabel; 手动场景")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_05_f3_auto_greet():
    """F-EXT-05: F3 推荐页自动打招呼 (限日配额 + 阈值 + 风控自检)"""
    pytest.skip("需 Boss 登录 + 推荐页有候选 + 真点打招呼按钮; 有封号风险")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_06_f4_single_chat_intake_toggle():
    """F-EXT-06: F4 单聊采集 toggle 写 chrome.storage.local.intake_enabled"""
    pytest.skip("需 Edge 加载扩展 + popup 操作 toggle + DevTools 验 storage; 手动场景")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_07_step1_alarm_60min():
    """F-EXT-07: Step1 alarm 每 60min 触发 + 互斥锁 30min 自动清"""
    pytest.skip("需扩展 SW 长期运行; 互斥逻辑见 background.js PHASE_LOCK_TIMEOUT_MS")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_08_step2_alarm_180min():
    """F-EXT-08: Step2 alarm 每 180min 触发 + LLM 分析 + 发提问"""
    pytest.skip("需扩展 SW 长期运行 + LLM key + 真聊天页; 手动场景")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_09_manual_step1_step2():
    """F-EXT-09: popup 手动按钮立即触发 Step1/Step2 (绕过 alarm)"""
    pytest.skip("需 Edge 加载扩展 + popup 点 btnStep1/btnStep2; 手动场景")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_10_emergency_stop():
    """F-EXT-10: intake_force_reset 清锁 + 停 alarm"""
    pytest.skip("需扩展运行中 + popup 触发紧急停止 + 验 storage 清; 手动场景")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_11_list_batch_request_resume():
    """F-EXT-11: 列表页批量求简历 (调 click_request_resume)"""
    pytest.skip("需 Boss 列表页 + 新招呼 + 真点求简历按钮; 有封号风险")


@pytest.mark.boss
@pytest.mark.external_real
@pytest.mark.extension
def test_F_EXT_12_list_batch_collect():
    """F-EXT-12: 列表页批量采集 — 抓字段 → 注册到后端"""
    pytest.skip("需 Boss 列表页 + 已收到的简历 + 真采集到后端; 手动场景")
