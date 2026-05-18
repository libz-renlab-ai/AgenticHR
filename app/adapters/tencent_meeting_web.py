# -*- coding: utf-8 -*-
"""Tencent Meeting web automation via Playwright.

多账号支持：每个账号标签对应 data/meeting_browser_{label}/ 一个独立的 Chrome
持久化目录。首次用某个新标签时，Playwright 会打开可见浏览器等候最多 120 秒
让你人工扫码登录；登录态之后常驻，后续调用无需再次登录。

新增账号的操作：
  1. 在 .env 里把新标签加进 TENCENT_MEETING_ACCOUNTS（逗号分隔，比如 zhang,li）
  2. 重启后端
  3. 在前端点"创建腾讯会议"，让调度器把任务分配到新账号时，扫码登录新账号即可

[2026-05-15] Windows 下 uvicorn 主循环是 SelectorEventLoop，不支持 subprocess
（`asyncio.create_subprocess_exec` 直接 `raise NotImplementedError`），所以
async_playwright().start() 必崩。直接在新线程里建 ProactorEventLoop 也水土不服
（Playwright 的 Node driver 一握手就 "Connection closed while reading from the
driver"）。最终方案：模块内部统一用 **sync_playwright**（Playwright 自带的同步
封装会自己管线程+loop，已被 tencent_meeting_recording.py 验证可行），由对外
async 入口通过 `asyncio.to_thread(...)` 把整个同步流程扔到工作线程。
"""
import asyncio
import logging
import os
import re
import sys
import time

logger = logging.getLogger(__name__)


def _ensure_proactor_loop_in_thread() -> None:
    """让本进程后续 `asyncio.new_event_loop()` 拿到 ProactorEventLoop（Windows 必需）。

    背景：uvicorn 在启动时把全局 policy 设成 WindowsSelectorEventLoopPolicy，
    HTTP/WebSocket 能跑，但 Selector loop 不支持 subprocess
    —— `asyncio.create_subprocess_exec` 直接 `raise NotImplementedError`。
    sync_playwright 内部用 `asyncio.new_event_loop()` 构造自己的 loop，吃的是
    **全局 policy**，所以只把线程 loop 换成 Proactor 没用，必须改 policy。

    解决：把全局 policy 切到 ProactorEventLoopPolicy。uvicorn 主循环已经在跑
    自己的 Selector loop，不受新 policy 影响；之后任何 `new_event_loop()` 调用
    都会拿到 Proactor，subprocess 能 spawn。HTTP 处理本身不受影响。
    """
    if sys.platform != "win32":
        return
    policy = asyncio.get_event_loop_policy()
    if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        logger.info("Switched global event loop policy to WindowsProactorEventLoopPolicy")

SCHEDULE_URL = "https://meeting.tencent.com/user-center/user-meeting-list/schedule"


def browser_data_dir_for(account_label: str) -> str:
    """Return the persistent Chrome user-data-dir for the given account label."""
    safe = "".join(c for c in account_label if c.isalnum() or c in ("-", "_")) or "default"
    return os.path.abspath(f"data/meeting_browser_{safe}")


