// popup.js — 招聘助手弹出窗口逻辑

const DEFAULT_SERVER_URL = "http://127.0.0.1:8000";

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const statusTextFull = document.getElementById("statusTextFull");
const serverUrlInput = document.getElementById("serverUrl");
const btnTestConnection = document.getElementById("btnTestConnection");
const btnCollect = document.getElementById("btnCollect");
const btnBatchCollect = document.getElementById("btnBatchCollect");
const btnBatchCollectNew = document.getElementById('btnBatchCollectNew');
const batchNewJobSelect = document.getElementById('batchNewJobSelect');
const batchNewLimit = document.getElementById('batchNewLimit');
const resultArea = document.getElementById("resultArea");
const loginSection = document.getElementById("loginSection");
const userSection = document.getElementById("userSection");
const userSettingsSection = document.getElementById("userSettingsSection");
const displayUser = document.getElementById("displayUser");
const displayUser2 = document.getElementById("displayUser2");
const contextLabel = document.getElementById("contextLabel");
const logCard = document.getElementById("logCard");
const logHint = document.getElementById("logHint");
const settingsPanel = document.getElementById("settingsPanel");
const btnOpenSettings = document.getElementById("btnOpenSettings");
const btnCloseSettings = document.getElementById("btnCloseSettings");
const btnLogoutFromSettings = document.getElementById("btnLogoutFromSettings");

const CARD_IDS = ["cardF3", "cardF4", "cardList", "cardDetail"];

function classifyPage(url) {
  if (!url) return "other";
  if (!/zhipin\.com/.test(url)) return "other";
  if (/\/web\/chat\/recommend/.test(url)) return "recommend";
  if (/\/web\/geek\/resume/.test(url) || /resumeDetail/.test(url)) return "detail";
  if (/\/web\/chat(?!\/recommend)/.test(url)) return "chat";
  return "list";
}

const CTX_TEXT = {
  recommend: "你在 推荐牛人页",
  chat: "你在 Boss 聊天页",
  list: "你在 Boss 消息列表",
  detail: "你在 简历详情页",
  other: "请打开 Boss 直聘网站",
};

let _currentCtx = "other";

async function detectPageContext() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    _currentCtx = classifyPage(tab?.url || "");
  } catch {
    _currentCtx = "other";
  }
  contextLabel.textContent = CTX_TEXT[_currentCtx];
  highlightCard(_currentCtx);
  updatePageHints();
}

function highlightCard(ctx) {
  // Tab 方案下不再用 chipRow 压缩。只对当前 tab 内的卡片做 dim/highlight 视觉提示:
  // 卡片对应的页面与当前页一致 → 正常显示;
  // 否则保持显示但 dim, 暗示"换页才能用"。
  // 这只是视觉, 按钮 disable 状态另外由 checkConnection / 业务逻辑控制。
  const map = { recommend: "cardF3", chat: "cardF4", list: "cardList", detail: "cardDetail" };
  const targetId = map[ctx];
  ["cardF3", "cardF4", "cardList", "cardDetail"].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    if (id === targetId) el.classList.remove("dimmed");
    else el.classList.add("dimmed");
  });
}

// Tab 当前页与黄色提示条联动: 在 找人 tab 但不在推荐牛人页 → 显示提示
function updatePageHints() {
  const huntHint = document.getElementById("huntPageHint");
  const harvestHint = document.getElementById("harvestPageHint");
  if (huntHint) {
    huntHint.classList.toggle("hidden", _currentCtx === "recommend");
  }
  if (harvestHint) {
    // 收人 tab 的功能在 chat / list / detail 任一页都能用一部分
    harvestHint.classList.toggle("hidden", _currentCtx !== "other");
  }
}

function setActiveTab(tabName) {
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach(p => {
    const id = p.id;
    const want = (tabName === "hunt" && id === "tabHunt")
              || (tabName === "harvest" && id === "tabHarvest")
              || (tabName === "log" && id === "tabLog");
    p.classList.toggle("hidden", !want);
  });
}

