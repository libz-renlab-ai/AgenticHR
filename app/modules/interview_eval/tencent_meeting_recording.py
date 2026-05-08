"""F-interview-eval：复用 meeting/account_pool 的 Playwright profile，
从 meeting.tencent.com/user-center/meeting-record 抓 mp4 下载。

约束：腾讯会议免费版只能在 web 端管理本人录制；超出 1GB 配额会失败。
"""
from __future__ import annotations

import logging
from typing import Any
import requests
from playwright.sync_api import sync_playwright

# NOTE: plan 引用 `app.modules.meeting.account_pool.browser_data_dir_for`，
# 但实际函数定义在 `app.adapters.tencent_meeting_web`（同一份持久化 profile 约定）。
# 调整 import 路径，其余语义不变。
from app.adapters.tencent_meeting_web import browser_data_dir_for

logger = logging.getLogger(__name__)

RECORD_LIST_URL = "https://meeting.tencent.com/user-center/meeting-record"


def _open_record_page(account_label: str):
    """返回 (browser_context, page)；调用方负责关闭。"""
    p = sync_playwright().start()
    user_data_dir = browser_data_dir_for(account_label)
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir, headless=False, timeout=60_000,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(RECORD_LIST_URL, wait_until="networkidle")
    return ctx, page


def _stream_download(url: str, dest: str) -> int:
    """流式下载 mp4 到 dest，返回 bytes。"""
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk); size += len(chunk)
    return size


def download(interview, dest_path: str) -> tuple[str, int, int]:
    """根据 interview.meeting_id 在 interview.meeting_account 账号下抓 mp4。

    返回 (path, size_bytes, duration_sec)。

    Raises:
        RuntimeError: 登录过期 / 录像不存在 / 网络失败 / 配额满
    """
    ctx, page = _open_record_page(interview.meeting_account)
    try:
        # 检查是否被跳到登录页
        if "/login" in (page.url or ""):
            raise RuntimeError(
                f"腾讯会议账号 '{interview.meeting_account}' 登录态过期，"
                "请到 meeting.tencent.com 重新扫码登录"
            )

        # 在录制列表里挑 meeting_id 匹配的行
        records: list[dict[str, Any]] = page.evaluate("""
() => {
  // 录制列表是 SPA 渲染的，DOM 结构以实际页面为准；
  // 实际部署时由 maintainer 抓页面 DOM 后填实下面的 selector。
  // 此处给出抽象骨架：
  const items = Array.from(document.querySelectorAll('[data-record-item]'));
  return items.map(el => ({
    meeting_id: el.getAttribute('data-meeting-id') || '',
    mp4_url: el.querySelector('a[data-mp4]')?.getAttribute('href') || '',
    duration_sec: parseInt(el.getAttribute('data-duration') || '0', 10),
  }));
}
""")
        match = next(
            (r for r in records if r["meeting_id"] == interview.meeting_id), None
        )
        if match is None or not match.get("mp4_url"):
            raise RuntimeError(
                f"录像未生成或已被清理（meeting_id={interview.meeting_id}），"
                "请几分钟后重试，或检查云录制 1GB 配额是否已满"
            )

        size = _stream_download(match["mp4_url"], dest_path)
        return dest_path, size, int(match.get("duration_sec") or 0)
    finally:
        try:
            ctx.close()
        except Exception:
            pass