def _cleanup_stale_chrome(browser_data_dir: str) -> None:
    """杀掉任何还占着这个 profile 的 Chrome 进程，并删掉锁文件。

    场景：上一次 Playwright 任务异常退出（Ctrl+C / 进程被杀 / RepeatMeetingModal
    超时后 Python 崩了）时，Chrome 进程树可能没被清干净，Profile 里的 lockfile
    /SingletonLock 也会残留。下一次 launch 会直接 exit 21（文件占用）或进不去。
    这个函数在每次 launch_persistent_context 之前跑一遍，幂等且无破坏性。
    """
    import subprocess

    # 1) 杀掉用这个 profile 的所有 chrome.exe
    try:
        # 用 wmic 按 command line 匹配，找到对应 PID
        result = subprocess.run(
            [
                "wmic", "process", "where",
                f"name='chrome.exe' and commandline like '%{os.path.basename(browser_data_dir)}%'",
                "get", "ProcessId",
            ],
            capture_output=True, text=True, timeout=5,
        )
        pids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(line)
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                           capture_output=True, timeout=5)
            logger.info(f"Killed stale Chrome PID {pid} from {browser_data_dir}")
    except Exception as e:
        logger.debug(f"Chrome cleanup via wmic failed: {e}")

    # 2) 删锁文件（Windows 用 lockfile；Linux/mac 用 SingletonLock 等）
    for name in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = os.path.join(browser_data_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
                logger.info(f"Removed stale lock file: {p}")
            except Exception as e:
                logger.warning(f"Could not remove {p}: {e}")


def _dismiss_blocking_modals(page):
    """移除所有可能遮挡主界面按钮的弹窗（sync 版）。

    腾讯会议在设置日期/时间后有时会自动弹一个"重复会议"配置对话框
    （class 含 RepeatMeeetingModal），它会拦截点击事件让"预定会议"按钮点不动。
    这个函数按顺序尝试：Escape → 点"不重复" / 关闭按钮 → 直接 DOM 移除。
    """
    # 1) Escape 一下通常能关掉大多数普通弹窗
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)
    except Exception:
        pass

    # 2) 如果弹窗依然存在，尝试点里面的"不重复"或关闭按钮
    for selector in [
        '.met-dialog button:has-text("不重复")',
        '.met-dialog button:has-text("确定")',
        '.met-dialog button:has-text("取消")',
        '.met-dialog .met-modal-close',
        '.met-dialog button[aria-label="Close"]',
    ]:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click(timeout=1500)
                logger.info(f"Dismissed modal via {selector}")
                time.sleep(0.4)
        except Exception:
            continue

    # 3) 最后兜底：直接把所有可见的 .met-dialog 从 DOM 里拿掉
    try:
        removed = page.evaluate("""() => {
            const dialogs = document.querySelectorAll('.met-dialog, .met-modal, [role="dialog"]');
            let count = 0;
            dialogs.forEach(d => {
                if (d.offsetParent !== null) {
                    d.remove();
                    count++;
                }
            });
            // 同时移除可能遮挡事件的 overlay
            const overlays = document.querySelectorAll('.met-modal-mask, .met-overlay');
            overlays.forEach(o => o.remove());
            return count;
        }""")
        if removed:
            logger.info(f"Force-removed {removed} blocking dialog(s)")
    except Exception as e:
        logger.debug(f"DOM cleanup failed: {e}")


def _set_input_value(page, selector, value):
    """Set input value using React-compatible method (sync 版)."""
    page.evaluate(f"""({{ selector, value }}) => {{
        const el = document.querySelector(selector);
        if (!el) return;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }}""", {"selector": selector, "value": value})


def _set_time(page, start_h, start_m, end_h, end_m):
    """Set start and end time by directly manipulating the input values (sync 版)。

    Start time = hour[0], minute[0] (visible, index 0)
    End time = hour[49], minute[49] (visible, index 49)
    """
    # 腾讯会议只支持 00 和 30 分钟，向下取整到最近的 00/30
    def round_min(m):
        m_int = int(m)
        return "00" if m_int < 30 else "30"

    start_m = round_min(start_m)
    end_m = round_min(end_m)

    logger.info(f"Time (rounded to 00/30): {start_h}:{start_m} - {end_h}:{end_m}")

    page.evaluate("""({ sh, sm, eh, em }) => {
        const hours = document.querySelectorAll('input.hour');
        const minutes = document.querySelectorAll('input.minute');

        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;

        function setVal(el, val) {
            setter.call(el, val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }

        // Start time: index 0
        if (hours[0]) setVal(hours[0], sh);
        if (minutes[0]) setVal(minutes[0], sm);

        // End time: index 49
        if (hours[49]) setVal(hours[49], eh);
        if (minutes[49]) setVal(minutes[49], em);
    }""", {"sh": start_h.zfill(2), "sm": start_m.zfill(2),
           "eh": end_h.zfill(2), "em": end_m.zfill(2)})

    # Also click the inputs to trigger any UI update
    hours = page.query_selector_all('input.hour')
    if len(hours) > 49:
        hours[0].click()
        time.sleep(0.2)
        hours[0].press("Escape")
        time.sleep(0.1)
        hours[49].click()
        time.sleep(0.2)
        hours[49].press("Escape")
        time.sleep(0.1)

    logger.info(f"Time set: {start_h}:{start_m} - {end_h}:{end_m}")