// ── Initialization ──────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await loadServerUrl();
  await loadAuthToken();
  await detectPageContext();
  await checkConnection();
  updateAuthUI();
  restoreLastResult();
  // F3: load after token is available
  await loadJobs();
  await loadBatchJobs();
  await loadDailyUsage();

  document.getElementById("btnLogin").addEventListener("click", doLogin);
  document.getElementById("btnLogout").addEventListener("click", doLogout);
  if (btnLogoutFromSettings) btnLogoutFromSettings.addEventListener("click", doLogout);

  // Settings slide-over
  if (btnOpenSettings) btnOpenSettings.addEventListener("click", () => settingsPanel.classList.add("open"));
  if (btnCloseSettings) btnCloseSettings.addEventListener("click", () => settingsPanel.classList.remove("open"));

  // "去登录" 按钮 — 未登录空状态里的快捷入口
  const btnGoLogin = document.getElementById("btnGoLogin");
  if (btnGoLogin) btnGoLogin.addEventListener("click", () => settingsPanel.classList.add("open"));

  // Tab 切换
  document.querySelectorAll(".tab").forEach(t => {
    t.addEventListener("click", () => setActiveTab(t.dataset.tab));
  });
  // 默认打开"找人" tab (已登录时); 未登录时 panels 由 updateAuthUI 全部隐藏
  if (_authToken) {
    // 智能默认: 在收人相关页 → 自动进收人 tab
    const harvestCtxs = new Set(["chat", "list", "detail"]);
    setActiveTab(harvestCtxs.has(_currentCtx) ? "harvest" : "hunt");
  }

  // 学历门槛 / 后台自动跟进 / 日志 卡片的折叠点击
  document.querySelectorAll(".collapsible > .head").forEach(head => {
    head.addEventListener("click", () => head.parentElement.classList.toggle("open"));
  });

  // 注: log/学历门槛/后台自动跟进等所有 .collapsible 卡片的折叠事件
  // 统一在下面的 .collapsible > .head 监听器里绑定, 这里不再单独绑 logCard。

  // F4 autoscan toggle (storage key: intake_enabled, read by background.js alarms)
  const autoscanToggle = document.getElementById("intakeAutoscanToggle");
  const autoscanLabel = document.getElementById("intakeAutoscanLabel");
  if (autoscanToggle) {
    chrome.storage.local.get(["intake_enabled"], (r) => {
      const on = !!r.intake_enabled;
      autoscanToggle.checked = on;
      if (autoscanLabel) autoscanLabel.textContent = on ? "开" : "关";
    });
    autoscanToggle.addEventListener("change", () => {
      const on = autoscanToggle.checked;
      chrome.storage.local.set({ intake_enabled: on });
      if (autoscanLabel) autoscanLabel.textContent = on ? "开" : "关";
    });
  }

  // 后台保活 toggle (Audio keep-alive, 解决 hidden tab 节流问题)
  const bgKeepAliveToggle = document.getElementById("bgKeepAliveToggle");
  const bgKeepAliveLabel = document.getElementById("bgKeepAliveLabel");
  const bgKeepAliveHint = document.getElementById("bgKeepAliveHint");
  async function findBossTab() {
    const tabs = await chrome.tabs.query({ url: "https://www.zhipin.com/*" });
    return tabs && tabs.length ? (tabs.find(t => /\/web\/chat/.test(t.url || "")) || tabs[0]) : null;
  }
  if (bgKeepAliveToggle) {
    chrome.storage.local.get(["bg_keep_alive_enabled"], (r) => {
      const on = !!r.bg_keep_alive_enabled;
      bgKeepAliveToggle.checked = on;
      if (bgKeepAliveLabel) bgKeepAliveLabel.textContent = on ? "开" : "关";
    });
    bgKeepAliveToggle.addEventListener("change", async () => {
      const on = bgKeepAliveToggle.checked;
      if (bgKeepAliveLabel) bgKeepAliveLabel.textContent = on ? "开" : "关";
      // 先持久化, content.js 重载时能恢复 pending 状态
      chrome.storage.local.set({ bg_keep_alive_enabled: on });
      const tab = await findBossTab();
      if (!tab) {
        if (bgKeepAliveHint) {
          bgKeepAliveHint.style.display = on ? "block" : "none";
          bgKeepAliveHint.textContent = on ? "⚠️ 打开 Boss 标签后此设置生效" : "";
        }
        return;
      }
      try {
        const result = await chrome.tabs.sendMessage(tab.id, {
          action: on ? "startBgKeepAlive" : "stopBgKeepAlive",
        });
        if (bgKeepAliveHint) {
          if (on && result && result.pending) {
            bgKeepAliveHint.style.display = "block";
            bgKeepAliveHint.textContent = "⚠️ 请点 Boss 页面任意位置激活";
          } else {
            bgKeepAliveHint.style.display = "none";
          }
        }
      } catch (e) {
        if (bgKeepAliveHint) {
          bgKeepAliveHint.style.display = "block";
          bgKeepAliveHint.textContent = "⚠️ 通信失败, 请刷新 Boss 页面";
        }
      }
    });
  }

  // Step1/Step2 manual triggers
  const btnStep1 = document.getElementById("btnStep1");
  const btnStep2 = document.getElementById("btnStep2");
  const phaseStatus = document.getElementById("phaseStatus");

  function refreshPhaseStatus() {
    chrome.runtime.sendMessage({ type: "get_phase_status" }, (s) => {
      if (chrome.runtime.lastError || !phaseStatus) return;
      if (s?.phase_running) {
        phaseStatus.textContent = `运行中: ${s.phase_running === "step1" ? "Step1 扫描列表" : "Step2 分析聊天"}`;
        phaseStatus.style.color = "var(--primary-dark)";
      } else {
        phaseStatus.textContent = s?.intake_enabled ? "自动采集已开启，空闲中" : "自动采集已关闭";
        phaseStatus.style.color = "var(--text-faint)";
      }
    });
  }
  refreshPhaseStatus();
  setInterval(refreshPhaseStatus, 5000);

  async function triggerStep(type) {
    const label = type === "manual_step1" ? "Step1 扫描" : "Step2 分析";
    showResult(`正在触发 ${label}...`, "");
    if (btnStep1) btnStep1.disabled = true;
    if (btnStep2) btnStep2.disabled = true;
    try {
      const result = await chrome.runtime.sendMessage({ type });
      if (result?.skipped) {
        showResult(`${label} 被跳过: ${result.skipped}`, "");
      } else if (result?.ok === false) {
        showResult(`${label} 失败: ${result.reason || result.error || "未知错误"}`, "error");
      } else {
        const r = result || {};
        const detail = type === "manual_step1"
          ? (r.registered != null
              ? `注册 ${r.registered} 人, 失败 ${r.failed ?? 0}, 扫描 ${r.scanned} 人`
              : "完成（刷新候选人页查看结果）")
          : (r.processed != null
              ? `处理 ${r.processed}, 无新消息 ${r.skipped_no_new ?? 0}`
              : "完成（刷新候选人页查看结果）");
        showResult(`${label} 完成: ${detail}`, "success");
      }
    } catch (e) {
      showResult(`${label} 异常: ${e.message}`, "error");
    } finally {
      if (btnStep1) btnStep1.disabled = false;
      if (btnStep2) btnStep2.disabled = false;
      refreshPhaseStatus();
    }
  }

  if (btnStep1) btnStep1.addEventListener("click", () => triggerStep("manual_step1"));
  if (btnStep2) btnStep2.addEventListener("click", () => triggerStep("manual_step2"));
});

