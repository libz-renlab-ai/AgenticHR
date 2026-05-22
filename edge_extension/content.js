// ────────────────────────────────────────────────
// 学校层次常量（教育部名单，含官方简称）
// ────────────────────────────────────────────────
const SCHOOL_985 = new Set([
  '北京大学','清华大学','中国人民大学','北京航空航天大学','北京理工大学',
  '中国农业大学','北京师范大学','中央民族大学','南开大学','天津大学',
  '大连理工大学','吉林大学','哈尔滨工业大学','复旦大学','同济大学',
  '上海交通大学','华东师范大学','南京大学','东南大学','浙江大学',
  '中国科学技术大学','厦门大学','山东大学','中国海洋大学','武汉大学',
  '华中科技大学','中南大学','中山大学','华南理工大学','四川大学',
  '重庆大学','电子科技大学','西安交通大学','西北工业大学','兰州大学',
  '国防科技大学','中国科学院大学','东北大学','湖南大学',
]);

// 211 包含 985
const SCHOOL_211 = new Set([
  ...SCHOOL_985,
  '北京交通大学','北京工业大学','北京科技大学','北京化工大学',
  '北京邮电大学','北京林业大学','北京中医药大学','中央音乐学院',
  '对外经济贸易大学','中国政法大学','华北电力大学','中国矿业大学',
  '河海大学','江南大学','南京农业大学','中国药科大学','南京航空航天大学',
  '南京理工大学','苏州大学','东北财经大学','大连海事大学','延边大学',
  '东北林业大学','东北农业大学','华东理工大学','东华大学','上海大学',
  '上海外国语大学','上海财经大学','合肥工业大学','中国地质大学',
  '武汉理工大学','华中农业大学','华中师范大学','中南财经政法大学',
  '湖南师范大学','暨南大学','华南师范大学','广西大学','海南大学',
  '西南大学','西南交通大学','西南财经大学','四川农业大学','贵州大学',
  '云南大学','西藏大学','西北农林科技大学','陕西师范大学','长安大学',
  '新疆大学','石河子大学','宁夏大学','青海大学','内蒙古大学',
  '太原理工大学','河北工业大学','燕山大学','山西大学',
  '郑州大学','安徽大学','南昌大学','福州大学',
]);

// 双一流学科高校（部分非211）
const SCHOOL_FIRST_CLASS = new Set([
  ...SCHOOL_211,
  '北京协和医学院','外交学院','中央财经大学','北京外国语大学',
  '华南农业大学','广州医科大学','南方科技大学','上海科技大学',
  '深圳大学','西湖大学',
]);

/**
 * 招聘助手 - Content Script
 * Boss 直聘 (zhipin.com/web/chat/index)
 *
 * 核心设计：每次切换候选人后，等待 name-box 和聊天消息区全部稳定后再操作。
 * PDF 下载前验证 PDF 卡片标题已更新，确保下载的是当前候选人的简历。
 */

const LOG = [];
let _paused = false;
let _stopped = false;
let _running = false;

function log(msg) { LOG.push(`[${new Date().toLocaleTimeString()}] ${msg}`); console.log('[招聘助手]', msg); }
async function waitIfPaused() { while (_paused && !_stopped) await sleep(300); if (_stopped) throw new Error('已停止'); }

function _setRunning(val) {
  _running = val;
  chrome.storage.local.set({ recruitRunning: val, recruitPaused: false });
}
function _setPaused(val) {
  _paused = val;
  chrome.storage.local.set({ recruitPaused: val });
}

// 点击页面任意位置暂停/继续（仅在运行中有效）
document.addEventListener('click', (e) => {
  if (!_running) return;
  // 忽略插件自己触发的点击（自动化操作）
  if (e.isTrusted && !e._fromPlugin) {
    if (!_paused) {
      _setPaused(true);
      log('用户点击页面，已暂停');
    }
  }
}, true);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const h = {
    collectCurrentResume: () => collectSingle(message.serverUrl, message.authToken || ''),
    batchCollect: () => batchCollect(message.serverUrl, message.authToken || ''),
    batchCollectNew: (msg) => batchCollectNew(
      message.limit, message.criteria, message.serverUrl, message.authToken || ''
    ),
    autoGreet: () => autoGreet(),
    autoGreetRecommend: () => autoGreetRecommend({
      jobId: message.jobId,
      serverUrl: message.serverUrl,
      authToken: message.authToken || '',
    }),
  };
  if (h[message.action]) {
    h[message.action]().then(r => sendResponse(r)).catch(e => sendResponse({ success: false, message: e.message, log: LOG }));
    return true;
  }
  if (message.action === 'ping') {
    const onMessagePage = !!document.querySelector('.geek-item') || window.location.href.includes('/web/geek/chat')
    sendResponse({ ok: true, onMessagePage })
    return true
  }
  if (message.action === 'getPageInfo') {
    sendResponse({ bossJobTitle: getBossTopJobTitle() })
    return true
  }
  if (message && message.type === 'INTAKE_COLLECT_CURRENT_CHAT') {
    // Manual single-chat trigger: if PDF not yet received, actively click 求简历.
    intake_runOrchestrator({ forceRequestPdfIfMissing: true })
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  if (message.action === 'pause') { _setPaused(true); sendResponse({ success: true }); }
  else if (message.action === 'resume') { _setPaused(false); sendResponse({ success: true }); }
  else if (message.action === 'stop') { _stopped = true; _setPaused(false); _setRunning(false); sendResponse({ success: true }); }
  else if (message.action === 'getStatus') { sendResponse({ running: _running, paused: _paused }); }
  else if (message.action === 'switchTab') { switchToTab(message.tabName); sendResponse({ success: true }); }
  else sendResponse({ success: false, message: '未知操作' });
  return false;
});

// ════════════════════════════════════════════════════════════════════
// 批量采集
// ════════════════════════════════════════════════════════════════════

async function batchCollect(serverUrl, authToken = '') {
  LOG.length = 0; _paused = false; _stopped = false; _setRunning(true);
  const items = document.querySelectorAll('.geek-item');
  if (!items.length) return { success: false, message: '未找到候选人列表' };

  log(`共 ${items.length} 个候选人`);
  const results = [];
  let created = 0, updated = 0, failed = 0;

  // 记录"上一个候选人"的状态用于检测变化
  let prevName = '';
  let prevPdfTitle = '';

  for (let i = 0; i < items.length; i++) {
    await waitIfPaused();
    const item = items[i];
    const listName = item.querySelector('.geek-name')?.textContent?.trim() || '';
    if (!listName) continue;
    log(`\n── [${i+1}/${items.length}] ${listName} ──`);

    // ① 点击候选人
    item.click();

    // ② 等面板完全切换：name-box 必须变为当前候选人
    if (!await waitForNameBox(listName, 6000)) {
      log(`面板未切换，跳过`);
      results.push({ name: listName, status: 'skip' }); failed++; continue;
    }

    // ③ 等聊天消息区加载完成
    // 关键：不能只等固定时间，要等 PDF 卡片标题变化（说明消息区已更新）
    await waitForChatUpdate(prevPdfTitle, 4000);
    // 额外等一下确保稳定
    await sleep(500);

    // ④ 读取当前状态（从正确的 PDF 卡片获取标题）
    const pdfCardInfo = findPdfCard();
    const hasPdf = !!pdfCardInfo;
    const currentPdfTitle = pdfCardInfo?.card.querySelector('.message-card-top-title')?.textContent?.trim() || '';

    // ⑤ 提取页面信息
    const detail = extractDetail();
    detail.boss_id = item.getAttribute('data-id') || '';
    supplementFromPushText(detail, item);

    // 二次验证：name-box 仍然是当前候选人（防止异步更新覆盖）
    const nameNow = document.querySelector('.name-box')?.textContent?.trim() || '';
    if (nameNow !== listName) {
      log(`二次验证失败: name-box="${nameNow}", 期望="${listName}"`);
      results.push({ name: listName, status: 'name_mismatch' }); failed++; continue;
    }

    log(`信息: 手机=${detail.phone||'无'} 学历=${detail.education||'无'} PDF=${hasPdf?currentPdfTitle.substring(0,30):'无'}`);

    // ⑥ 下载 PDF
    let method = 'page_only';
    if (hasPdf && currentPdfTitle && serverUrl) {
      const pdfResult = await downloadPdf(detail, listName, serverUrl, authToken);
      if (pdfResult.ok) {
        method = 'pdf_uploaded';
        created++;
        prevPdfTitle = currentPdfTitle;
        prevName = listName;
        results.push({ name: listName, status: method });
        await sleep(1500);
        continue;
      }
      log('PDF下载失败，退回页面数据');
    }

    // 提交页面数据
    try {
      const resp = await submitPageData(detail, serverUrl, authToken);
      if (resp.ok) { created++; method = 'page_created'; }
      else { updated++; method = 'duplicate'; }
    } catch { failed++; method = 'error'; }

    prevPdfTitle = currentPdfTitle;
    prevName = listName;
    results.push({ name: listName, status: method });
    await sleep(800);
  }

  _setRunning(false);
  return { success: true, data: results, summary: { total: results.length, created, updated, failed }, log: LOG };
}

function findScrollableAncestor(el) {
  let node = el?.parentElement;
  while (node) {
    const s = getComputedStyle(node);
    if (/auto|scroll/.test(s.overflowY) && node.scrollHeight > node.clientHeight) return node;
    node = node.parentElement;
  }
  return document.scrollingElement || document.body;
}

// 多方式触发列表加载更多：容器 scrollTop + 最后项 scrollIntoView + window 兜底
async function triggerListLoadMore(scrollable, beforeCount) {
  if (scrollable) {
    scrollable.scrollTop = scrollable.scrollHeight;
  }
  const all = document.querySelectorAll('.geek-item');
  if (all.length) {
    try { all[all.length - 1].scrollIntoView({ block: 'end', behavior: 'instant' }); }
    catch { all[all.length - 1].scrollIntoView(false); }
  }
  try { window.scrollTo(0, document.body.scrollHeight); } catch {}

  let waited = 0;
  while (waited < 8000 && document.querySelectorAll('.geek-item').length === beforeCount) {
    await sleep(500); waited += 500;
  }
  return document.querySelectorAll('.geek-item').length;
}