async def create_meeting(topic: str, start_date: str, start_time: str,
                         end_date: str, end_time: str,
                         account_label: str = "default") -> dict:
    """Create a Tencent Meeting via web automation (public async wrapper).

    把同步实现扔到 worker 线程：FastAPI handler `await` 它，主事件循环不阻塞；
    sync_playwright 自己开线程跑 Node driver，避开 SelectorEventLoop 限制。
    """
    return await asyncio.to_thread(
        _create_meeting_sync,
        topic, start_date, start_time, end_date, end_time, account_label,
    )


def _create_meeting_sync(topic: str, start_date: str, start_time: str,
                        end_date: str, end_time: str,
                        account_label: str = "default") -> dict:
    """实际跑 Playwright 的同步版本。被 `asyncio.to_thread` 派发到工作线程执行。

    Args:
        topic: Meeting subject
        start_date: "2026/04/10"
        start_time: "14:00"
        end_date: "2026/04/10"
        end_time: "15:00"
        account_label: 账号标签（对应 data/meeting_browser_{label}/ 目录）。

    Returns:
        {"success": True, "meeting_id": "...", "link": "https://...", "password": ""}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "error": "playwright not installed"}

    browser_data_dir = browser_data_dir_for(account_label)
    os.makedirs(browser_data_dir, exist_ok=True)
    logger.info(f"Using Tencent Meeting account '{account_label}' at {browser_data_dir}")

    # 清理上次遗留的僵尸 Chrome 进程和锁文件
    _cleanup_stale_chrome(browser_data_dir)

    # Windows + uvicorn 必须强制 ProactorEventLoop，否则 sync_playwright 启不来 Node driver
    _ensure_proactor_loop_in_thread()

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=browser_data_dir,
            headless=False,
            viewport={"width": 1280, "height": 900},
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(SCHEDULE_URL, timeout=60000)
        time.sleep(4)

        # 落盘各阶段诊断（截图 + HTML），方便排查 UI 失配。
        # 默认关；TENCENT_MEETING_DEBUG=1 才开。开了会在 data/meeting_debug_shots 累积文件。
        _diag_on = os.environ.get("TENCENT_MEETING_DEBUG") == "1"
        diag_dir = os.path.abspath("data/meeting_debug_shots")
        ts = time.strftime("%Y%m%d-%H%M%S")
        if _diag_on:
            os.makedirs(diag_dir, exist_ok=True)
        def _snap(tag: str) -> None:
            if not _diag_on:
                return
            try:
                path = os.path.join(diag_dir, f"{ts}-{tag}.png")
                page.screenshot(path=path, full_page=False)
                logger.info(f"diag snapshot {tag}: {path} | url={page.url}")
            except Exception as exc:
                logger.warning(f"snapshot {tag} failed: {exc}")

        _snap("01-after-goto")

        # Check login
        if 'login' in page.url.lower():
            logger.warning("Tencent Meeting: waiting for login...")
            _snap("02-login-prompt")
            for _ in range(120):
                time.sleep(1)
                if 'login' not in page.url.lower():
                    break
            else:
                _snap("02-login-timeout")
                return {"success": False, "error": "Login timeout"}
            page.goto(SCHEDULE_URL, timeout=60000)
            time.sleep(4)
            _snap("03-after-login")

        # 1. Fill topic
        title_input = page.query_selector('input[placeholder="请输入会议名称"]')
        if title_input:
            title_input.click(click_count=3)
            title_input.fill(topic)
        else:
            _snap("04-no-topic-input")
            return {"success": False, "error": "Cannot find topic input"}

        # 2. Set dates
        date_inputs = page.query_selector_all('input[placeholder="选择日期"]')
        if len(date_inputs) >= 2:
            date_inputs[0].click(click_count=3)
            date_inputs[0].fill(start_date)
            date_inputs[0].press("Enter")
            time.sleep(0.5)

            date_inputs[1].click(click_count=3)
            date_inputs[1].fill(end_date)
            date_inputs[1].press("Enter")
            time.sleep(0.5)

        # 3. Set times
        start_h, start_m = start_time.split(":")
        end_h, end_m = end_time.split(":")
        _set_time(page, start_h, start_m, end_h, end_m)
        time.sleep(1)

        # 3.5 设置完时间后，腾讯会议有时会弹"重复会议"对话框遮住提交按钮
        _dismiss_blocking_modals(page)
        _snap("05-before-submit")

        # 4. Submit（两次尝试：先正常点，失败则清弹窗后强制点）
        submit = page.query_selector('button:has-text("预定会议")')
        if submit:
            try:
                submit.click(timeout=5000)
                logger.info("Submit clicked normally")
            except Exception as e:
                logger.warning(f"Submit click blocked ({e}); dismissing modals and retrying with force")
                _dismiss_blocking_modals(page)
                time.sleep(0.5)
                try:
                    submit.click(force=True, timeout=5000)
                    logger.info("Submit clicked with force=True")
                except Exception as e2:
                    logger.error(f"Submit click failed even with force: {e2}")
                    raise
            time.sleep(3)

        # 5. Handle dialog
        ok_btn = page.query_selector('text=我知道了')
        if ok_btn:
            ok_btn.click()
            time.sleep(3)

        # May need second click
        if 'schedule' in page.url:
            submit2 = page.query_selector('button:has-text("预定会议")')
            if submit2:
                submit2.click()
                time.sleep(8)

        _snap("06-after-submit")

        # 6. Wait for detail page
        for _ in range(15):
            if 'detailed-meeting-info' in page.url or 'meeting_id' in page.url:
                break
            time.sleep(1)

        _snap("07-final")

        # 7. Extract meeting info
        html = page.content()
        text = page.inner_text('body')
        if _diag_on:
            try:
                with open(os.path.join(diag_dir, f"{ts}-07-final.html"), "w", encoding="utf-8") as _fh:
                    _fh.write(html)
            except Exception:
                pass

        link_match = re.search(r'https://meeting\.tencent\.com/dm/[a-zA-Z0-9]+', html)
        meeting_link = link_match.group() if link_match else ""

        id_match = re.search(r'(\d{3}\s?\d{3}\s?\d{3})', text)
        meeting_id = id_match.group().replace(' ', '') if id_match else ""

        pwd_match = re.search(r'会议密码[：:]\s*(\d+)', text)
        meeting_pwd = pwd_match.group(1) if pwd_match else ""

        if meeting_link:
            logger.info(f"Meeting created: {meeting_link} (ID: {meeting_id})")
            return {
                "success": True,
                "meeting_id": meeting_id,
                "link": meeting_link,
                "password": meeting_pwd,
            }
        # 没拿到链接：看看表单里有没有红字校验提示，抓出来回给前端
        form_errors: list[str] = []
        try:
            for sel in [
                '.met-form-item-error',          # element-ui-like
                '.met-form-item-error-tip',
                '.met-form-error',
                '[class*="errorTip"]',
                '[class*="ErrorTip"]',
                'div.error-tip',
                'span.error-tip',
            ]:
                for el in page.query_selector_all(sel):
                    txt = (el.inner_text() or "").strip()
                    if txt and txt not in form_errors:
                        form_errors.append(txt)
        except Exception:
            pass
        if form_errors:
            joined = "; ".join(form_errors[:3])
            return {"success": False, "error": f"腾讯会议表单校验未通过：{joined}"}
        return {"success": False, "error": "Meeting link not found after submit"}

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Meeting creation failed: {e!r}\n{tb}")
        # str(NotImplementedError()) 是空串，会让前端只看到 detail=""；用 repr 兜底
        return {"success": False, "error": str(e) or repr(e) or "未知错误"}
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


async def cancel_meeting(meeting_id: str, account_label: str = "default") -> dict:
    """Cancel a Tencent Meeting (public async wrapper)。"""
    return await asyncio.to_thread(
        _cancel_meeting_sync, meeting_id, account_label,
    )


def _cancel_meeting_sync(meeting_id: str, account_label: str = "default") -> dict:
    """在腾讯会议网页上取消某场会议（同步实现，跑在 worker 线程）。

    Args:
        meeting_id: 腾讯会议的 9 位会议号（去除空格）
        account_label: 主持该会议的账号标签（决定用哪个 profile 登录）

    Returns:
        {"success": True}              成功取消
        {"success": False, "error": ...} 失败

    Selector 依据（通过实地 DOM 检查得到）：
    - 每一行会议是 `<tr class="{meeting_id_no_space}">`，class 名直接就是会议号
    - 行内取消按钮是 `button.cancle-meeting-btn`（腾讯自己拼错了 "cancel"）
    - 即将召开的会议页直达 URL：/user-center/user-meeting-list/current
    """
    if not meeting_id:
        return {"success": False, "error": "meeting_id 为空"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "error": "playwright not installed"}

    browser_data_dir = browser_data_dir_for(account_label)
    if not os.path.exists(browser_data_dir):
        return {"success": False, "error": f"账号 '{account_label}' 的浏览器目录不存在"}

    _cleanup_stale_chrome(browser_data_dir)
    mid = meeting_id.strip().replace(" ", "")
    logger.info(f"Cancelling Tencent meeting {mid} via account '{account_label}'")

    # Windows + uvicorn 必须强制 ProactorEventLoop，否则 sync_playwright 启不来 Node driver
    _ensure_proactor_loop_in_thread()

    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=browser_data_dir,
            headless=False,
            viewport={"width": 1400, "height": 900},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # 直达"即将召开的会议"tab
        page.goto(
            "https://meeting.tencent.com/user-center/user-meeting-list/current",
            timeout=60000,
        )
        time.sleep(2)
        if "login" in page.url.lower():
            return {"success": False, "error": f"登录态失效，请先手动登录账号 {account_label}"}

        # 等 tbody 有行再继续
        try:
            page.wait_for_selector("tbody tr", timeout=15000)
        except Exception:
            return {"success": False, "error": "会议列表未加载（该账号可能没有任何即将召开的会议）"}

        _dismiss_blocking_modals(page)
        time.sleep(1)

        # 用 attr selector 精确命中这行（class 是一串数字，不能用 CSS 的 .xxx 语法）
        row = page.query_selector(f'tr[class="{mid}"]')
        if not row:
            # 备用：按会议号带空格的 title 属性找
            if len(mid) == 9:
                spaced = f"{mid[0:3]} {mid[3:6]} {mid[6:9]}"
                row = page.query_selector(f'tr:has(span[title="{spaced}"])')
        if not row:
            logger.warning(f"Meeting {mid} not found in upcoming tab")
            return {"success": False, "error": f"未在即将召开列表中找到会议 {mid}（可能已过期或被取消）"}

        logger.info(f"Located row for meeting {mid}")

        # 行内取消按钮
        cancel_btn = row.query_selector("button.cancle-meeting-btn")
        if not cancel_btn:
            return {"success": False, "error": "行内未找到 cancle-meeting-btn（DOM 结构可能已变）"}

        try:
            cancel_btn.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        cancel_btn.click(timeout=5000)
        logger.info("Cancel button clicked, waiting for confirm dialog")
        time.sleep(1)

        # 确认弹窗：先等 .met-dialog 出现
        try:
            page.wait_for_selector(
                '.met-dialog, [role="dialog"]',
                state="visible",
                timeout=5000,
            )
        except Exception:
            logger.info("No confirm dialog appeared — maybe cancelled directly")

        # 多种确认按钮 selector 回退
        confirmed = False
        for selector in [
            '.met-dialog button:has-text("确认取消")',
            '.met-dialog button:has-text("确定")',
            '.met-dialog button:has-text("确认")',
            '[role="dialog"] button:has-text("确认取消")',
            '[role="dialog"] button:has-text("确定")',
            '.met-dialog .met-btn--primary',
            '.met-dialog button.primary',
        ]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click(timeout=3000)
                    logger.info(f"Confirmed via {selector}")
                    confirmed = True
                    break
            except Exception:
                continue

        if not confirmed:
            logger.warning("No confirm button matched; attempting Enter key")
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        # 校验：等这行在表格里消失（证明取消成功）
        disappeared = False
        for _ in range(10):
            time.sleep(1)
            still_there = page.query_selector(f'tr[class="{mid}"]')
            if not still_there:
                disappeared = True
                break

        if disappeared:
            logger.info(f"Meeting {mid} successfully cancelled (row disappeared)")
            return {"success": True}
        else:
            logger.warning(f"Meeting {mid} row still present after confirm click")
            return {"success": False, "error": "点了取消和确认，但列表里该会议仍存在"}

    except Exception as e:
        logger.error(f"Cancel meeting failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