// ── Server URL persistence ──────────────────────────────────────────

async function loadServerUrl() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["serverUrl"], (result) => {
      serverUrlInput.value = result.serverUrl || DEFAULT_SERVER_URL;
      resolve();
    });
  });
}

function saveServerUrl() {
  const url = serverUrlInput.value.trim() || DEFAULT_SERVER_URL;
  chrome.storage.local.set({ serverUrl: url });
  return url;
}

function getServerUrl() {
  return serverUrlInput.value.trim() || DEFAULT_SERVER_URL;
}

// ── Auth token persistence ──────────────────────────────────────────

let _authToken = '';
let _authUser = '';

async function loadAuthToken() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["authToken", "authUser"], (result) => {
      _authToken = result.authToken || '';
      _authUser = result.authUser || '';
      resolve();
    });
  });
}

function getAuthToken() {
  return _authToken;
}

function updateAuthUI() {
  const name = _authUser || '用户';
  const needLogin = document.getElementById('needLoginCard');
  const tabbar = document.getElementById('tabbar');
  const panels = document.querySelectorAll('.tab-panel');
  if (_authToken) {
    if (loginSection) loginSection.style.display = 'none';
    if (userSection) userSection.classList.remove('hidden');
    if (userSettingsSection) userSettingsSection.style.display = 'block';
    if (displayUser) displayUser.textContent = name;
    if (displayUser2) displayUser2.textContent = name;
    // 显示 tab + 主体
    if (needLogin) needLogin.classList.add('hidden');
    if (tabbar) tabbar.style.display = '';
    // panels 的显示由 setActiveTab 控制, 这里仅恢复默认
  } else {
    if (loginSection) loginSection.style.display = 'block';
    if (userSection) userSection.classList.add('hidden');
    if (userSettingsSection) userSettingsSection.style.display = 'none';
    // 显示空状态, 隐藏 tab + 所有 panel
    if (needLogin) needLogin.classList.remove('hidden');
    if (tabbar) tabbar.style.display = 'none';
    panels.forEach(p => p.classList.add('hidden'));
  }
}