async function batchCollectNew(limit, criteria, serverUrl, authToken = '') {
  LOG.length = 0; _setRunning(true);
  window.__intakeBatchInProgress = true;  // 互斥：阻 outbox dispatch 抢 DOM
  let items = document.querySelectorAll('.geek-item');
  if (!items.length) {
    _setRunning(false); window.__intakeBatchInProgress = false;
    return { success: false, message: '未找到候选人列表' };
  }

  const scrollable = findScrollableAncestor(items[0]);
  log(`消息列表当前 ${items.length} 人，目标采集 ${limit} 人 (scroller=${scrollable?.className || 'body'})`);

  const processed = new Set();
  let collected = 0, skippedCriteria = 0, skippedDup = 0, failed = 0;
  let prevPdfTitle = '';

  try {
  outer: while (collected < limit) {
    items = document.querySelectorAll('.geek-item');
    const fresh = [...items].filter(el => {
      const id = el.getAttribute('data-id');
      return id && !processed.has(id);
    });

    if (!fresh.length) {
      // 触底：强化滚动加载
      const beforeCount = items.length;
      log(`触底 (已扫 ${beforeCount}, 入 ${collected}/${limit})，加载更多...`);
      const after = await triggerListLoadMore(scrollable, beforeCount);
      log(`滚动后共 ${after} 人 (+${after - beforeCount})`);
      if (after === beforeCount) {
        log(`列表到底，无新条目`);
        break;
      }
      continue;
    }

    // 批量去重
    const ids = fresh.map(el => el.getAttribute('data-id'));
    const existingSet = await checkBossIds(ids, serverUrl, authToken);

    for (const item of fresh) {
      if (collected >= limit) break outer;
      const bossId = item.getAttribute('data-id') || '';
      processed.add(bossId);
      if (existingSet.has(bossId)) { skippedDup++; continue; }

      const listName = item.querySelector('.geek-name')?.textContent?.trim() || '';
      if (!listName) continue;
      log(`\n── [已扫 ${processed.size}, 入 ${collected}/${limit}] ${listName} ──`);

      item.click();
      if (!await waitForNameBox(listName, 6000)) {
        log('面板未切换，跳过'); failed++; continue;
      }
      await waitForChatUpdate(prevPdfTitle, 4000);
      await sleep(500);

      const detail = extractDetail();
      detail.boss_id = bossId;
      supplementFromPushText(detail, item);
      const schoolTier = extractSchoolTier();
      log(`学历=${detail.education} 学校层次=${schoolTier}`);

      if (!matchesCriteria(detail, schoolTier, criteria)) {
        log(`跳过: 不符标准`); skippedCriteria++; continue;
      }

      // PDF 检测 + 上传拿服务器真实 path
      let realPdfPath = null;
      const pdfCard = findPdfCard();
      const pdfPresent = !!pdfCard;
      const pdfTitle = pdfCard?.card.querySelector('.message-card-top-title')?.textContent?.trim() || '';
      if (pdfPresent) {
        const dl = await downloadPdf({ ...detail, source: 'batch_chat' }, listName, serverUrl, authToken);
        if (dl && dl.ok && dl.data) {
          realPdfPath = dl.data.pdf_path || null;
          if (pdfTitle) prevPdfTitle = pdfTitle;
        } else {
          log('PDF 上传失败，仍按页面数据入库');
        }
      }

      // 进 intake 流水线（Step1 仅注册候选人，Step2 inline 路径负责后续发消息）
      try {
        const headers = { 'Content-Type': 'application/json' };
        if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
        const resp = await fetch(`${serverUrl}/api/intake/collect-chat`, {
          method: 'POST', headers,
          body: JSON.stringify({
            boss_id: bossId,
            name: detail.name || listName,
            job_intention: detail.job_intention || '',
            messages: [],
            pdf_present: pdfPresent,
            pdf_url: realPdfPath || null,
            skip_outbox: true,
          }),
        });
        if (resp.ok) {
          collected++;
          log(`入 intake 成功 (${collected}/${limit})`);
        } else {
          failed++; log(`入库失败 HTTP ${resp.status}`);
        }
      } catch (e) {
        failed++; log(`入库异常: ${e.message}`);
      }
      await sleep(1000);
    }
  }
  } finally {
    _setRunning(false);
    window.__intakeBatchInProgress = false;
  }
  return {
    success: true, collected, skippedDup, skippedCriteria, failed,
    message: `采集 ${collected}/${limit} 人完成`,
  };
}

// ════════════════════════════════════════════════════════════════════
// 等待聊天消息区更新（PDF 标题变化 或 消息区内容变化）
// ════════════════════════════════════════════════════════════════════

async function waitForChatUpdate(prevPdfTitle, timeout) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const curPdf = findPdfCard();
    const currentTitle = curPdf?.card.querySelector('.message-card-top-title')?.textContent?.trim() || '';
    // 如果 PDF 标题变了（或上一个没有 PDF 而现在有了），说明消息区已更新
    if (currentTitle && currentTitle !== prevPdfTitle) {
      log(`消息区已更新 (PDF标题变化, ${((Date.now()-start)/1000).toFixed(1)}秒)`);
      return;
    }
    // 如果上一个有 PDF 但当前没有，也说明切换了
    if (prevPdfTitle && !findPdfCard()) {
      log(`消息区已更新 (PDF卡片消失, ${((Date.now()-start)/1000).toFixed(1)}秒)`);
      return;
    }
    await sleep(200);
  }
  // 超时也继续，靠后续的固定等待
  log(`消息区等待超时，继续 (prevPdf="${prevPdfTitle?.substring(0,20)||'无'}")`);
}

// ════════════════════════════════════════════════════════════════════
// 等待面板姓名
// ════════════════════════════════════════════════════════════════════

async function waitForNameBox(expected, timeout) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const name = document.querySelector('.name-box')?.textContent?.trim();
    if (name === expected) return true;
    await sleep(150);
  }
  const actual = document.querySelector('.name-box')?.textContent?.trim() || '(空)';
  log(`name-box: "${actual}", 期望: "${expected}"`);
  return actual === expected;
}

// ════════════════════════════════════════════════════════════════════
// 单个采集
// ════════════════════════════════════════════════════════════════════

async function collectSingle(serverUrl, authToken = '', source = 'boss_zhipin') {
  LOG.length = 0;
  if (document.querySelector('.conversation-no-data'))
    return { success: false, message: '未选中联系人' };

  const detail = extractDetail();
  detail.boss_id = document.querySelector('.geek-item.selected')?.getAttribute('data-id') || '';
  supplementFromPushText(detail, document.querySelector('.geek-item.selected'));
  if (!detail.name) return { success: false, message: '无法获取候选人姓名' };

  if (findPdfCard() && serverUrl) {
    const result = await downloadPdf({ ...detail, source }, detail.name, serverUrl, authToken);
    if (result.ok) return { success: true, data: result.data, method: 'pdf_uploaded', log: LOG };
  }

  const resp = await submitPageData(detail, serverUrl, authToken, source);
  if (resp.ok) return { success: true, data: await resp.json(), method: 'page_only', log: LOG };
  return { success: true, data: detail, method: 'page_only', log: LOG };
}

// ════════════════════════════════════════════════════════════════════
// PDF 下载（带验证）
// ════════════════════════════════════════════════════════════════════

async function downloadPdf(candidateInfo, expectedName, serverUrl, authToken = '') {
  try {
    // 1. 下载前再次确认 name-box 是当前候选人
    const nameNow = document.querySelector('.name-box')?.textContent?.trim() || '';
    if (nameNow !== expectedName) {
      log(`PDF下载前验证失败: name-box="${nameNow}", 期望="${expectedName}"`);
      return { ok: false };
    }

    // 2. 关闭残留预览，然后强制删除所有 PDF iframe（确保干净状态）
    await closeDialog();
    await sleep(500);
    document.querySelectorAll('.attachment-iframe, iframe[src*="pdf"], iframe[src*="wflow"]').forEach(el => {
      log('删除残留iframe');
      el.remove();
    });
    await sleep(300);

    // 3. 确认页面上没有 PDF iframe 了
    if (document.querySelector('.attachment-iframe, iframe[src*="pdf"], iframe[src*="wflow"]')) {
      log('无法清除旧iframe，跳过');
      return { ok: false };
    }

    // 4. 找到真正的简历 PDF 卡片（跳过"同意附件"等系统卡片）
    const pdfInfo = findPdfCard();
    if (!pdfInfo) { log('未找到简历PDF卡片'); return { ok: false }; }
    const btn = pdfInfo.btn;
    log('点击预览...');
    btn.click();

    // 5. 等待全新的 iframe 出现（之前的已删除，新出现的一定是当前候选人的）
    let iframe = null;
    const t0 = Date.now();
    // 慢网下 PDF iframe 加载慢 — 给到 20s，与本文件其它网络等待量级一致
    while (Date.now() - t0 < 20000) {
      await sleep(400);
      iframe = document.querySelector('.attachment-iframe, iframe[src*="pdf"], iframe[src*="wflow"]');
      if (iframe && (iframe.getAttribute('src') || '').length > 30) break;
      iframe = null;
    }
    if (!iframe) {
      log(`iframe超时 (${((Date.now()-t0)/1000).toFixed(1)}秒)`);
      await closeDialog();
      return { ok: false };
    }
    log(`新iframe出现 (${((Date.now()-t0)/1000).toFixed(1)}秒)`);

    // 6. 第三次确认 name-box
    const nameAfter = document.querySelector('.name-box')?.textContent?.trim() || '';
    if (nameAfter !== expectedName) {
      log(`预览后验证失败: "${nameAfter}" != "${expectedName}"`);
      await closeDialog();
      return { ok: false };
    }

    // 7. 提取 PDF URL
    const src = iframe.getAttribute('src') || '';
    const pdfUrl = extractPdfUrl(src);
    if (!pdfUrl) { log('无法提取URL'); await closeDialog(); return { ok: false }; }
    log(`URL: ${pdfUrl.substring(0, 60)}...`);

    // 8. 关闭预览
    await closeDialog();
    await sleep(400);

    // 8. 下载 — BOSS直聘 卡顿时下载会失败/超时，重试 3 次再放弃
    const fullUrl = pdfUrl.startsWith('http') ? pdfUrl : `https://www.zhipin.com${pdfUrl}`;
    const blob = await intake_retry(async () => {
      const resp = await fetch(fullUrl, { credentials: 'include' });
      if (!resp.ok) { log(`下载失败: ${resp.status}`); return null; }
      const b = await resp.blob();
      if (b.size < 1024) { log(`文件太小 (${b.size} bytes)`); return null; }
      return b;
    }, { tries: 3, delayMs: 1000, label: 'PDF下载' });
    if (!blob) { return { ok: false }; }
    log(`${blob.size} bytes`);

    // 9. 上传
    const form = new FormData();
    let fileName = candidateInfo.pdf_filename || `${candidateInfo.name}.pdf`;
    if (!fileName.toLowerCase().endsWith('.pdf')) fileName += '.pdf';
    form.append('file', blob, fileName);
    form.append('candidate_name', candidateInfo.name || '');
    form.append('candidate_phone', candidateInfo.phone || '');
    form.append('candidate_email', candidateInfo.email || '');
    form.append('candidate_education', candidateInfo.education || '');
    form.append('candidate_work_years', String(candidateInfo.work_years || 0));
    form.append('candidate_job', candidateInfo.job_intention || '');
    if (candidateInfo.boss_id) form.append('candidate_boss_id', candidateInfo.boss_id);
    if (candidateInfo.source) form.append('candidate_source', candidateInfo.source);

    const uploadHeaders = authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
    const uploadResp = await fetch(`${serverUrl}/api/resumes/upload`, { method: 'POST', headers: uploadHeaders, body: form });
    // 上传成功 / 失败两条路径都要 closeDialog, 确保下一候选人开始前 PDF 预览不残留
    // (现网报告 2026-05-18: 上一人简历没关上, 已开始给下一人发消息)
    if (uploadResp.ok) {
      log('上传成功');
      const data = await uploadResp.json();
      await closeDialog();
      return { ok: true, data };
    }
    log(`上传失败: ${uploadResp.status}`);
    await closeDialog();
    return { ok: false };
  } catch (e) {
    log(`异常: ${e.message}`);
    await closeDialog();
    return { ok: false };
  }
}


function extractPdfUrl(iframeSrc) {
  try { const u = new URL(iframeSrc, 'https://www.zhipin.com'); const p = u.searchParams.get('url'); if (p) return p; } catch {}
  const m = iframeSrc.match(/url=([^&]+)/); return m ? decodeURIComponent(m[1]) : null;
}

