"""F-interview-eval：从腾讯会议录制获取转写稿（Path B 主）或 mp4（Path A 兜底）。

复用 meeting/account_pool 的 Playwright 持久化 profile（已登录态）。

- Path B `scrape_transcript()`：打开录制播放页，直接 scrape 腾讯自带的「逐字稿」
  （说话人 + 时间戳 + 文本）。免费、无 5MB/COS 限制。无逐字稿时抛
  `TranscriptUnavailable`，由 worker 回退 Path A。
- Path A `download()`：播放页「另存为」→「下载至本地」接 mp4，供 tencent_asr
  走 ffmpeg + 腾讯云 ASR。Path B 不可用时的兜底。

=== Task 4 实地 DOM findings（2026-05-14 抓自真实录制页）===
- 录制列表行：tr[class*="recordListRow"]
- 会议号：行内 [class*="recordInfo"] span，文本格式 "会议号：XXX XXX XXX"（带空格）
- 进播放页：点行内视频封面 [class*="VideoCover_Video"]，会开新 tab，URL meeting.tencent.com/ctw/...
- 逐字稿条目：.minutes-module-paragraph-box，每条内：
    .minutes-module-name-time span      → 说话人名
    .minutes-module-p-start-time        → mm:ss 时间戳
    .minutes-module-word                → 正文
- mp4 下载：播放页 div.met-dropdown.saveas-btn（「另存为」）→ 下拉「下载至本地」
"""
from __future__ import annotations

import logging
import os

from playwright.sync_api import sync_playwright

from app.adapters.tencent_meeting_web import browser_data_dir_for, _cleanup_stale_chrome

logger = logging.getLogger(__name__)

RECORD_LIST_URL = "https://meeting.tencent.com/user-center/meeting-record"
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 单录像 ≤2GB（防磁盘写爆）
DOWNLOAD_TIMEOUT_MS = 300_000  # 下载最长等 5 分钟
LAST_SEGMENT_PAD_MS = 3000  # 末条转写无 end，用 start + 此值估算


class TranscriptUnavailable(Exception):
    """Path B 不可用信号 —— 播放页无逐字稿。由 worker 捕获后回退 Path A。"""


# ---------------- 共用：打开录制播放页 ----------------