async function doLogin() {
  const url = getServerUrl();
  const username = document.getElementById("loginUsername").value.trim();
  const password = document.getElementById("loginPassword").value;
  if (!username || !password) { showResult("请输入用户名和密码", "error"); return; }

  try {
    const resp = await fetch(`${url}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await resp.json();
    if (!resp.ok) { showResult(data.detail || "登录失败", "error"); return; }
    _authToken = data.token;
    _authUser = data.user?.display_name || data.user?.username || username;
    chrome.storage.local.set({ authToken: _authToken, authUser: _authUser });
    updateAuthUI();
    showResult("登录成功", "success");
    await loadJobs();
    await loadBatchJobs();
    await loadDailyUsage();
  } catch {
    showResult("无法连接服务器", "error");
  }
}

function doLogout() {
  _authToken = '';
  _authUser = '';
  chrome.storage.local.remove(["authToken", "authUser"]);
  updateAuthUI();
  // F3: clear
  if (recruitJobSelect) recruitJobSelect.innerHTML = '<option value="">-- 选择岗位 --</option>';
  if (usageUsed) usageUsed.textContent = '0';
  if (usageCap) usageCap.textContent = '0';
  if (recruitStats) recruitStats.textContent = '';
  showResult("已退出登录", "");
}

// ── Connection check ────────────────────────────────────────────────

// 这些按钮即使后端未连通也允许点击：
// - 测试连接：本身就是为了重试连接
// - 设置/登录类：纯本地或本地→后端 401 比 disabled 更直观
// - F4 手动 Step1/Step2：后端如果未连通，错误会落到 result-area，让用户看到具体原因，
//   而不是按钮死灰、看似完全没反应（曾经的"点了没反应" bug）
const _ALWAYS_ENABLED_BTN_IDS = new Set([
  'btnTestConnection',
  'btnOpenSettings',
  'btnCloseSettings',
  'btnLogin',
  'btnLogout',
  'btnLogoutFromSettings',
  'btnStep1',
  'btnStep2',
  'intakeAutoscanToggle',
]);

function _setBackendDependentButtons(disabled) {
  document.querySelectorAll('button, input[type="checkbox"]').forEach(btn => {
    if (_ALWAYS_ENABLED_BTN_IDS.has(btn.id)) return;
    btn.disabled = disabled;
    btn.style.opacity = disabled ? '0.5' : '';
  });
}

async function checkConnection() {
  const url = getServerUrl();
  setStatus("checking");
  showResult("正在检测连接...", "");

  try {
    const resp = await fetch(`${url}/api/health`, {
      signal: AbortSignal.timeout(5000),
    });
    if (resp.ok) {
      setStatus("connected");
      showResult("已连接到招聘助手后端", "success");
      _setBackendDependentButtons(false);
    } else {
      setStatus("error");
      showResult(`连接失败: HTTP ${resp.status}`, "error");
      _setBackendDependentButtons(true);
    }
  } catch (err) {
    setStatus("error");
    showResult(`无法连接到服务器: ${err.message}\n请确认后端服务已启动`, "error");
    _setBackendDependentButtons(true);
  }
}

// ── Collect current resume ──────────────────────────────────────────

async function collectCurrentResume() {
  const url = saveServerUrl();
  const token = getAuthToken();
  showResult("正在采集当前候选人信息...", "");
  setButtonsDisabled(true);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.includes("zhipin.com")) {
      showResult("请在Boss直聘页面上使用此功能", "error");
      setButtonsDisabled(false);
      return;
    }

    // Pre-flight: verify content script is injected
    try {
      await chrome.tabs.sendMessage(tab.id, { action: "ping" });
    } catch (e) {
      showResult("请先刷新Boss直聘页面（插件需要页面刷新后才能工作）", "error");
      setButtonsDisabled(false);
      return;
    }

    const response = await chrome.tabs.sendMessage(tab.id, {
      action: "collectCurrentResume",
      serverUrl: url,
      authToken: token,
    });

    if (!response?.success) {
      showResult(`采集失败: ${response?.message || "无响应，请刷新页面重试"}`, "error");
      setButtonsDisabled(false);
      return;
    }

    const d = response.data;
    const method = response.method || "page_only";

    // 构建调试日志
    const logLines = response.log?.length ? ['\n--- 调试日志 ---', ...response.log] : [];

    if (method === "pdf_uploaded") {
      showResult(
        [`采集成功 (PDF已解析)`, `姓名: ${d.name}`, `手机: ${d.phone}`, `邮箱: ${d.email}`, `学历: ${d.education}`, ...logLines].join('\n'),
        "success"
      );
    } else {
      // 页面信息提交
      const postResp = await fetch(`${url}/api/resumes/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { "Authorization": `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          name: d.name || "", phone: d.phone || "", email: d.email || "",
          education: d.education || "", work_years: d.work_years || 0,
          job_intention: d.job_intention || "", skills: d.skills || "",
          work_experience: d.work_experience || "", source: "boss_zhipin",
          raw_text: d.raw_text || "",
        }),
      });
      if (postResp.ok) {
        const result = await postResp.json();
        showResult(
          [`采集成功 (仅页面信息)`, `姓名: ${result.name}`, `手机: ${result.phone}`, `邮箱: ${result.email}`, `学历: ${result.education}`, ...logLines].join('\n'),
          "success"
        );
      } else {
        showResult([`提交失败: HTTP ${postResp.status}`, ...logLines].join('\n'), "error");
      }
    }
  } catch (err) {
    showResult(`操作失败: ${err.message}`, "error");
  } finally {
    setButtonsDisabled(false);
  }
}

// ── Batch collect from list ─────────────────────────────────────────

async function batchCollectFromList() {
  const url = saveServerUrl();
  const token = getAuthToken();

  // Pre-flight: ping the content script to verify page readiness
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.includes("zhipin.com")) {
      showResult("请在Boss直聘页面上使用此功能", "error");
      return;
    }

    let pingResp;
    try {
      pingResp = await chrome.tabs.sendMessage(tab.id, { action: "ping" });
    } catch (e) {
      showResult("请先刷新Boss直聘页面（插件需要页面刷新后才能工作）", "error");
      return;
    }
    if (chrome.runtime.lastError || !pingResp) {
      showResult("请先刷新Boss直聘页面（插件需要页面刷新后才能工作）", "error");
      return;
    }
    if (!pingResp.onMessagePage) {
      showResult("请先打开Boss直聘的「消息」页面再进行批量采集", "error");
      return;
    }
  } catch (err) {
    showResult("请先刷新Boss直聘页面（插件需要页面刷新后才能工作）", "error");
    return;
  }

  showResult("正在逐个采集候选人详细信息并下载PDF简历...\n请勿操作Boss直聘页面！", "");
  setButtonsDisabled(true);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    const response = await chrome.tabs.sendMessage(tab.id, {
      action: "batchCollect",
      serverUrl: url,
      authToken: token,
    });

    if (!response?.success) {
      showResult(`采集失败: ${response?.message || "无响应，请刷新页面重试"}`, "error");
      setButtonsDisabled(false);
      return;
    }

    const summary = response.summary || {};
    const results = response.data || [];

    // Categorized counts
    const withPdf = results.filter(r => r.status === 'pdf_uploaded').length;
    const pageOnly = results.filter(r => r.status === 'page_created' || r.status === 'page_only' || r.status === 'duplicate').length;
    const failedCount = results.filter(r => ['skip', 'name_mismatch', 'error'].includes(r.status)).length;
    const missingContact = results.filter(r => r.noPhone && r.noEmail).length;

    const lines = [
      `批量采集完成`,
      `总计: ${summary.total || results.length} 人`,
      `PDF简历: ${withPdf} 人`,
      `仅页面信息: ${pageOnly} 人`,
      `失败/跳过: ${failedCount} 人`,
      ...(missingContact > 0 ? [`缺少联系方式（无手机且无邮箱）: ${missingContact} 人`] : []),
      ``,
      `详情:`,
    ];
    results.forEach(r => {
      const label = r.status === 'pdf_uploaded' ? 'PDF已上传解析'
        : r.status === 'page_created' ? '页面信息已新增'
        : r.status === 'duplicate' ? '已存在(更新)'
        : r.status === 'page_only' ? '仅页面信息'
        : r.status;
      lines.push(`  ${r.name}: ${label}`);
    });

    // 显示调试日志
    if (response.log && response.log.length > 0) {
      lines.push('', '--- 调试日志 ---');
      response.log.forEach(l => lines.push(l));
    }
    showResult(lines.join("\n"), summary.failed === 0 ? "success" : "error");
  } catch (err) {
    showResult(`操作失败: ${err.message}`, "error");
  } finally {
    setButtonsDisabled(false);
  }
}

// ── Batch collect NEW candidates from list ──────────────────────────

async function batchCollectNewFromList() {
  const url = saveServerUrl();
  const token = getAuthToken();
  if (!token) { showResult('请先登录', 'error'); return; }

  const jobId = parseInt(batchNewJobSelect?.value, 10);
  if (!jobId) { showResult('请选择岗位', 'error'); return; }

  const limit = parseInt(batchNewLimit?.value, 10) || 10;
  if (limit < 1 || limit > 50) { showResult('采集数量须为 1-50', 'error'); return; }

  // read criteria from selected option dataset
  let criteria = null;
  const selOpt = batchNewJobSelect?.selectedOptions?.[0];
  try { criteria = JSON.parse(selOpt?.dataset?.criteria || 'null'); } catch { criteria = null; }

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.includes('zhipin.com')) {
      showResult('请在Boss直聘页面使用此功能', 'error'); return;
    }
    let pingResp;
    try { pingResp = await chrome.tabs.sendMessage(tab.id, { action: 'ping' }); } catch {
      showResult('请先刷新Boss直聘页面', 'error'); return;
    }
    if (!pingResp?.onMessagePage) {
      showResult('请先打开Boss直聘「消息」页面', 'error'); return;
    }
  } catch { showResult('请先刷新Boss直聘页面', 'error'); return; }

  showResult(`开始批量采集，目标 ${limit} 人，标准: ${JSON.stringify(criteria) || '无限制'}\n请勿操作Boss直聘页面！`, '');
  setButtonsDisabled(true);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, {
      action: 'batchCollectNew', limit, criteria, serverUrl: url, authToken: token,
    });
    if (!response?.success) {
      showResult(`采集失败: ${response?.message || '无响应，请刷新页面重试'}`, 'error');
      setButtonsDisabled(false); return;
    }
    const { collected, skippedDup, skippedCriteria, failed } = response;
    showResult(
      `✅ 采集完成\n成功入库: ${collected} 人\n跳过(已在库): ${skippedDup} 人\n跳过(不符标准): ${skippedCriteria} 人\n失败: ${failed} 人`,
      collected > 0 ? 'success' : ''
    );
  } catch (e) {
    showResult(`异常: ${e.message}`, 'error');
  } finally {
    setButtonsDisabled(false);
  }
}

// ── UI Helpers ──────────────────────────────────────────────────────

function setStatus(state) {
  statusDot.className = "status-dot";
  let txt = "未连接";
  switch (state) {
    case "connected": statusDot.classList.add("connected"); txt = "已连接"; break;
    case "error": statusDot.classList.add("error"); txt = "连接失败"; break;
    case "checking": txt = "检测中..."; break;
  }
  if (statusText) statusText.textContent = txt;
  if (statusTextFull) statusTextFull.textContent = txt;
}

// Messages about connection/health checks are ephemeral; don't persist them
// over the user's real operation result.
const _EPHEMERAL_MSG_PATTERNS = [
  /^正在检测连接/, /^已连接到/, /^连接失败/, /^无法连接/, /^已退出登录/,
];

function _isEphemeralMsg(message) {
  if (!message) return true;
  return _EPHEMERAL_MSG_PATTERNS.some((re) => re.test(message));
}

function showResult(message, type) {
  resultArea.textContent = message;
  resultArea.className = "result-area";
  if (type) resultArea.classList.add(type);

  // Auto-expand log card on error or any non-empty result; show tail hint
  if (logCard) {
    if (type === "error" || (type === "success" && message)) {
      logCard.classList.add("open");
    }
    if (logHint) {
      const firstLine = (message || "").split("\n")[0].slice(0, 40);
      logHint.textContent = firstLine ? `· ${firstLine}` : "";
    }
  }

  // Persist real operation results so popup close/reopen restores them
  if (!_isEphemeralMsg(message)) {
    try {
      chrome.storage.local.set({
        popupLastResult: { message: message || "", type: type || "", ts: Date.now() },
      });
    } catch {}
  }
}

function restoreLastResult() {
  chrome.storage.local.get(["popupLastResult"], (r) => {
    const d = r.popupLastResult;
    if (!d || !d.message) return;
    // Keep live errors visible (e.g. connection failed); otherwise restore last op result
    if (resultArea.classList.contains("error")) return;
    resultArea.textContent = d.message;
    resultArea.className = "result-area";
    if (d.type) resultArea.classList.add(d.type);
    if (logCard && (d.type === "error" || d.type === "success")) {
      logCard.classList.add("open");
    }
    if (logHint) {
      const firstLine = (d.message || "").split("\n")[0].slice(0, 40);
      logHint.textContent = firstLine ? `· ${firstLine}` : "";
    }
  });
}

const btnPause = document.getElementById("btnPause");
let isPaused = false;
let isRunning = false;

// 恢复上次状态（popup 关闭再打开时）
chrome.storage.local.get(["recruitRunning", "recruitPaused", "recruitStats", "recruitLog"], (data) => {
  isRunning = !!data.recruitRunning;
  isPaused = !!data.recruitPaused;
  updatePauseButton();
  // 恢复进度显示
  if (data.recruitStats) {
    const s = data.recruitStats;
    if (s.total !== undefined) {
      recruitStats.textContent = `进度: 总 ${s.total}, 打招呼 ${s.greeted||0}, 淘汰 ${s.rejected||0}, 跳过 ${s.skipped||0}, 失败 ${s.failed||0}`;
    }
  }
  if (isRunning) {
    showResult('F3 自动打招呼运行中，请勿操作 Boss 推荐牛人页...', '');
  }
});

// 监听 storage 变化（content.js 修改状态时实时更新）
chrome.storage.onChanged.addListener((changes) => {
  if (changes.recruitRunning) {
    isRunning = !!changes.recruitRunning.newValue;
    updatePauseButton();
  }
  if (changes.recruitPaused) {
    isPaused = !!changes.recruitPaused.newValue;
    updatePauseButton();
  }
});

function updatePauseButton() {
  if (isRunning) {
    btnPause.style.display = "block";
    if (isPaused) {
      btnPause.textContent = "继续（点击页面也可暂停）";
      btnPause.style.background = "#52c41a";
    } else {
      btnPause.textContent = "暂停";
      btnPause.style.background = "#ff4d4f";
    }
  } else {
    btnPause.style.display = "none";
  }
}

function setButtonsDisabled(disabled) {
  btnCollect.disabled = disabled;
  btnBatchCollect.disabled = disabled;
  btnTestConnection.disabled = disabled;
  btnAutoGreet.disabled = disabled;
  btnRecruitStart.disabled = disabled;
  if (btnBatchCollectNew) btnBatchCollectNew.disabled = disabled;

  if (disabled) {
    isRunning = true;
    isPaused = false;
    updatePauseButton();
  }
  // 不在这里隐藏按钮，让 storage 监听来控制
}

async function sendToContent(action) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) return chrome.tabs.sendMessage(tab.id, { action });
}

btnPause.addEventListener("click", async () => {
  if (!isRunning) return;

  if (!isPaused) {
    await sendToContent("pause");
    isPaused = true;
  } else {
    await sendToContent("resume");
    isPaused = false;
  }
  updatePauseButton();
});

// ── Auto Greet ──────────────────────────────────────────────────────

const btnAutoGreet = document.getElementById("btnAutoGreet");

async function autoGreet() {
  showResult("正在自动打招呼，请勿操作Boss直聘页面...\n将逐个点击候选人并发送求简历请求", "");
  setButtonsDisabled(true);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.includes("zhipin.com")) {
      showResult("请在Boss直聘页面上使用此功能", "error");
      setButtonsDisabled(false);
      return;
    }

    const response = await chrome.tabs.sendMessage(tab.id, { action: "autoGreet" });

    if (!response?.success) {
      showResult(`打招呼失败: ${response?.message || "无响应，请刷新页面重试"}`, "error");
      setButtonsDisabled(false);
      return;
    }

    const summary = response.summary || {};
    const results = response.data || [];
    const lines = [
      `自动打招呼完成`,
      `总计: ${summary.total || results.length} 人`,
      `已求简历: ${summary.greeted || 0}`,
      `跳过: ${summary.skipped || 0}`,
      `失败: ${summary.failed || 0}`,
      ``,
      `详情:`,
    ];
    results.forEach(r => {
      const statusText = r.status === 'greeted' ? '已求简历' : `跳过(${r.reason || ''})`;
      lines.push(`  ${r.name}: ${statusText}`);
    });

    if (response.log?.length) {
      lines.push('', '--- 调试日志 ---');
      response.log.forEach(l => lines.push(l));
    }
    showResult(lines.join("\n"), summary.failed === 0 ? "success" : "error");
  } catch (err) {
    showResult(`操作失败: ${err.message}`, "error");
  } finally {
    setButtonsDisabled(false);
  }
}

// ── Event Listeners ─────────────────────────────────────────────────

btnTestConnection.addEventListener("click", () => { saveServerUrl(); checkConnection(); });
btnAutoGreet.addEventListener("click", autoGreet);
btnCollect.addEventListener("click", collectCurrentResume);
btnBatchCollect.addEventListener("click", batchCollectFromList);
if (btnBatchCollectNew) btnBatchCollectNew.addEventListener('click', batchCollectNewFromList);
serverUrlInput.addEventListener("change", saveServerUrl);
// authToken is now managed via login/logout, no manual input needed

// ── F3 Recruit ──────────────────────────────────────────────────────

const recruitJobSelect = document.getElementById('recruitJobSelect');
const usageUsed = document.getElementById('usageUsed');
const usageCap = document.getElementById('usageCap');
const editCap = document.getElementById('editCap');
const btnRecruitStart = document.getElementById('btnRecruitStart');
const recruitStats = document.getElementById('recruitStats');

const jobTitlesMap = new Map(); // jobId(string) -> title

function stringSimilarity(a, b) {
  if (!a || !b) return 0;
  const tokens = s => {
    const set = new Set();
    const t = s.trim().toLowerCase();
    if (t.length === 0) return set;
    if (t.length === 1) { set.add(t); return set; }
    for (let i = 0; i < t.length - 1; i++) set.add(t.slice(i, i + 2));
    for (const ch of t) set.add(ch);
    return set;
  };
  const A = tokens(a), B = tokens(b);
  if (A.size === 0 || B.size === 0) return 0;
  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;
  const union = A.size + B.size - inter;
  return union === 0 ? 0 : inter / union;
}

async function loadJobs() {
  const url = getServerUrl();
  const token = getAuthToken();
  if (!token) return;
  try {
    const r = await fetch(`${url}/api/screening/jobs?active_only=true`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) return;
    const data = await r.json();
    recruitJobSelect.innerHTML = '<option value="">-- 选择岗位 --</option>';
    const jobs = Array.isArray(data) ? data : (data.items || []);
    jobs.forEach(j => {
      if (j.competency_model_status !== 'approved') return;
      jobTitlesMap.set(String(j.id), j.title || '');
      const opt = document.createElement('option');
      opt.value = j.id;
      opt.textContent = `${j.title} (阈值 ${j.greet_threshold || 60})`;
      recruitJobSelect.appendChild(opt);
    });
  } catch (e) {
    console.error('loadJobs fail', e);
  }
}

async function loadBatchJobs() {
  const url = getServerUrl();
  const token = getAuthToken();
  if (!token || !batchNewJobSelect) return;
  try {
    const r = await fetch(`${url}/api/screening/jobs?active_only=true`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) return;
    const data = await r.json();
    batchNewJobSelect.innerHTML = '<option value="">-- 选择岗位 --</option>';
    const jobs = Array.isArray(data) ? data : (data.items || []);
    jobs.forEach(j => {
      const opt = document.createElement('option');
      opt.value = j.id;
      opt.dataset.criteria = JSON.stringify(j.batch_collect_criteria || null);
      opt.textContent = j.title || `岗位${j.id}`;
      batchNewJobSelect.appendChild(opt);
    });
  } catch (e) {
    console.error('loadBatchJobs fail', e);
  }
}

async function loadDailyUsage() {
  const url = getServerUrl();
  const token = getAuthToken();
  if (!token) return;
  try {
    const r = await fetch(`${url}/api/recruit/daily-usage`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) return;
    const d = await r.json();
    usageUsed.textContent = d.used;
    usageCap.textContent = d.cap;
  } catch (e) {
    console.error('loadDailyUsage fail', e);
  }
}

async function editDailyCap() {
  const url = getServerUrl();
  const token = getAuthToken();
  const newCap = prompt('输入新的每日配额 (0-10000)', usageCap.textContent);
  if (newCap === null) return;
  const n = parseInt(newCap, 10);
  if (!(n >= 0 && n <= 10000)) {
    showResult('配额必须 0-10000', 'error'); return;
  }
  try {
    const r = await fetch(`${url}/api/recruit/daily-cap`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ cap: n }),
    });
    if (r.ok) { await loadDailyUsage(); showResult(`配额已改为 ${n}`, 'success'); }
    else { showResult(`修改失败: HTTP ${r.status}`, 'error'); }
  } catch (e) {
    showResult(`网络错: ${e.message}`, 'error');
  }
}

async function startAutoRecruit() {
  const url = getServerUrl();
  const token = getAuthToken();
  if (!token) { showResult('请先登录', 'error'); return; }
  const jobId = parseInt(recruitJobSelect.value, 10);
  if (!jobId) { showResult('请选择岗位', 'error'); return; }

  recruitStats.textContent = '';
  showResult('F3 自动打招呼已启动，请勿操作 Boss 推荐牛人页...', '');
  setButtonsDisabled(true);

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.includes('zhipin.com/web/chat/recommend')) {
      showResult('请先打开 Boss 推荐牛人页', 'error');
      setButtonsDisabled(false); return;
    }

    // Pre-flight: verify content script is injected
    try {
      await chrome.tabs.sendMessage(tab.id, { action: 'ping' });
    } catch (e) {
      showResult('请先刷新 Boss 推荐牛人页（插件需要页面刷新后才能工作）', 'error');
      setButtonsDisabled(false); return;
    }

    // 岗位匹配确认 — 在 popup 侧做，避免 content script confirm() 关闭 popup
    try {
      const pageInfo = await chrome.tabs.sendMessage(tab.id, { action: 'getPageInfo' });
      const bossTitle = pageInfo?.bossJobTitle || '';
      const sysTitle = jobTitlesMap.get(String(jobId)) || '';
      if (bossTitle && sysTitle && stringSimilarity(sysTitle, bossTitle) < 0.7) {
        const ok = confirm(`岗位可能不匹配:\n  Boss 页: ${bossTitle}\n  系统选的: ${sysTitle}\n继续?`);
        if (!ok) { showResult('已取消', ''); setButtonsDisabled(false); return; }
      }
    } catch (e) {
      // getPageInfo 失败不阻断流程，content.js 会记 log
    }

    const resp = await chrome.tabs.sendMessage(tab.id, {
      action: 'autoGreetRecommend',
      jobId, serverUrl: url, authToken: token,
    });

    if (resp?.success) {
      const s = resp.summary;
      showResult(
        [
          'F3 自动打招呼完成',
          `总 ${s.total} 人: 打招呼 ${s.greeted}, 跳过 ${s.skipped}, 淘汰 ${s.rejected}, 失败 ${s.failed}`,
        ].join('\n'),
        'success'
      );
    } else {
      const s = resp?.summary;
      showResult(
        [
          resp?.message || '未知错误',
          s ? `进度: 总 ${s.total}, 成 ${s.greeted}, 淘 ${s.rejected}, 失 ${s.failed}` : '',
        ].filter(Boolean).join('\n'),
        'error'
      );
    }
    await loadDailyUsage();
  } catch (e) {
    showResult(`异常: ${e.message}`, 'error');
  } finally {
    setButtonsDisabled(false);
  }
}

chrome.storage.onChanged.addListener((changes) => {
  if (changes.recruitStats) {
    const s = changes.recruitStats.newValue || {};
    if (s.total !== undefined) {
      recruitStats.textContent = `进度: 总 ${s.total}, 成 ${s.greeted||0}, 淘 ${s.rejected||0}, 跳 ${s.skipped||0}, 失 ${s.failed||0}`;
    }
  }
});

editCap.addEventListener('click', (e) => { e.preventDefault(); editDailyCap(); });
btnRecruitStart.addEventListener('click', startAutoRecruit);

document.getElementById("btnCollectSingleChat").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url?.includes("zhipin.com/web/chat")) {
    showResult("请先在 Boss 直聘聊天页打开候选人对话", "error");
    return;
  }
  showResult("正在采集...");
  chrome.tabs.sendMessage(tab.id, { type: "INTAKE_COLLECT_CURRENT_CHAT" }, (resp) => {
    if (chrome.runtime.lastError) {
      showResult(`失败: ${chrome.runtime.lastError.message}`, "error");
    } else if (resp?.ok) {
      showResult("已触发采集，查看页面右上角提示", "success");
    } else {
      showResult(`采集失败: ${resp?.error || "未知错误"}`, "error");
    }
  });
});

// === F3 学历门槛卡片 ===========================================
// HR 在 popup 配置「最低学历 + 名校标签 + 是否必须名校」, 持久化到
// chrome.storage.local 的 HR_EDUCATION_FILTER 键; content.js 在
// autoGreetRecommend 启动循环时读取并放入 POST body 的 education_filter。
(function initEducationFilter() {
  const STORAGE_KEY = 'HR_EDUCATION_FILTER';
  const DEFAULT_FILTER = {
    min_level: '本科',
    prestigious_tags: [],
    require_prestigious: false,
  };
  const $level = document.getElementById('edu-min-level');
  const $tags = document.querySelectorAll('#edu-tags input[type=checkbox]');
  const $require = document.getElementById('edu-require');
  const $save = document.getElementById('edu-save');
  const $hint = document.getElementById('edu-save-hint');
  if (!$level || !$save) return;

  function applyToUI(f) {
    $level.value = f.min_level || '本科';
    $tags.forEach(cb => { cb.checked = (f.prestigious_tags || []).includes(cb.value); });
    $require.checked = !!f.require_prestigious;
  }

  function readFromUI() {
    return {
      min_level: $level.value,
      prestigious_tags: Array.from($tags).filter(c => c.checked).map(c => c.value),
      require_prestigious: !!$require.checked,
    };
  }

  chrome.storage.local.get([STORAGE_KEY], (res) => {
    applyToUI(res[STORAGE_KEY] || DEFAULT_FILTER);
  });

  $save.addEventListener('click', () => {
    const f = readFromUI();
    if (f.require_prestigious && f.prestigious_tags.length === 0) {
      $hint.textContent = '勾了"必须名校"必须至少选 1 个名校标签';
      $hint.style.color = '#ff4d4f';
      return;
    }
    chrome.storage.local.set({ [STORAGE_KEY]: f }, () => {
      $hint.textContent = '已保存 ' + new Date().toLocaleTimeString();
      $hint.style.color = '#00b38a';
    });
  });
})();