async function closeDialog() {
  // 关闭简历预览弹窗 / PDF iframe。三层兜底, 函数返回前**保证** DOM 中
  // 已无 .attachment-iframe / .resume-common-dialog 残留, 避免下一个候选人
  // 被处理时上一个的简历预览还盖在聊天面板上 (现网报告 2026-05-18)。
  const closeSelectors = [
    '.resume-custom-close',
    '.resume-common-dialog .boss-popup__close',
    '.boss-popup__close',
    '.dialog-resume-full .close',
    '.icon-close',
  ];
  const residualSelector = '.attachment-iframe, iframe[src*="pdf"], iframe[src*="wflow"], .resume-common-dialog, .dialog-resume-full';

  function clickFirstClose() {
    for (const sel of closeSelectors) {
      const btn = document.querySelector(sel);
      if (btn && btn.offsetParent !== null) {
        btn.click();
        return sel;
      }
    }
    return null;
  }

  // 第 1 轮: click 关闭按钮 + 等弹窗消失
  const sel1 = clickFirstClose();
  if (sel1) log(`关闭弹窗: ${sel1}`);
  await sleep(300);

  // 第 2 轮: 如果还在, 再 click
  if (document.querySelector(residualSelector)) {
    clickFirstClose();
    await sleep(300);
  }

  // 第 3 轮: Escape 键
  if (document.querySelector(residualSelector)) {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
    await sleep(300);
  }

  // 终极兜底: 直接 DOM remove (avoids leftover stacking on next candidate)
  const residuals = document.querySelectorAll(residualSelector);
  if (residuals.length > 0) {
    log(`兜底删除 ${residuals.length} 个残留弹窗/iframe`);
    residuals.forEach((el) => {
      try { el.remove(); } catch (_) {}
    });
    await sleep(200);
  }
}

// ════════════════════════════════════════════════════════════════════
// 数据提取
// ════════════════════════════════════════════════════════════════════

function extractDetail() {
  const name = document.querySelector('.name-box')?.textContent?.trim() || '';
  let age = '', workYearsRaw = '', education = '';
  const box = document.querySelector('.base-info-single-detial');
  if (box) box.querySelectorAll(':scope > div').forEach(div => {
    if (div.classList.contains('active-time') || div.classList.contains('name-contet')) return;
    const t = div.textContent.trim(); if (!t) return;
    if (t.includes('岁')) age = t;
    else if (/博士|硕士|研究生|本科|学士|大专|专科|高中|中专|MBA/.test(t) && !t.includes('年')) education = t;
    else if (t.includes('应届') || t.includes('经验') || /^\d+年/.test(t)) workYearsRaw = t;
    else if (!age && /\d/.test(t)) age = t;
  });

  const job = document.querySelector('.position-content .position-name')?.textContent?.trim()
    || document.querySelector('.geek-item.selected .source-job')?.textContent?.trim() || '';
  const chatText = document.querySelector('.chat-message-list')?.textContent || '';
  const pdfCardResult = findPdfCard();
  const pdfTitle = pdfCardResult?.card.querySelector('.message-card-top-title')?.textContent?.trim() || '';
  const allText = chatText + ' ' + pdfTitle;

  return {
    name, phone: (allText.match(/1[3-9]\d{9}/)||[])[0] || '',
    email: (allText.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/)||[])[0] || '',
    education: normEdu(education), work_years: parseYr(workYearsRaw), job_intention: job,
    skills: '', source: 'boss_zhipin', pdf_filename: pdfTitle,
    work_experience: Array.from(document.querySelectorAll('.experience-content.detail-list .work-content .value')).map(el => el.textContent.trim()).join('\n'),
    raw_text: `年龄:${age} 工作:${workYearsRaw} 学历:${education} PDF:${pdfTitle}`,
  };
}

function supplementFromPushText(d, item) {
  if (!item) return;
  const msg = item.querySelector('.push-text')?.textContent?.trim() || '';
  if (!d.phone) { const m = msg.match(/1[3-9]\d{9}/); if (m) d.phone = m[0]; }
  if (!d.email) { const m = msg.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/); if (m) d.email = m[0]; }
}

function extractSchoolTier() {
  // Step 1: check tier tags in right panel
  const TIER_RE = /^985$|^211$|^双一流$|^\d+院校$/;
  const panel = document.querySelector('.geek-detail') || document.querySelector('.geek-sidebar') || document.body;
  const tagEls = panel.querySelectorAll('.tag-item, .tag, .edu-tag');
  for (const el of tagEls) {
    const txt = el.textContent.trim();
    if (TIER_RE.test(txt)) {
      if (/985/.test(txt)) return '985';
      if (/211/.test(txt)) return '211';
      if (/双一流/.test(txt)) return '双一流';
    }
  }
  // Step 2: text search for school names
  const panelText = (
    document.querySelector('.base-info-single-detial')?.textContent || ''
  ) + ' ' + (document.querySelector('.geek-header')?.textContent || '');
  for (const school of SCHOOL_985) {
    if (panelText.includes(school)) return '985';
  }
  for (const school of SCHOOL_211) {
    if (panelText.includes(school)) return '211';
  }
  for (const school of SCHOOL_FIRST_CLASS) {
    if (panelText.includes(school)) return '双一流';
  }
  return 'unknown';
}

function matchesCriteria(detail, schoolTier, criteria) {
  if (!criteria) return true;
  const EDU_ORDER = ['大专', '本科', '硕士', '博士'];
  if (criteria.education_min) {
    const minIdx = EDU_ORDER.indexOf(criteria.education_min);
    const detailIdx = EDU_ORDER.indexOf(detail.education);
    if (minIdx >= 0 && detailIdx >= 0 && detailIdx < minIdx) return false;
  }
  if (criteria.school_tiers && criteria.school_tiers.length > 0) {
    if (schoolTier === 'unknown') return true; // conservative pass
    const match = criteria.school_tiers.some(tier => {
      if (tier === '985') return schoolTier === '985';
      if (tier === '211') return schoolTier === '985' || schoolTier === '211';
      if (tier === '双一流') return schoolTier === '985' || schoolTier === '211' || schoolTier === '双一流';
      return false;
    });
    if (!match) return false;
  }
  return true;
}

async function checkBossIds(bossIds, serverUrl, authToken) {
  if (!bossIds.length || !serverUrl) return new Set();
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    const r = await fetch(`${serverUrl}/api/resumes/check-boss-ids`, {
      method: 'POST', headers,
      body: JSON.stringify({ boss_ids: bossIds }),
    });
    if (!r.ok) return new Set();
    const data = await r.json();
    return new Set(data.existing || []);
  } catch { return new Set(); }
}

async function submitPageData(d, url, authToken = '', source = 'boss_zhipin') {
  const headers = { 'Content-Type': 'application/json' };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  return fetch(`${url}/api/resumes/`, { method: 'POST', headers,
    body: JSON.stringify({ name: d.name, phone: d.phone||'', email: d.email||'', education: d.education||'',
      work_years: d.work_years||0, job_intention: d.job_intention||'', skills: '', work_experience: d.work_experience||'',
      source: source, raw_text: d.raw_text||'' }) });
}

// ════════════════════════════════════════════════════════════════════
// 自动打招呼
// ════════════════════════════════════════════════════════════════════

async function autoGreet() {
  LOG.length = 0; _paused = false; _stopped = false; _setRunning(true);
  if (!switchToTab('新招呼')) return { success: false, message: '未找到"新招呼"标签' };
  await sleep(1500);
  const items = document.querySelectorAll('.geek-item');
  if (!items.length) return { success: false, message: '新招呼列表为空' };

  log(`${items.length} 个新招呼`);
  const results = []; let greeted = 0, skipped = 0;
  for (let i = 0; i < items.length; i++) {
    await waitIfPaused();
    const name = items[i].querySelector('.geek-name')?.textContent?.trim() || `#${i+1}`;
    items[i].click(); await sleep(1500);
    if (!await waitForSel('.chat-conversation', 3000)) { skipped++; results.push({ name, status: 'skipped' }); continue; }
    await sleep(500);
    let ok = false;
    for (const btn of document.querySelectorAll('.operate-btn')) {
      if (btn.textContent.trim().includes('求简历')) {
        btn.click(); await sleep(800);
        const c = document.querySelector('.exchange-tooltip .boss-btn-primary'); if (c) { c.click(); await sleep(500); }
        ok = true; greeted++; results.push({ name, status: 'greeted' }); break;
      }
    }
    if (!ok) { skipped++; results.push({ name, status: 'skipped', reason: '无求简历按钮' }); }
    await sleep(2000 + Math.random() * 3000);
  }
  _setRunning(false);
  return { success: true, data: results, summary: { total: results.length, greeted, skipped, failed: 0 }, log: LOG };
}

// ════════════════════════════════════════════════════════════════════
// 工具
// ════════════════════════════════════════════════════════════════════

function findPdfCard() {
  // Real PDF card: boss-green with a "预览附件" button. Filter out accept/reject
  // prompt cards ("对方想发送附件简历给您，您是否同意") whose .card-btn are 拒绝/同意.
  const cards = document.querySelectorAll('.message-card-wrap.boss-green');
  for (let i = cards.length - 1; i >= 0; i--) {
    const title = (cards[i].querySelector('.message-card-top-title')?.textContent || '').trim();
    if (/您是否同意|拒绝发送|拒绝同意/.test(title)) continue;
    const btns = cards[i].querySelectorAll('.card-btn:not(.disabled)');
    for (const btn of btns) {
      const t = (btn.textContent || '').trim();
      if (/预览|查看|下载/.test(t)) return { card: cards[i], btn };
    }
  }
  return null;
}

