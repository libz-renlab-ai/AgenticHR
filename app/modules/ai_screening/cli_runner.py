"""Claude Code CLI 子进程封装。

调用方式:
  claude --print --output-format json --append-system-prompt <SYS> --add-dir <PDF_DIR>
         --allowedTools Read --permission-mode bypassPermissions
         <USER_PROMPT>

输出: claude --print --output-format json 返回:
  {"result": "<assistant text>", "session_id": "...", ...}
我们再把 result 文本用 json 解析, 拿候选人评分数组。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

from app.modules.ai_screening.prompts import (
    SYSTEM_PROMPT,
    render_user_prompt,
)

logger = logging.getLogger(__name__)

# 单批默认 5min
DEFAULT_TIMEOUT = 300

# 候选 binary; 允许 env 覆盖
CLAUDE_BIN = os.environ.get("CLAUDE_CLI_PATH", "claude")


class CliError(Exception):
    pass


def _strip_markdown_fence(text: str) -> str:
    """去掉可能存在的 ```json ... ``` 包裹。"""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.+?)\s*```$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def parse_claude_response(stdout: bytes) -> list[dict]:
    """从 claude --print --output-format json 的 stdout 解析候选人评分数组。

    Claude 输出格式 (json):
      {"result": "<text>", ...}
    text 期望是 JSON 数组 (可能被 markdown 包裹)。
    """
    try:
        wrapper = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise CliError(f"claude wrapper json decode failed: {e}; stdout={stdout[:300]!r}")

    result_text = wrapper.get("result", "")
    if not result_text:
        raise CliError(f"claude result empty; wrapper={wrapper}")

    cleaned = _strip_markdown_fence(result_text)
    try:
        arr = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # 容错: 在文本里抓第一个 [...] JSON 数组
        m = re.search(r"(\[.*\])", cleaned, re.DOTALL)
        if not m:
            raise CliError(f"no JSON array in result: {cleaned[:300]}")
        try:
            arr = json.loads(m.group(1))
        except json.JSONDecodeError as e2:
            raise CliError(f"json array parse failed: {e2}; text={cleaned[:300]}")

    if not isinstance(arr, list):
        raise CliError(f"expected JSON array, got {type(arr).__name__}")

    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        cid = item.get("candidate_id")
        score = item.get("score")
        reason = item.get("reason", "")
        if not isinstance(cid, int):
            continue
        if not isinstance(score, (int, float)):
            continue
        score_int = max(0, min(100, int(score)))
        out.append({
            "candidate_id": cid,
            "score": score_int,
            "reason": str(reason)[:500],
        })
    return out


def _resolve_pdf_dirs(pdf_paths: list[str]) -> list[str]:
    """收集 PDF 文件父目录唯一集合, 给 claude --add-dir。"""
    dirs = set()
    for p in pdf_paths:
        if not p:
            continue
        try:
            d = os.path.dirname(os.path.abspath(p))
            if d and os.path.isdir(d):
                dirs.add(d)
        except Exception:
            continue
    return sorted(dirs)


class ClaudeProcessHandle:
    """子进程句柄, 暴露给 worker 用于 cancel。"""

    def __init__(self):
        self.proc: Optional[asyncio.subprocess.Process] = None

    def terminate(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass


async def run_claude_batch(
    jd_text: str,
    candidates: list[dict],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    handle: Optional[ClaudeProcessHandle] = None,
) -> list[dict]:
    """跑一批候选人横向打分。

    candidates: [{candidate_id, pdf_path}, ...]  长度建议 ≤ 10
    返回: [{candidate_id, score, reason}, ...]
    raise CliError on failure.
    """
    user_prompt = render_user_prompt(jd_text, candidates)
    pdf_dirs = _resolve_pdf_dirs([c["pdf_path"] for c in candidates])

    args = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "json",
        "--append-system-prompt", SYSTEM_PROMPT,
        "--allowedTools", "Read",
        "--permission-mode", "bypassPermissions",
    ]
    for d in pdf_dirs:
        args.extend(["--add-dir", d])
    args.append(user_prompt)

    logger.info(
        "spawning claude batch: candidates=%d, dirs=%d, timeout=%ds",
        len(candidates), len(pdf_dirs), timeout,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise CliError(f"claude binary not found: {CLAUDE_BIN} ({e})")

    if handle is not None:
        handle.proc = proc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        raise CliError(f"claude batch timeout after {timeout}s")

    if proc.returncode != 0:
        # cancel 时 returncode 通常 = -15 / 1
        err_text = stderr.decode("utf-8", errors="replace")[:500] if stderr else ""
        raise CliError(
            f"claude exit={proc.returncode}; stderr={err_text}"
        )

    return parse_claude_response(stdout)


def detect_claude_cli() -> bool:
    """快速检测 claude binary 是否可用。同步函数, 启动时调用。"""
    import shutil
    return shutil.which(CLAUDE_BIN) is not None