def _open_player_page(interview):
    """列表页 → 按 meeting_id 找行 → 点视频封面 → 接新 tab → 返回 (ctx, player_page)。

    调用方负责 ctx.close()。

    Raises:
        RuntimeError: 登录态过期 / meeting_id 找不到对应录制
    """
    data_dir = browser_data_dir_for(interview.meeting_account)
    _cleanup_stale_chrome(data_dir)

    p = sync_playwright().start()
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=data_dir, headless=False, timeout=60_000,
        accept_downloads=True,
    )
    ctx._pw = p  # 存 playwright 实例，_close_player 用它完整清理（防 driver 进程泄漏）
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(RECORD_LIST_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(4000)  # SPA 列表渲染

        if "/login" in (page.url or ""):
            raise RuntimeError(
                f"腾讯会议账号 '{interview.meeting_account}' 登录态过期，"
                "请到 meeting.tencent.com 重新扫码登录"
            )

        row = _find_recording_row(page, interview.meeting_id)
        if row is None:
            raise RuntimeError(
                f"录像未生成或已被清理（meeting_id={interview.meeting_id}），"
                "请几分钟后重试，或检查云录制 1GB 配额是否已满"
            )
        cover = row.query_selector('[class*="VideoCover_Video"]')
        if cover is None:
            raise RuntimeError(
                f"录制行内未找到视频封面（meeting_id={interview.meeting_id}），DOM 结构可能已变"
            )
        try:
            with ctx.expect_page(timeout=10_000) as pg:
                cover.click()
            player = pg.value
        except Exception:
            player = page  # 同页跳转兜底
        try:
            player.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        player.wait_for_timeout(5000)  # 逐字稿异步渲染
        return ctx, player
    except Exception:
        _close_player(ctx)
        raise


def _close_player(ctx) -> None:
    """关闭 browser context + 停 playwright 实例（防 driver 子进程泄漏）。"""
    try:
        ctx.close()
    except Exception:
        pass
    try:
        pw = getattr(ctx, "_pw", None)
        if pw is not None:
            pw.stop()
    except Exception:
        pass


def _find_recording_row(page, meeting_id: str):
    """按 meeting_id 在录制列表里找行。会议号文本格式 "会议号：XXX XXX XXX"（带空格）。

    找不到返回 None。
    """
    mid = (meeting_id or "").replace(" ", "").strip()
    if not mid:
        return None
    for row in page.query_selector_all('tr[class*="recordListRow"]'):
        info = row.query_selector('[class*="recordInfo"] span')
        if not info:
            continue
        text = (info.inner_text() or "").replace(" ", "")
        if mid in text:
            return row
    return None


# ---------------- Path B：scrape 转写稿 ----------------

def _parse_ts_to_ms(ts: str) -> int:
    """'mm:ss' 或 'hh:mm:ss' → 毫秒。解析失败返回 0。"""
    try:
        parts = [int(x) for x in ts.strip().split(":")]
    except (ValueError, AttributeError):
        return 0
    if len(parts) == 2:
        return (parts[0] * 60 + parts[1]) * 1000
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    return 0


def _parse_raw_transcript(raw: list[dict]) -> list[dict]:
    """raw: [{name, time, text}] → [{start_ms, end_ms, speaker, text}]。

    - end_ms = 下一条 start_ms；末条 = start_ms + LAST_SEGMENT_PAD_MS
    - 说话人归一：按累计发言时长，发言最少者 → interviewer，其余 → candidate；
      只有一个说话人 → 全部 candidate（保守，与 tencent_asr._map_speakers 同款启发式）
    """
    rows = []
    for r in raw:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        rows.append({
            "name": (r.get("name") or "").strip(),
            "start_ms": _parse_ts_to_ms(r.get("time") or ""),
            "text": text,
        })
    if not rows:
        return []
    for i, row in enumerate(rows):
        row["end_ms"] = (
            rows[i + 1]["start_ms"] if i + 1 < len(rows)
            else row["start_ms"] + LAST_SEGMENT_PAD_MS
        )
    # 说话人归一
    durations: dict[str, int] = {}
    for row in rows:
        durations[row["name"]] = durations.get(row["name"], 0) + (
            row["end_ms"] - row["start_ms"]
        )
    if len(durations) <= 1:
        role = {n: "candidate" for n in durations}
    else:
        interviewer_name = sorted(durations.items(), key=lambda kv: kv[1])[0][0]
        role = {
            n: ("interviewer" if n == interviewer_name else "candidate")
            for n in durations
        }
    return [
        {
            "start_ms": row["start_ms"], "end_ms": row["end_ms"],
            "speaker": role.get(row["name"], "candidate"), "text": row["text"],
        }
        for row in rows
    ]


def scrape_transcript(interview) -> list[dict]:
    """Path B：scrape 腾讯会议播放页「逐字稿」。

    返回 [{start_ms, end_ms, speaker, text}]，speaker 归一 interviewer/candidate。

    Raises:
        TranscriptUnavailable: 播放页无逐字稿（转写未开通 / 体验期已过）→ worker 回退 Path A
        RuntimeError: 登录态过期 / meeting_id 找不到对应录制
    """
    ctx, player = _open_player_page(interview)
    try:
        raw = player.evaluate(
            """() => {
              const items = document.querySelectorAll('.minutes-module-paragraph-box');
              return Array.from(items).map(it => {
                const nameEl = it.querySelector('.minutes-module-name-time span');
                const timeEl = it.querySelector('.minutes-module-p-start-time');
                const wordEl = it.querySelector('.minutes-module-word');
                return {
                  name: nameEl ? (nameEl.textContent || '').trim() : '',
                  time: timeEl ? (timeEl.textContent || '').trim() : '',
                  text: wordEl ? (wordEl.textContent || '').trim() : '',
                };
              });
            }"""
        )
        segments = _parse_raw_transcript(raw or [])
        if not segments:
            raise TranscriptUnavailable(
                f"播放页无逐字稿（meeting_id={interview.meeting_id}），"
                "转写可能未开通或免费体验期已过"
            )
        logger.info(
            "scrape_transcript: meeting_id=%s → %d segments",
            interview.meeting_id, len(segments),
        )
        return segments
    finally:
        _close_player(ctx)


# ---------------- Path A：下载 mp4（兜底）----------------

def _click_video_download(player) -> None:
    """播放页：另存为 → 下载至本地 → 原视频文件。

    调用方需把本函数包在 player.expect_download() 上下文里。

    Raises:
        RuntimeError: 未找到「另存为」/「下载至本地」；或「原视频文件」不可下载
                      （转写记录类型 / 需腾讯会议付费版 —— DOM 上 display:none）
    """
    header = player.query_selector('.saveas-btn .met-dropdown__header')
    if header is None:
        raise RuntimeError("播放页未找到「另存为」按钮，DOM 结构可能已变")
    header.click()
    player.wait_for_timeout(1000)

    submenu = player.query_selector('li.tea-list__submenu')  # 含「下载至本地」
    if submenu is None:
        raise RuntimeError("「另存为」下拉未找到「下载至本地」子菜单")
    submenu.hover()
    player.wait_for_timeout(800)

    video_li = player.query_selector(
        'li.tea-list__submenu .tea-dropdown-box li:has-text("原视频文件")'
    )
    if video_li is None or not video_li.is_visible():
        raise RuntimeError(
            "「原视频文件」下载不可用 —— 该录制可能是转写记录类型，"
            "或下载原视频需腾讯会议付费版"
        )
    video_li.click()


def download(interview, dest_path: str) -> tuple[str, int, int]:
    """Path A 兜底：下载录制 mp4（另存为 → 下载至本地 → 原视频文件）。

    返回 (path, size_bytes, duration_sec)。duration 在播放页抓不到，填 0。

    Raises:
        RuntimeError: 登录态过期 / meeting_id 找不到 / 原视频文件不可下载 / 超 2GB
    """
    ctx, player = _open_player_page(interview)
    try:
        with player.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
            _click_video_download(player)
        dl_info.value.save_as(dest_path)

        size = os.path.getsize(dest_path)
        if size > MAX_DOWNLOAD_BYTES:
            try:
                os.remove(dest_path)
            except OSError:
                pass
            raise RuntimeError(
                f"录像超过单文件上限 {MAX_DOWNLOAD_BYTES // (1024**3)}GB，"
                "请缩短会议时长或调整 MAX_DOWNLOAD_BYTES"
            )

        logger.info(
            "download: meeting_id=%s → %s (%d bytes)",
            interview.meeting_id, dest_path, size,
        )
        return dest_path, size, 0
    finally:
        _close_player(ctx)