function switchToTab(n) {
  const t = document.querySelector(`.chat-label-item[title*="${n}"]`);
  if (t) { t.click(); return true; }
  for (const el of document.querySelectorAll('.chat-label-item'))
    if ((el.querySelector('.content')?.textContent||'').includes(n)) { el.click(); return true; }
  return false;
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Bridge to MAIN world (main_world_bridge.js) — needed to access page-side
// Vue internals (el.__vue__) which are invisible from isolated content-script world.
function _bridgeCall(cmd, extras = {}) {
  return new Promise((resolve) => {
    const id = `br_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const handler = (e) => {
      if (e.source !== window || !e.data?.__intakeBridgeReply || e.data.id !== id) return;
      window.removeEventListener("message", handler);
      resolve(e.data);
    };
    window.addEventListener("message", handler);
    window.postMessage({ __intakeBridge: true, cmd, id, ...extras }, "*");
    // get_datasources scrolls full list (can take ~15s); other commands are fast
    const timeoutMs = cmd === "get_datasources" ? 30000 : 3000;
    setTimeout(() => { window.removeEventListener("message", handler); resolve(null); }, timeoutMs);
  });
}

function waitForSel(sel, timeout = 3000) {
  return new Promise(resolve => {
    if (document.querySelector(sel)) { resolve(true); return; }
    const s = Date.now();
    const iv = setInterval(() => { if (document.querySelector(sel) || Date.now()-s > timeout) { clearInterval(iv); resolve(!!document.querySelector(sel)); } }, 100);
  });
}
function parseYr(t) { if (!t) return 0; if (t.includes('应届')) return 0; const m = t.match(/(\d+)\s*年/); return m ? parseInt(m[1]) : 0; }
function normEdu(t) { if (!t) return ''; for (const [k,v] of Object.entries({'博士':'博士','硕士':'硕士','研究生':'硕士','本科':'本科','学士':'本科','大专':'大专','专科':'大专'})) if (t.includes(k)) return v; return t; }

// ════════════════════════════════════════════════════════════════════
// F3 工具 — 反检测人类式操作 (spec §7.2, §7.3)
// ════════════════════════════════════════════════════════════════════

/**
 * 人类式点击: scrollIntoView + mouseover → mousedown → mouseup → click
 * spec §7.2 反检测要求. 不直接 .click() 因为 isTrusted=false 更易被检出.
 */
async function simulateHumanClick(el) {
  if (!el) throw new Error('simulateHumanClick: element is null');
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  await sleep(300);

  const opts = { bubbles: true, cancelable: true, view: window, button: 0 };
  el.dispatchEvent(new MouseEvent('mouseover', opts));
  await sleep(150 + Math.random() * 100);
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  await sleep(50 + Math.random() * 50);
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('click', opts));
}

/**
 * 取推荐牛人 iframe 的 contentDocument. 推荐页结构:
 *   top frame: /web/chat/recommend (空壳)
 *   iframe[src*=/web/frame/recommend/]: 真正的卡片 + 岗位下拉所在处
 * 2026-04-21 live 探查证实.
 */
function _getRecommendDoc() {
  for (const f of document.querySelectorAll('iframe')) {
    try {
      const src = f.src || '';
      if (!src.includes(F3_SELECTORS.RECOMMEND_IFRAME_PATH)) continue;
      const doc = f.contentDocument;
      if (doc && doc.body) return doc;
    } catch (_) { /* cross-origin fallback */ }
  }
  // 回退: 任一 same-origin 且含 '打招呼' 文本的 iframe
  for (const f of document.querySelectorAll('iframe')) {
    try {
      const doc = f.contentDocument;
      if (doc && doc.body && doc.body.innerText.includes('打招呼')) return doc;
    } catch (_) {}
  }
  return null;
}

/**
 * 检测风控告警. 命中返 true + halt 主循环.
 * spec §7.3. 扫描 top frame 和推荐 iframe 两个 document.
 */
function detectRiskControl() {
  const riskSelectors = [
    F3_SELECTORS.RISK_CAPTCHA,
    F3_SELECTORS.RISK_VERIFY,
    F3_SELECTORS.RISK_ALERT,
    F3_SELECTORS.PAID_GREET_DIALOG,
  ];
  const docs = [document];
  const iframeDoc = _getRecommendDoc();
  if (iframeDoc) docs.push(iframeDoc);

  for (const doc of docs) {
    for (const sel of riskSelectors) {
      try {
        const el = doc.querySelector(sel);
        if (el && el.offsetParent !== null) {
          return { detected: true, source: `selector:${sel}` };
        }
      } catch (_) {}
    }
    const bodyText = doc.body?.innerText || '';
    for (const pattern of F3_SELECTORS.RISK_TEXT_PATTERNS) {
      if (bodyText.includes(pattern)) {
        return { detected: true, source: `text:${pattern}` };
      }
    }
  }
  return { detected: false };
}

/**
 * 从 Boss 推荐牛人 list 卡片抠字段. LIST-only (spec §5.2).
 * 返回 ScrapedCandidate-shaped plain object 或 null (信号 scrape 失败).
 */
function scrapeRecommendCard(cardEl) {
  if (!cardEl) return null;

  // boss_id: 优先 .card-inner[data-id]，再找任意 [data-id]，再试 data-geek-id
  const innerEl = cardEl.querySelector(F3_SELECTORS.CARD_INNER) || cardEl;
  let bossId = innerEl.getAttribute('data-id')
    || cardEl.getAttribute('data-id')
    || cardEl.querySelector('[data-id]')?.getAttribute('data-id')
    || innerEl.getAttribute('data-geekid')
    || innerEl.getAttribute('data-geek')
    || cardEl.getAttribute('data-geekid')
    || cardEl.getAttribute('data-geek')
    || cardEl.querySelector('[data-geekid]')?.getAttribute('data-geekid')
    || cardEl.querySelector('[data-geek]')?.getAttribute('data-geek')
    || innerEl.getAttribute('data-geek-id')
    || cardEl.getAttribute('data-geek-id')
    || cardEl.querySelector('[data-geek-id]')?.getAttribute('data-geek-id')
    || '';

  // name: 先 span.name，再 .name，再任意含中文的 .name-wrap 子元素
  const name = cardEl.querySelector(F3_SELECTORS.CARD_NAME)?.textContent?.trim()
    || cardEl.querySelector('.name')?.textContent?.trim()
    || cardEl.querySelector('.geek-name')?.textContent?.trim()
    || '';

  if (!name) {
    log(`scrape失败: 找不到name. HTML前300: ${cardEl.outerHTML?.slice(0,300)}`);
    return null;
  }

  // 无 ID 时用 name 作 fallback（避免全部跳过；重复由 Set 过滤）
  if (!bossId) {
    log(`scrape警告: 找不到data-id, 用name作fallback: ${name}`);
    bossId = `name_${name}`;
  }

  // base-info: "22岁 27年应届生 硕士 刚刚活跃"
  const baseText = (cardEl.querySelector(F3_SELECTORS.CARD_BASE_INFO)?.textContent || '')
    .replace(/\s+/g, ' ').trim();
  const age = parseInt(baseText.match(/(\d+)岁/)?.[1] || '', 10) || null;
  const gradMatch = baseText.match(/(\d{2})年(应届生|毕业)/);
  const gradYear = gradMatch ? (2000 + parseInt(gradMatch[1], 10)) : null;
  const eduMatch = baseText.match(/博士|硕士|研究生|本科|学士|大专|专科|高中|中专|MBA/);
  const education = normEdu(eduMatch?.[0] || '');
  const activeStatus =
    (baseText.match(/刚刚活跃|今日活跃|在线|\d+日内活跃|\d+小时前活跃/) || [''])[0];

  // expect-wrap .content: "北京 全栈工程师" — 空格分 (无 · 分隔). 末 token 视为岗位.
  const focusText = (cardEl.querySelector(F3_SELECTORS.CARD_RECENT_FOCUS)?.textContent || '')
    .replace(/\s+/g, ' ').trim();
  const focusTokens = focusText.split(' ').filter(Boolean);
  const intendedJob = focusTokens.length > 0 ? focusTokens[focusTokens.length - 1] : '';

  // edu-wrap .content: "北京交通大学 软件工程 硕士" — 空格分 (首=学校 / 中=专业 / 末=学位)
  const eduRow = (cardEl.querySelector(F3_SELECTORS.CARD_EDUCATION_ROW)?.textContent || '')
    .replace(/\s+/g, ' ').trim();
  const eduParts = eduRow.split(' ').filter(Boolean);
  const school = eduParts[0] || '';
  const major = eduParts.length >= 3 ? eduParts[1] : (eduParts.length === 2 ? '' : (eduParts[1] || ''));

  // col-3 工作经历: 有则 timeline-wrap 里 "2024.09 2024.11 公司 岗位"; 无则显示 "未填写工作经历"
  const timelineEl = cardEl.querySelector(F3_SELECTORS.CARD_WORK_ROW_TIMELINE);
  const col3Text = (cardEl.querySelector(F3_SELECTORS.CARD_WORK_ROW)?.textContent || '')
    .replace(/\s+/g, ' ').trim();
  const latestWorkBrief = timelineEl
    ? (timelineEl.textContent || '').replace(/\s+/g, ' ').trim()
    : (col3Text === '未填写工作经历' ? '' : col3Text);

  const workYears = parseWorkYearsFromBrief(latestWorkBrief);

  const tagEls = cardEl.querySelectorAll(F3_SELECTORS.CARD_TAG_ITEM);
  const skill_tags = [];
  const school_tier_tags = [];
  const ranking_tags = [];
  let recommendation_reason = '';
  tagEls.forEach(t => {
    const txt = t.textContent.trim();
    if (!txt) return;
    // .tag-item.highlight = 推荐理由 (live 校准)
    if (t.classList?.contains('highlight') || /来自相似职位|推荐理由/.test(txt)) {
      recommendation_reason = txt;
      // school tier 标签 (live 校准 2026-05-18):
      //   - '211院校' / '985院校' / '双一流' — 老形态
      //   - 'QS前100院校' / 'QS前500院校' / '全球前100院校' — Boss 现行高频标签
      //   - '海外名校' / 'QS_TOP_*' — 偶发
      // 后端按学校名查 985/211 白名单兜底, 但这里能多抠就多抠, 反向加快通过率。
    } else if (/^\d+院校$|^985院校?$|^211院校?$|^双一流$|^QS前\d+院校?$|^全球前\d+院校?$|^海外名校$/.test(txt)) {
      school_tier_tags.push(txt);
    } else if (/专业前\d+%/.test(txt)) {
      ranking_tags.push(txt);
    } else {
      skill_tags.push(txt);
    }
  });

  const expected_salary = cardEl.querySelector(F3_SELECTORS.CARD_SALARY)?.textContent?.trim() || '';

  return {
    name, boss_id: bossId,
    age, education, grad_year: gradYear, work_years: workYears,
    school, major, intended_job: intendedJob,
    skill_tags, school_tier_tags, ranking_tags,
    expected_salary, active_status: activeStatus,
    recommendation_reason,
    latest_work_brief: latestWorkBrief,
    raw_text: '',
    boss_current_job_title: getBossTopJobTitle(),
  };
}

function parseWorkYearsFromBrief(brief) {
  if (!brief || brief === '未填写工作经历') return 0;
  // 两种格式: "2024.09 - 2024.11 公司 岗位" (带 hyphen) 或 "2024.09 2024.11 公司 岗位" (无 hyphen)
  const m = brief.match(/(\d{4})\.(\d{1,2})\s*-?\s*(\d{4})\.(\d{1,2})/);
  if (m) {
    const start = parseInt(m[1], 10) * 12 + parseInt(m[2], 10);
    const end = parseInt(m[3], 10) * 12 + parseInt(m[4], 10);
    return Math.max(0, Math.round((end - start) / 12));
  }
  return 0;
}

function getBossTopJobTitle() {
  // 岗位下拉在 iframe 里 (2026-04-21 live 校准), 非 top frame
  const doc = _getRecommendDoc() || document;
  const el = doc.querySelector(F3_SELECTORS.TOP_JOB_TEXT);
  if (!el) return '';
  const full = (el.textContent || '').replace(/\s+/g, ' ').trim();
  // "全栈工程师 _ 北京  400-500元/天" → 取 _ 前
  return full.split('_')[0].split('(')[0].trim();
}

// ════════════════════════════════════════════════════════════════════
// F3 主循环 — autoGreetRecommend
// ════════════════════════════════════════════════════════════════════

async function autoGreetRecommend({ jobId, serverUrl, authToken }) {
  LOG.length = 0; _paused = false; _stopped = false; _setRunning(true);
  const stats = { total: 0, greeted: 0, skipped: 0, rejected: 0, failed: 0, blocked: false };
  _setStats(stats);

  // F3 学历门槛筛选: 从 popup 设置 (chrome.storage.local) 读取, 缺省回退默认
  const educationFilter = await new Promise((resolve) => {
    try {
      chrome.storage.local.get(['HR_EDUCATION_FILTER'], (res) => {
        resolve(res.HR_EDUCATION_FILTER || {
          min_level: '本科', prestigious_tags: [], require_prestigious: false,
        });
      });
    } catch (e) {
      resolve({ min_level: '本科', prestigious_tags: [], require_prestigious: false });
    }
  });
  if (educationFilter.require_prestigious && educationFilter.prestigious_tags.length === 0) {
    log('未配置名校标签但启用了"必须名校", 已停止');
    _setRunning(false);
    return { success: false, message: '请在扩展弹窗里完善学历门槛设置', summary: stats, log: LOG };
  }
  log(`学历门槛: ${educationFilter.min_level}${educationFilter.require_prestigious ? ' + 必须名校' : ''}${educationFilter.prestigious_tags.length ? ' [' + educationFilter.prestigious_tags.join(',') + ']' : ''}`);

  try {
    if (!location.pathname.includes(F3_SELECTORS.PAGE_URL_PATH)) {
      return { success: false, message: '请先打开 Boss 推荐牛人页', log: LOG };
    }

    let idx = 0;
    const processedBossIds = new Set();
    let silentMissCount = 0;

    while (!_stopped) {
      await waitIfPaused();

      const risk = detectRiskControl();
      if (risk.detected) {
        stats.blocked = true;
        log(`风控命中: ${risk.source}`);
        _setRunning(false);
        return {
          success: false,
          message: `检测到 Boss 风控，已自动停止 (${risk.source})`,
          summary: stats, log: LOG,
        };
      }

      // 卡片全在 iframe 里 (2026-04-21 live 校准)
      const recDoc = _getRecommendDoc();
      if (!recDoc) {
        _setRunning(false);
        return { success: false, message: '未找到推荐牛人 iframe, 请刷新页面', summary: stats, log: LOG };
      }
      const cards = Array.from(recDoc.querySelectorAll(F3_SELECTORS.CARD_ITEM));
      if (idx >= cards.length) {
        // 触底：滚动加载更多
        const scrollable = recDoc.querySelector('.list-body') || recDoc.scrollingElement || recDoc.body;
        scrollable.scrollTop = scrollable.scrollHeight;
        log(`idx=${idx} 触底，滚动等待新卡片...`);
        // 轮询最多 8 秒等待新卡片出现
        let waited = 0;
        let newCount = cards.length;
        while (waited < 8000) {
          await sleep(500);
          waited += 500;
          newCount = recDoc.querySelectorAll(F3_SELECTORS.CARD_ITEM).length;
          if (newCount > cards.length) break;
        }
        if (newCount === cards.length) {
          log(`列表到底，无新卡片. 共处理 ${idx} 人`);
          break;
        }
        log(`新增 ${newCount - cards.length} 张卡片`);
        continue;
      }

      const card = cards[idx];
      idx++;

      const scraped = scrapeRecommendCard(card);
      if (!scraped) { log(`[${idx}] scrape失败,跳过`); stats.skipped++; _setStats(stats); continue; }
      if (processedBossIds.has(scraped.boss_id)) { stats.skipped++; _setStats(stats); continue; }
      processedBossIds.add(scraped.boss_id);

      stats.total++;
      log(`[${idx}] ${scraped.name} (${scraped.boss_id.substring(0,12)})`);

      let decision;
      try {
        const evalResp = await fetch(`${serverUrl}/api/recruit/evaluate_and_record`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${authToken}`,
          },
          body: JSON.stringify({ job_id: jobId, candidate: scraped, education_filter: educationFilter }),
        });
        if (evalResp.status === 401) {
          _setRunning(false);
          return { success: false, message: '登录已过期', summary: stats, log: LOG };
        }
        if (!evalResp.ok) {
          log(`后端错 HTTP ${evalResp.status}, 跳过`);
          stats.failed++; _setStats(stats); continue;
        }
        decision = await evalResp.json();
      } catch (e) {
        log(`网络错: ${e.message}, 跳过`);
        stats.failed++; _setStats(stats); continue;
      }

      if (decision.decision === 'blocked_daily_cap') {
        stats.blocked = true;
        log(`每日配额已满: ${decision.reason}`);
        _setRunning(false);
        return {
          success: false, message: `今日配额已满 (${decision.reason})`,
          summary: stats, log: LOG,
        };
      }
      if (decision.decision === 'skipped_already_greeted') {
        stats.skipped++; log('历史已打过招呼，跳过');
        _setStats(stats);
      } else if (decision.decision === 'rejected_low_education') {
        stats.rejected++;
        log(`学历未达标: ${decision.reason}, 跳过`);
        _setStats(stats);
      } else if (decision.decision === 'should_greet') {
        const greetBtn = findGreetButtonInCard(card);
        if (!greetBtn) {
          log('打招呼按钮找不到, 记失败');
          await reportGreetResult(serverUrl, authToken, decision.resume_id, false, 'button_not_found');
          stats.failed++; _setStats(stats); continue;
        }
        try {
          await simulateHumanClick(greetBtn);
          await sleep(1200 + Math.random() * 500);
          // 成功判定: 打招呼后原按钮消失 + 出现"继续沟通"按钮，或原按钮变为disabled/"已打招呼"
          const greetBtnGone = !greetBtn.isConnected || !card.querySelector('button.btn.btn-greet');
          const continueBtn = card.querySelector('button.btn-continue, button.btn.btn-continue');
          const btnText = greetBtn.textContent.trim();
          const done = greetBtnGone
                    || !!continueBtn
                    || greetBtn.classList.contains('done')
                    || btnText.includes('已打招呼')
                    || !!card.querySelector(F3_SELECTORS.CARD_GREET_BTN_DONE);
          if (done) {
            await reportGreetResult(serverUrl, authToken, decision.resume_id, true, '');
            stats.greeted++; log('打招呼成功');
            silentMissCount = 0;
          } else {
            await reportGreetResult(serverUrl, authToken, decision.resume_id, false, 'button_no_response');
            stats.failed++; silentMissCount++;
            log(`按钮无反应 (silent miss ${silentMissCount}/3)`);
            if (silentMissCount >= 3) {
              _setRunning(false);
              return {
                success: false, message: '连续 3 次按钮无反应, 熔断',
                summary: stats, log: LOG,
              };
            }
          }
        } catch (e) {
          log(`点击异常: ${e.message}`);
          await reportGreetResult(serverUrl, authToken, decision.resume_id, false, e.message);
          stats.failed++;
        }
        _setStats(stats);
      }

      // 节流
      const delay = 2000 + Math.random() * 3000;
      await sleep(delay);
      if (stats.greeted > 0 && stats.greeted % 10 === 0) {
        const longPause = 3000 + Math.random() * 3000;
        log(`已打 ${stats.greeted}, 长停 ${Math.round(longPause/1000)}s`);
        await sleep(longPause);
      }
    }

    _setRunning(false);
    return { success: true, summary: stats, log: LOG };
  } catch (e) {
    _setRunning(false);
    return { success: false, message: `异常: ${e.message}`, summary: stats, log: LOG };
  }
}

async function reportGreetResult(serverUrl, authToken, resumeId, success, errorMsg) {
  try {
    await fetch(`${serverUrl}/api/recruit/record-greet`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`,
      },
      body: JSON.stringify({ resume_id: resumeId, success, error_msg: errorMsg }),
    });
  } catch (e) {
    log(`record-greet 上报失败: ${e.message}`);
  }
}

function _setStats(stats) {
  chrome.storage.local.set({ recruitStats: stats });
}

function stringSimilarity(a, b) {
  if (!a || !b) return 0;
  // Chinese-friendly: use character bigrams + unigrams, compute Jaccard
  const tokens = s => {
    const set = new Set();
    const t = s.trim().toLowerCase();
    if (t.length === 0) return set;
    if (t.length === 1) { set.add(t); return set; }
    for (let i = 0; i < t.length - 1; i++) {
      set.add(t.slice(i, i + 2));  // bigram
    }
    for (const ch of t) set.add(ch);  // unigram for robustness
    return set;
  };
  const A = tokens(a), B = tokens(b);
  if (A.size === 0 || B.size === 0) return 0;
  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;
  const union = A.size + B.size - inter;
  return union === 0 ? 0 : inter / union;
}

/**
 * 在 card 里找"打招呼"按钮. 2026-04-21 live 校准: 选择器 "button.btn.btn-greet" valid.
 * 保留 textContent fallback 防未来 DOM 变.
 */
function findGreetButtonInCard(card) {
  const primary = card.querySelector(F3_SELECTORS.CARD_GREET_BTN);
  if (primary && primary.offsetParent !== null) return primary;

  // fallback: 扫 button/.btn-greet 任一含 "打招呼" 文本且可见
  const btns = card.querySelectorAll('button, [role="button"], .btn-greet');
  for (const b of btns) {
    if ((b.textContent || '').includes('打招呼') && b.offsetParent !== null) {
      return b;
    }
  }
  return null;
}

// ---- intake: chat page automation helpers ----
// Selectors verified live on zhipin.com/web/chat/index 2026-04-23.

async function intake_typeAndSendChatMessage(text) {
  const input = document.getElementById("boss-chat-editor-input");
  if (!input) return { ok: false, reason: "输入框未找到 (#boss-chat-editor-input)" };
  const beforeSelf = document.querySelectorAll(".chat-message-list .message-item .item-myself").length;

  input.focus();
  try { document.execCommand("selectAll", false); document.execCommand("delete", false); }
  catch (_) { input.textContent = ""; }
  await new Promise((r) => setTimeout(r, 120));

  // Type char-by-char via execCommand so Vue v-model / draft sync
  for (const ch of text) {
    try { document.execCommand("insertText", false, ch); }
    catch (_) {
      input.textContent += ch;
      input.dispatchEvent(new InputEvent("input", { bubbles: true, data: ch, inputType: "insertText" }));
    }
    await new Promise((r) => setTimeout(r, 25 + Math.random() * 55));
  }
  await new Promise((r) => setTimeout(r, 300));

  // Prefer Vue-exposed sendText() via MAIN world bridge (proven live 2026-04-23)
  let triggered = false;
  const sendRes = await _bridgeCall("send_text");
  if (sendRes?.ok) {
    triggered = true;
  }
  if (!triggered) {
    const opts = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
    input.dispatchEvent(new KeyboardEvent("keydown", opts));
    input.dispatchEvent(new KeyboardEvent("keypress", opts));
    input.dispatchEvent(new KeyboardEvent("keyup", opts));
  }

  // Verify delivery by watching for new .item-myself message (not by input-cleared heuristic).
  // 慢网下消息已发出、但新气泡要等服务端往返+渲染才出现，旧的 5s 窗口太短，
  // 会把"发送成功但慢"误判为失败 —— 进而触发 outbox 重发 / 重复打扰候选人。
  // 放宽到 20s，与本文件其它网络等待（聊天同步 12s、bridge 30s）量级一致。
  const SEND_CONFIRM_MS = 20000;
  const deadline = Date.now() + SEND_CONFIRM_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 250));
    const afterSelf = document.querySelectorAll(".chat-message-list .message-item .item-myself").length;
    if (afterSelf > beforeSelf) return { ok: true };
  }
  return { ok: false, reason: `${SEND_CONFIRM_MS / 1000}s 内未见新的 .item-myself 消息，发送可能失败` };
}

async function intake_clickRequestResumeButton() {
  const btn = Array.from(document.querySelectorAll(".operate-btn")).find(
    (el) => /求简历|索要简历/.test((el.textContent || "").trim())
  );
  if (!btn) return { ok: false, reason: "求简历按钮未找到 (.operate-btn)" };
  if (typeof simulateHumanClick === "function") await simulateHumanClick(btn);
  else btn.click();
  // Boss 弹出 "确定向牛人索取简历吗？" 确认框
  await new Promise((r) => setTimeout(r, 800));
  let confirm = document.querySelector(".exchange-tooltip .boss-btn-primary");
  if (!confirm) {
    confirm = Array.from(document.querySelectorAll(".boss-popup__btn, button, span"))
      .find((el) => /^确定$/.test((el.textContent || "").trim()) && el.offsetParent !== null);
  }
  if (confirm) {
    if (typeof simulateHumanClick === "function") await simulateHumanClick(confirm);
    else confirm.click();
    await new Promise((r) => setTimeout(r, 400));
  }
  return { ok: true };
}

async function intake_checkPdfReceived(bossId) {
  // Skip accept/reject prompt cards; only treat 预览/查看/下载 buttons as real PDF.
  // BUG-A1: do NOT return card title as `url`. The title is human-readable
  // text like "简历.pdf" and earlier callers used it as a fallback pdf_url
  // when downloadPdf() failed, polluting Resume.pdf_path with bare filenames
  // that 404 in /api/resumes/{id}/pdf. Only signal presence here; the real
  // server-side path comes from downloadPdf() → /api/resumes/upload.
  const cards = document.querySelectorAll(".message-card-wrap.boss-green");
  for (let i = cards.length - 1; i >= 0; i--) {
    const title = (cards[i].querySelector(".message-card-top-title")?.textContent || "").trim();
    if (/您是否同意|拒绝发送|拒绝同意/.test(title)) continue;
    const btns = cards[i].querySelectorAll(".card-btn:not(.disabled)");
    for (const btn of btns) {
      const t = (btn.textContent || "").trim();
      if (/预览|查看|下载/.test(t)) {
        return { present: true };
      }
    }
  }
  return { present: false };
}

window.intake_typeAndSendChatMessage = intake_typeAndSendChatMessage;
window.intake_clickRequestResumeButton = intake_clickRequestResumeButton;
window.intake_checkPdfReceived = intake_checkPdfReceived;

// ---- intake: orchestrator ----

function intake_getQueryParam(key) {
  try {
    return new URL(location.href).searchParams.get(key);
  } catch {
    return null;
  }
}

async function intake_getServerUrl() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["serverUrl"], (r) => {
      resolve(r.serverUrl || "http://127.0.0.1:8000");
    });
  });
}

async function intake_getAuthToken() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["authToken"], (r) => resolve(r.authToken || ""));
  });
}

async function intake_postJSON(path, body) {
  const base = await intake_getServerUrl();
  const token = await intake_getAuthToken();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const r = await fetch(`${base}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`${path} → ${r.status} ${txt.slice(0, 200)}`);
  }
  return r.json();
}

function intake_showToast(msg, kind) {
  let el = document.getElementById("intake-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "intake-toast";
    el.style.cssText =
      "position:fixed;top:20px;right:20px;z-index:99999;background:#fff;" +
      "border:2px solid #00b38a;padding:12px 16px;border-radius:8px;" +
      "box-shadow:0 4px 12px rgba(0,0,0,0.15);font-size:13px;max-width:320px;" +
      "color:#333;font-family:-apple-system,sans-serif;";
    document.body.appendChild(el);
  }
  el.textContent = `【采集】${msg}`;
  const color =
    kind === "error" ? "#ff4d4f" : kind === "done" ? "#52c41a" : "#00b38a";
  el.style.borderColor = color;
}

function intake_waitFor(predicate, timeoutMs) {
  timeoutMs = timeoutMs || 10000;
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = () => {
      try {
        if (predicate()) return resolve(true);
      } catch {}
      if (Date.now() - start > timeoutMs) return reject(new Error("timeout"));
      setTimeout(tick, 200);
    };
    tick();
  });
}

// Retry an async network step a few times before giving up. BOSS直聘 lag
// is bursty — a stalled virtual-list scroll or a slow chat-panel sync
// usually succeeds on a second attempt. `fn(attempt)` should return a
// truthy value on success, or throw / return falsy on a failed attempt.
// Returns the first truthy result, or null once `tries` is exhausted —
// never throws, so callers can treat null as "give up this candidate
// this round" instead of crashing the whole Step2 loop.
async function intake_retry(fn, opts) {
  const { tries = 3, delayMs = 800, label = "" } = opts || {};
  for (let attempt = 1; attempt <= tries; attempt++) {
    try {
      const result = await fn(attempt);
      if (result) return result;
    } catch (e) {
      log(`[retry] ${label} 第${attempt}次失败: ${e?.message || e}`);
    }
    if (attempt < tries) await sleep(delayMs * attempt); // linear backoff
  }
  if (label) log(`[retry] ${label} ${tries} 次后放弃`);
  return null;
}

// Scroll the geek-item virtual list until a row with the given data-id renders
// into the DOM.
//
// Browser-verified DOM facts (Boss's chat page, 2026-04):
//   - The real scroll container is `.user-list.b-scroll-stable`.
//   - That container has `overflow-y: hidden` (display:none-style scroll bar)
//     yet `scrollTop` writes ARE honored and DO trigger the SPA's lazy fetch.
//   - `findScrollableAncestor` filters on `overflowY: auto|scroll`, so it
//     SKIPS this container and falls back to `document.body`, which is why
//     the previous implementation never actually scrolled the list.
//   - Setting `scrollTop = scrollHeight` reliably extends the list (tested:
//     40 → 42 with scrollHeight 7830 → 15630 in one tick).
//
// Strategy: pick `.user-list` explicitly; loop scrollTop=scrollHeight until
// the target id renders or the list stops growing.
function intake_findGeekListScroller() {
  return document.querySelector(".user-list.b-scroll-stable")
      || document.querySelector(".user-list");
}

async function intake_scrollUntilGeekVisible(bossId, opts = {}) {
  const sel = `.geek-item[data-id="${bossId}"]`;
  if (document.querySelector(sel)) return true;

  if (!document.querySelectorAll(".geek-item").length) {
    intake_showToast("候选人列表为空，请先打开 Boss 消息页", "error");
    return false;
  }
  const scroller = intake_findGeekListScroller();
  if (!scroller) {
    intake_showToast("未找到候选人列表滚动容器（.user-list）", "error");
    return false;
  }
  const maxRounds = opts.maxRounds || 30;

  for (let i = 0; i < maxRounds; i++) {
    if (document.querySelector(sel)) {
      const el = document.querySelector(sel);
      try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch { el.scrollIntoView(false); }
      return true;
    }
    const before = document.querySelectorAll(".geek-item").length;
    scroller.scrollTop = scroller.scrollHeight;
    await new Promise((r) => setTimeout(r, 1200));
    const after = document.querySelectorAll(".geek-item").length;
    if (after === before) {
      // List bottomed out — the SPA returned no new rows after a full
      // scrollTop=scrollHeight write + 1.2s settle.
      intake_showToast(`列表已到底，共 ${after} 人，未找到目标`, "error");
      return false;
    }
    intake_showToast(`滚动加载… 已渲染 ${after} 人 (round ${i + 1})`);
  }
  return !!document.querySelector(sel);
}

async function intake_runOrchestrator(opts = {}) {
  const forceRequestPdf = !!opts.forceRequestPdfIfMissing;
  intake_showToast("正在分析聊天记录...");

  // Resolve which candidate the user wants to capture, in priority order:
  //   1. URL ?id=  (deep-link from /intake or extension popup)
  //   2. .geek-item.selected (left-list selection state)
  //   3. parseChatFromDOM result (right-panel parser; works even when the
  //      candidate isn't rendered in the left list at all).
  const urlBossId = intake_getQueryParam("id");
  let effectiveBossId = urlBossId;
  const selectedNow = document.querySelector(".geek-item.selected");
  if (!effectiveBossId && selectedNow) {
    effectiveBossId = selectedNow.getAttribute("data-id") || null;
  }

  // Critical insight (verified against live Boss DOM): when the user opens a
  // chat via deep-link, the right panel mounts but Boss does NOT auto-select
  // or auto-scroll the corresponding .geek-item in the left list. The
  // candidate may sit far outside the left-list render window, and the SPA
  // is happy to leave it there forever — chasing it via virtual-scroll is
  // expensive and can fail when the list runs out before reaching them.
  //
  // We don't actually need the left-list row. parseChatFromDOM reads the
  // boss_id straight from the right-panel chat header / message list. So
  // skip the left-list dance entirely whenever the chat panel is already
  // showing the conversation we want.
  const rightPanelReady = () => {
    const nb = (document.querySelector(".name-box")?.textContent || "").trim();
    const msgs = document.querySelectorAll(".chat-message-list .message-item").length;
    return !!nb && msgs > 0;
  };

  // Wait briefly for the right panel to render — for deep-link paths it
  // may need a moment to mount.
  try {
    await intake_waitFor(rightPanelReady, 8000);
  } catch {}

  if (!rightPanelReady()) {
    // Right panel never came up — fall back to the old left-list path so we
    // can click the row programmatically and force the chat open.
    if (effectiveBossId) {
      let found = !!document.querySelector(`.geek-item[data-id="${effectiveBossId}"]`);
      if (!found) {
        try {
          await intake_waitFor(
            () => !!document.querySelector(`.geek-item[data-id="${effectiveBossId}"]`),
            2500
          );
          found = true;
        } catch {
          intake_showToast("候选人未在视窗，自动滚动加载...");
          found = await intake_scrollUntilGeekVisible(effectiveBossId);
        }
      }
      if (!found) {
        const total = document.querySelectorAll(".geek-item").length;
        const sample = document.querySelector(".geek-item")?.getAttribute("data-id") || "(none)";
        intake_showToast(
          `候选人未出现在列表（找 id=${effectiveBossId}，DOM 有 ${total} 行，首行 data-id=${sample}）`,
          "error"
        );
        return;
      }
      const item = document.querySelector(`.geek-item[data-id="${effectiveBossId}"]`);
      if (item) {
        try { item.scrollIntoView({ block: "center" }); } catch {}
        if (!item.classList.contains("selected")) item.click();
      }
    }
    try {
      await intake_waitFor(rightPanelReady, 15000);
    } catch {
      const sel = document.querySelector(".geek-item.selected");
      const nb = (document.querySelector(".name-box")?.textContent || "").trim();
      const msgs = document.querySelectorAll(".chat-message-list .message-item").length;
      intake_showToast(
        `未找到聊天窗口 (selected=${sel?.getAttribute("data-id") || "none"}, name="${nb || "空"}", msgs=${msgs})`,
        "error"
      );
      return;
    }
  }

  // Force the latest candidate replies into the rendered DOM before parsing.
  // Boss's chat-message-list is a virtualized scroller — newer messages
  // (especially those that arrived after we mounted the panel) may sit
  // below the viewport until the list scrolls to its bottom. Without this,
  // we risk parsing only the top history and missing the candidate's most
  // recent answer, which then makes the slot extractor report empty.
  const msgListEl = document.querySelector(".chat-message-list");
  if (msgListEl) {
    try { msgListEl.scrollTop = msgListEl.scrollHeight; } catch {}
    await new Promise((r) => setTimeout(r, 600));
  }

  const root = document.querySelector(window.CHAT_SELECTORS.root);
  const parsed = window.parseChatFromDOM(root);
  // Diagnostic: surface the parse outcome so the user can see in F12 whether
  // the extension actually saw the candidate's reply or only old messages.
  console.log("[intake] parseChatFromDOM →", {
    boss_id: parsed?.boss_id,
    name: parsed?.name,
    msg_count: parsed?.messages?.length,
    last_3: parsed?.messages?.slice(-3),
  });
  if (!parsed || !parsed.boss_id) {
    intake_showToast("抓取聊天信息失败（boss_id 未识别）", "error");
    return;
  }
  intake_showToast(`抓到 ${parsed.messages?.length || 0} 条消息`);

  const pdf = await window.intake_checkPdfReceived(parsed.boss_id);

  // 若 PDF 可见，复用已有 downloadPdf() 真实下载 + 上传后端（与批量采集路径一致）。
  // 后端 /api/resumes/upload 存 PDF 到 settings.resume_storage_path 并写 Resume.pdf_path
  // 为服务器端真实路径；之后 collect-chat 把该真实路径作为 pdf_url 传入，让
  // intake slot 存真实 path、promote_to_resume 再 merge 到同一 Resume 行。
  let realPdfPath = null;
  if (pdf.present) {
    intake_showToast("检测到简历，下载中...");
    const serverUrl = await intake_getServerUrl();
    const authToken = await intake_getAuthToken();
    const detail = extractDetail();
    detail.boss_id = parsed.boss_id;
    // Prefer the geek-item that matches our resolved boss_id over
    // .geek-item.selected — Boss SPA leaves .selected pointing at the
    // previously-clicked candidate when the user opens a chat via deep-link
    // or switches conversations. Pulling push-text from the wrong row was
    // how 李熠's PDF ended up tagged with 陈铭's boss_id earlier.
    const pushTextSource =
      document.querySelector(`.geek-item[data-id="${parsed.boss_id}"]`) ||
      (document.querySelector(".geek-item.selected")?.getAttribute("data-id") === parsed.boss_id
        ? document.querySelector(".geek-item.selected")
        : null);
    if (pushTextSource) supplementFromPushText(detail, pushTextSource);
    const dl = await downloadPdf(detail, parsed.name, serverUrl, authToken);
    if (dl && dl.ok && dl.data) {
      realPdfPath = dl.data.pdf_path || null;
      intake_showToast("简历已上传");
    } else {
      intake_showToast("简历下载失败，仍按文件名记录", "error");
    }
  } else if (forceRequestPdf) {
    // Manual single-chat path: proactively click 求简历 so HR doesn't wait for
    // backend NextAction. Idempotent at button level — if already requested,
    // clickRequestResumeButton returns ok=false with a benign reason.
    intake_showToast("未检测到简历，尝试求简历...");
    try {
      const r = await window.intake_clickRequestResumeButton();
      if (r && r.ok) {
        intake_showToast("已点击求简历，等候方发来后再次点击采集即可下载", "done");
      } else {
        intake_showToast(`求简历按钮状态: ${(r && r.reason) || "未触发"}`, "info");
      }
    } catch (e) {
      intake_showToast(`求简历失败: ${e.message}`, "error");
    }
  }

  let resp;
  try {
    resp = await intake_postJSON("/api/intake/collect-chat", {
      boss_id: parsed.boss_id,
      name: parsed.name,
      job_intention: parsed.job_intention,
      messages: parsed.messages,
      pdf_present: pdf.present,
      // BUG-A1: only ever send a real server-side path from /resumes/upload.
      // Never fall back to pdf.url (card title) — backend now rejects bare names
      // but defending at the source avoids audit noise.
      pdf_url: realPdfPath || null,
      skip_outbox: true,
    });
  } catch (e) {
    intake_showToast(`后端返回错误: ${e.message}`, "error");
    return;
  }

  const action = resp.next_action;
  intake_showToast(`下一步: ${action.type}`);

  try {
    if (action.type === "send_hard" || action.type === "send_soft") {
      const r = await window.intake_typeAndSendChatMessage(action.text);
      if (r.ok) {
        await intake_postJSON(
          `/api/intake/candidates/${resp.candidate_id}/ack-sent`,
          { action_type: action.type, delivered: true }
        );
        intake_showToast("问题已发送", "done");
      } else {
        intake_showToast(`发送失败: ${r.reason}`, "error");
      }
    } else if (action.type === "request_pdf") {
      const r = await window.intake_clickRequestResumeButton();
      if (r.ok) {
        await intake_postJSON(
          `/api/intake/candidates/${resp.candidate_id}/ack-sent`,
          { action_type: "request_pdf", delivered: true }
        );
        intake_showToast("已点击求简历", "done");
      } else {
        intake_showToast(`按钮未找到: ${r.reason}`, "error");
      }
    } else if (action.type === "wait_pdf") {
      intake_showToast("等待候选人发送简历", "info");
    } else if (action.type === "wait_reply") {
      intake_showToast("冷却中 — 等对方回复后再问", "info");
    } else if (action.type === "complete") {
      intake_showToast("采集完成 → 已进入简历库", "done");
    } else if (action.type === "mark_pending_human") {
      intake_showToast("已标记为人工兜底", "done");
    } else if (action.type === "abandon") {
      intake_showToast("候选人超时，已放弃", "error");
    }
  } catch (e) {
    intake_showToast(`执行动作失败: ${e.message}`, "error");
  }
}

// ────────────────────────────────────────────────────────
// Virtual-list scroll helper — works across sendIntakeMessage
// and outbox dispatch. Scrolls the virtual list to bring the
// target geek-item into the DOM viewport before returning it.
// ────────────────────────────────────────────────────────
async function _scrollToGeekItem(bossId) {
  const dataId = String(bossId);
  let el = document.querySelector(`.geek-item[data-id="${dataId}"]`);
  if (el) return el;
  // Item not in DOM viewport — ask MAIN world bridge to scroll virtual list
  const res = await _bridgeCall("scroll_to_geek", { bossId: dataId });
  if (!res || res.idx === -1) return null;
  // Condition-based wait: virtual list re-render can take >500ms on slow pages
  await waitForSel(`.geek-item[data-id="${dataId}"]`, 1500);
  return document.querySelector(`.geek-item[data-id="${dataId}"]`);
}

// ────────────────────────────────────────────────────────
// F4 Task 11: sendIntakeMessage — reusable helper for
// (autoscan tick, outbox dispatch). Locates the chat row by
// data-id in the geek-item list, clicks to select, waits for
// the chat window to sync, then types + sends `text`.
// Returns true on visible delivery, false on any visible failure.
// ────────────────────────────────────────────────────────
async function sendIntakeMessage({ boss_id, text }) {
  try {
    if (!boss_id || !text) return false;
    if (!location.host.includes("zhipin.com")) return false;
    const dataId = String(boss_id);
    const row = await _scrollToGeekItem(dataId);
    if (!row) {
      log(`[sendIntakeMessage] geek-item[data-id="${dataId}"] not found (virtual list)`);
      return false;
    }
    if (!row.classList.contains("selected")) {
      row.click();
    }
    // Wait for right panel to sync to this candidate
    try {
      await intake_waitFor(() => {
        const sel = document.querySelector(".geek-item.selected");
        const nb = (document.querySelector(".name-box")?.textContent || "").trim();
        return sel?.getAttribute("data-id") === dataId && !!nb;
      }, 12000);
    } catch {
      log(`[sendIntakeMessage] chat sync timeout for ${dataId}`);
      return false;
    }
    await sleep(400);
    const r = await intake_typeAndSendChatMessage(text);
    return !!(r && r.ok);
  } catch (e) {
    log(`[sendIntakeMessage] error: ${e?.message || e}`);
    return false;
  }
}

window.sendIntakeMessage = sendIntakeMessage;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === "intake_outbox_dispatch") {
    // 互斥：批采正在跑时拒绝 dispatch，让 outbox row 走 ack_failed → 后端 reap → 下轮 pending
    if (window.__intakeBatchInProgress) {
      const ob = message.outbox || {};
      chrome.runtime.sendMessage({
        type: "intake_outbox_ack",
        outbox_id: ob.id,
        success: false,
        error: "batch collect in progress, defer",
      });
      sendResponse({ queued: false, reason: "batch in progress" });
      return true;
    }
    // Serialize concurrent dispatches: background may send multiple items
    // back-to-back and Chrome resolves the await before sendResponse fires,
    // which caused characters from 3 outbox rows to interleave in the same
    // input box. Chain onto a module-level promise to force serial execution.
    // sendResponse({queued:true}) at task-start so background's await resolves
    // immediately (ack path is the separate intake_outbox_ack runtime message).
    window.__intakeDispatchQueue = (window.__intakeDispatchQueue || Promise.resolve()).then(async () => {
      const ob = message.outbox || {};
      try {
        let ok = false;
        if (ob.action_type === "request_pdf") {
          // request_pdf has no text — switch to candidate first, then click 求简历
          const row = await _scrollToGeekItem(ob.boss_id);
          if (!row) {
            ok = false;
          } else {
            if (!row.classList.contains("selected")) row.click();
            try {
              await intake_waitFor(() => {
                const sel = document.querySelector(".geek-item.selected");
                return sel?.getAttribute("data-id") === String(ob.boss_id);
              }, 10000);
              await sleep(500);
            } catch (_) { /* timeout — still try */ }
            const r = await window.intake_clickRequestResumeButton();
            ok = !!(r && r.ok);
          }
        } else {
          const r = await sendIntakeMessage({ boss_id: ob.boss_id, text: ob.text });
          ok = !!r;
        }
        chrome.runtime.sendMessage({
          type: "intake_outbox_ack",
          outbox_id: ob.id,
          success: ok,
          error: ok ? "" : "send returned false",
        });
      } catch (e) {
        chrome.runtime.sendMessage({
          type: "intake_outbox_ack",
          outbox_id: ob.id,
          success: false,
          error: String(e?.message || e).slice(0, 500),
        });
      }
    });
    sendResponse({ queued: true });
    return true;
  }
  if (message && message.type === "intake_step1_scan") {
    if (window.__intakeBatchInProgress) {
      sendResponse({ ok: true, skipped: "batch in progress" });
      return true;
    }
    window.__intakeBatchInProgress = true;
    step1_scanList()
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }))
      .finally(() => { window.__intakeBatchInProgress = false; });
    return true;
  }
  if (message && message.type === "intake_step2_enrich") {
    if (window.__intakeBatchInProgress) {
      sendResponse({ ok: true, skipped: "batch in progress" });
      return true;
    }
    window.__intakeBatchInProgress = true;
    step2_enrichCandidates()
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }))
      .finally(() => { window.__intakeBatchInProgress = false; });
    return true;
  }
  return false;
});

// ════════════════════════════════════════════════════════════════════
// Step1: 扫描"全部"列表，批量注册新候选人（不进入聊天）
// ════════════════════════════════════════════════════════════════════

// (cap removed 2026-05-22 — bridge stable-rounds + deadline already bound the result)

async function step1_scanList() {
  if (!location.host.includes("zhipin.com")) return { ok: false, reason: "not_on_zhipin" };
  if (!/\/web\/chat/.test(location.pathname) || /recommend/.test(location.pathname)) {
    return { ok: false, reason: "not_on_chat_list" };
  }
  intake_showToast("Step1: 加载联系人列表...", "info");
  const serverUrl = await intake_getServerUrl();
  const authToken = await intake_getAuthToken();
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;

  // 切换到"全部"标签（含"全部联系人"变体）
  switchToTab("全部") || switchToTab("全部联系人");
  await sleep(800); // 给标签切换留一点过渡时间

  // 等候选人列表真正渲染出来再扫描。慢网/页面未就绪时，Boss 列表可能
  // 几秒后才出现首行 .geek-item。旧实现只 sleep 后直接读取，列表没加载
  // 出来时 bridge 与 DOM 兜底都为空 → 误报"扫描 0 人"且 ok:true，
  // 用户以为采集成功、实则一个候选人都没进系统。
  // 改为条件等待：最多 15s 等首行出现，超时则明确返回失败而非假成功。
  try {
    await intake_waitFor(() => !!document.querySelector(".geek-item"), 15000);
  } catch (_) {
    intake_showToast("Step1: 候选人列表未加载，请确认 BOSS 消息页已打开后重试", "error");
    log("[step1] 候选人列表 15s 内未渲染，放弃本次扫描");
    return { ok: false, reason: "list_not_loaded" };
  }

  const processed = new Set();
  let registered = 0, failed = 0;

  // BOSS直聘使用虚拟列表，DOM 只渲染可见窗口（~40条）
  // 通过 MAIN world 桥接滚动加载全量数据（多页懒加载，最多等待 20s）
  intake_showToast("Step1: 滚动加载全部联系人...", "info");
  const bridgeResult = await _bridgeCall("get_datasources");
  const dataSources = bridgeResult?.data;

  if (dataSources && dataSources.length > 0) {
    log(`[step1] 虚拟列表读取: 共 ${dataSources.length} 条候选人`);
    const total = dataSources.length;
    intake_showToast(`Step1: 共 ${total} 人，注册中...`, "info");
    for (const item of dataSources) {
      const bossId = item.uniqueId;  // e.g. "70177414-0"
      if (!bossId) continue;
      processed.add(bossId);
      const name = item.name || "";
      const jobTitle = item.jobName || "";
      try {
        const r = await fetch(`${serverUrl}/api/intake/candidates/register`, {
          method: "POST",
          headers,
          body: JSON.stringify({ boss_id: bossId, name, job_title: jobTitle || null }),
        });
        if (r.ok) registered++;
        else { failed++; log(`[step1] register HTTP ${r.status} boss_id=${bossId}`); }
      } catch (e) {
        failed++;
        log(`[step1] register error: ${e?.message || e}`);
      }
      await sleep(30); // 本地 API，轻量节流
    }
  } else {
    // 兜底：DOM 扫描（虚拟列表不可用时）
    log("[step1] dataSources 不可用，回退到 DOM 扫描");
    let items = document.querySelectorAll(".geek-item");
    for (const item of items) {
      const bossId = item.getAttribute("data-id");
      if (!bossId || processed.has(bossId)) continue;
      processed.add(bossId);
      const name = item.querySelector(".geek-name")?.textContent?.trim() || "";
      const jobTitle =
        item.querySelector(".source-job")?.textContent?.trim() ||
        item.querySelector(".expect-job")?.textContent?.trim() ||
        item.querySelector(".geek-expect")?.textContent?.trim() ||
        item.querySelector("[class*='expect']")?.textContent?.trim() || "";
      try {
        const r = await fetch(`${serverUrl}/api/intake/candidates/register`, {
          method: "POST",
          headers,
          body: JSON.stringify({ boss_id: bossId, name, job_title: jobTitle || null }),
        });
        if (r.ok) registered++;
        else { failed++; log(`[step1] register HTTP ${r.status} boss_id=${bossId}`); }
      } catch (e) {
        failed++;
        log(`[step1] register error: ${e?.message || e}`);
      }
      await sleep(60);
    }
  }

  const msg = `Step1 完成: 注册 ${registered} 人, 失败 ${failed}, 扫描 ${processed.size} 人`;
  intake_showToast(msg, "done");
  log(`[step1] ${msg}`);
  return { ok: true, registered, failed, scanned: processed.size };
}

// ════════════════════════════════════════════════════════════════════
// Step2: 逐个打开聊天，分析新消息，推进 intake 状态机
// ════════════════════════════════════════════════════════════════════

// Message count cache lives in chrome.storage.session (survives content-script
// re-injection within a browser session; cleared on browser close).
// Loaded into a local object at the start of each step2_enrichCandidates run.
const _STEP2_INTER_DELAY_MS = 1500;

async function step2_enrichCandidates() {
  if (!location.host.includes("zhipin.com")) return { ok: false, reason: "not_on_zhipin" };
  if (!/\/web\/chat/.test(location.pathname) || /recommend/.test(location.pathname)) {
    return { ok: false, reason: "not_on_chat_list" };
  }
  intake_showToast("Step2: 分析候选人聊天...", "info");
  const serverUrl = await intake_getServerUrl();
  const authToken = await intake_getAuthToken();
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const authHeaders = authToken ? { Authorization: `Bearer ${authToken}` } : {};

  // Load persisted caches from chrome.storage.session (survives re-injection)
  let _msgCounts = {};
  let _missCounts = {};
  try {
    const stored = await chrome.storage.session.get(["intake_msg_counts", "intake_miss_counts"]);
    _msgCounts = stored.intake_msg_counts || {};
    _missCounts = stored.intake_miss_counts || {};
  } catch (_) {}

  // 拉取待处理候选人（collecting + awaiting_reply 状态）
  let candidates;
  try {
    const r = await fetch(
      `${serverUrl}/api/intake/autoscan/rank?limit=9999`,
      { headers: authHeaders }
    );
    if (!r.ok) return { ok: false, reason: `rank_http_${r.status}` };
    candidates = (await r.json()).items || [];
  } catch (e) {
    return { ok: false, reason: e?.message || e };
  }

  let processed = 0, skipped_missing = 0, skipped_no_new = 0, failed = 0;

  for (const c of candidates) {
    const bossId = c.boss_id;
    if (!bossId) { skipped_missing++; continue; }

    // 切到下一个候选人**之前**强制清理上一人的简历预览/PDF iframe。
    // 现网报告 2026-05-18: 上一人简历预览没关上, 已开始给下一人发消息。
    // 防止 downloadPdf 任一异常路径残留弹窗污染下一轮。
    await closeDialog();

    // 慢网下虚拟列表滚动可能一次没到位 — 重试 3 次再判定"不在列表"
    const geek = await intake_retry(() => _scrollToGeekItem(bossId), {
      tries: 3, delayMs: 700, label: `滚动定位 ${bossId}`,
    });
    if (!geek) {
      skipped_missing++;
      log(`[step2] ${bossId} 不在列表（重试 3 次仍未定位）`);
      _missCounts[bossId] = (_missCounts[bossId] || 0) + 1;
      chrome.storage.session.set({ intake_miss_counts: _missCounts }).catch(() => {});
      if (_missCounts[bossId] >= 3 && c.candidate_id) {
        fetch(`${serverUrl}/api/intake/candidates/${c.candidate_id}/status`, {
          method: "PATCH", headers,
          body: JSON.stringify({ status: "abandoned" }),
        }).catch(() => {});
        log(`[step2] ${bossId} 连续3次不在列表，自动放弃`);
      }
      continue;
    }

    // 点击切换到该候选人聊天 — 慢网下面板可能要点第二次才同步，重试 3 次
    const synced = await intake_retry(async () => {
      geek.click();
      // 等候选人面板同步：selected 匹配 + name-box 填充 + 至少1条消息渲染
      await intake_waitFor(() => {
        const sel = document.querySelector(".geek-item.selected");
        const nb = (document.querySelector(".name-box")?.textContent || "").trim();
        const msgs = document.querySelectorAll(".chat-message-list .message-item").length;
        const noData = !!document.querySelector(".conversation-no-data");
        return sel?.getAttribute("data-id") === bossId && !!nb && (msgs > 0 || noData);
      }, 12000);
      return true;
    }, { tries: 3, delayMs: 800, label: `面板同步 ${bossId}` });
    if (!synced) {
      log(`[step2] ${bossId} 面板同步超时（重试 3 次仍失败）`);
      failed++;
      continue;
    }
    await sleep(300);

    // 抓取聊天内容
    const chatRoot = document.querySelector(window.CHAT_SELECTORS?.root || ".chat-conversation");
    let parsed = null;
    try {
      if (window.parseChatFromDOM) parsed = window.parseChatFromDOM(chatRoot);
    } catch (e) {
      log(`[step2] parseChatFromDOM error: ${e?.message || e}`);
    }
    const messages = parsed?.messages || [];

    // 候选人消息数（排除自己发的 "self"）
    const candidateMsgCount = messages.filter(
      (m) => m.sender_id && m.sender_id !== "self"
    ).length;
    const prevCount = _msgCounts[bossId];

    if (prevCount !== undefined && candidateMsgCount === prevCount) {
      // 无新候选人消息，跳过 LLM 分析；仍更新 last_checked_at 供 UI 显示
      skipped_no_new++;
      log(`[step2] ${bossId} 无新候选人消息 (${candidateMsgCount}条)，跳过`);
      if (c.candidate_id) {
        fetch(`${serverUrl}/api/intake/candidates/${c.candidate_id}/last-checked`, {
          method: "PATCH", headers: authHeaders,
        }).catch(() => {});
      }
      continue;
    }
    // _msgCounts written only after successful collect-chat (prevents cache-on-failure bug)

    // ── 有新消息：PDF 检测 + collect-chat ──────────────────────
    const pdf = await window.intake_checkPdfReceived(bossId);
    let realPdfPath = null;
    if (pdf.present) {
      const detail = extractDetail();
      detail.boss_id = bossId;
      supplementFromPushText(detail, geek);
      const dl = await downloadPdf(detail, parsed?.name || "", serverUrl, authToken);
      if (dl?.ok && dl.data) realPdfPath = dl.data.pdf_path || null;
    }

    // collect-chat 网络抖动重试 3 次。collect-chat 幂等：skip_outbox=true 不生成
    // outbox 行，终态有 _TERMINAL_STATUSES 守卫，重跑 analyze_chat 抽取同样消息结果一致。
    const collectResp = await intake_retry(async () => {
      const r = await fetch(`${serverUrl}/api/intake/collect-chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          boss_id: bossId,
          name: parsed?.name || "",
          job_intention: parsed?.job_intention || "",
          messages,
          pdf_present: pdf.present,
          // BUG-A1: only ever send a real server-side path; null lets backend
          // re-issue request_pdf instead of persisting a card-title fallback.
          pdf_url: realPdfPath || null,
          skip_outbox: true,
        }),
      });
      if (!r.ok) {
        log(`[step2] collect-chat HTTP ${r.status}`);
        return null; // 5xx / 抖动 → 重试
      }
      return await r.json();
    }, { tries: 3, delayMs: 800, label: `collect-chat ${bossId}` });
    if (!collectResp) {
      log(`[step2] ${bossId} collect-chat 失败（重试 3 次）`);
      failed++;
      continue;
    }
    // Cache count only on success — prevents candidate being stuck if backend returns 500
    _msgCounts[bossId] = candidateMsgCount;
    chrome.storage.session.set({ intake_msg_counts: _msgCounts }).catch(() => {});

    const candidateId = collectResp.candidate_id;
    const action = collectResp.next_action;
    log(`[step2] ${bossId} → ${action?.type}`);

    // ── 执行 next_action ────────────────────────────────────────
    try {
      if (action.type === "send_hard" || action.type === "send_soft") {
        const r = await window.intake_typeAndSendChatMessage(action.text);
        if (r.ok) {
          const ackR = await fetch(`${serverUrl}/api/intake/candidates/${candidateId}/ack-sent`, {
            method: "POST",
            headers,
            body: JSON.stringify({ action_type: action.type, delivered: true }),
          });
          if (!ackR.ok) log(`[step2] ack-sent HTTP ${ackR.status} for ${bossId}`);
          intake_showToast(`${parsed?.name || bossId}: 问题已发送`, "done");
        } else {
          intake_showToast(`${parsed?.name || bossId}: 发送失败 — ${r.reason}`, "error");
        }
      } else if (action.type === "request_pdf") {
        const r = await window.intake_clickRequestResumeButton();
        if (r.ok) {
          const ackR = await fetch(`${serverUrl}/api/intake/candidates/${candidateId}/ack-sent`, {
            method: "POST",
            headers,
            body: JSON.stringify({ action_type: "request_pdf", delivered: true }),
          });
          if (!ackR.ok) log(`[step2] ack-sent HTTP ${ackR.status} for ${bossId}`);
          intake_showToast(`${parsed?.name || bossId}: 已求简历`, "done");
        }
      } else if (action.type === "complete") {
        intake_showToast(`${parsed?.name || bossId}: 采集完成 → 已进入简历库`, "done");
      } else if (action.type === "timed_out") {
        intake_showToast(`${parsed?.name || bossId}: 超时未回复，已标记`, "error");
      }
    } catch (e) {
      log(`[step2] action execution error: ${e?.message || e}`);
    }

    // 更新 last_checked_at
    try {
      await fetch(`${serverUrl}/api/intake/candidates/${candidateId}/last-checked`, {
        method: "PATCH",
        headers: authHeaders,
      });
    } catch (e) {
      log(`[step2] last-checked update failed: ${e?.message || e}`);
    }

    // Clear ghost-candidate miss counter on successful process
    if (_missCounts[bossId]) {
      delete _missCounts[bossId];
      chrome.storage.session.set({ intake_miss_counts: _missCounts }).catch(() => {});
    }

    processed++;
    await sleep(_STEP2_INTER_DELAY_MS);
  }

  const msg = `Step2 完成: 处理 ${processed}, 列表缺失 ${skipped_missing}, 无新消息 ${skipped_no_new}, 失败 ${failed}`;
  intake_showToast(msg, "done");
  log(`[step2] ${msg}`);
  return { ok: true, processed, skipped_missing, skipped_no_new, failed };
}

// Auto-trigger on URL with intake_candidate_id query param (arrived via /intake deep link)
if (
  location.host.includes("zhipin.com") &&
  location.pathname.includes("/web/chat")
) {
  // Dedup per candidate_id so repeated URL changes don't re-spam the same candidate.
  // Cleared only on a new candidate_id arriving.
  window.__intake_lastRunCandidate = null;
  window.__intake_running = false;

  async function intake_runOnceForDeepLink() {
    const cid = intake_getQueryParam("intake_candidate_id");
    if (!cid) return;
    if (window.__intake_running) return;
    if (window.__intake_lastRunCandidate === cid) return;
    window.__intake_running = true;
    window.__intake_lastRunCandidate = cid;
    try {
      await intake_runOrchestrator();
    } finally {
      window.__intake_running = false;
    }
  }

  if (intake_getQueryParam("intake_candidate_id")) {
    setTimeout(intake_runOnceForDeepLink, 1500);
  }

  // Also respond to SPA URL changes (new candidate_id only)
  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      const cid = intake_getQueryParam("intake_candidate_id");
      if (cid && cid !== window.__intake_lastRunCandidate) {
        setTimeout(intake_runOnceForDeepLink, 1500);
      }
    }
  }, 1000);
}
