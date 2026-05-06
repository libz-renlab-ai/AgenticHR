r"""Claude Code CLI 子进程封装。

调用方式:
  claude --print --output-format json --append-system-prompt <SYS> --add-dir <PDF_DIR>
         --allowedTools Read --permission-mode bypassPermissions
         <USER_PROMPT>

输出: claude --print --output-format json 返回:
  {"result": "<assistant text>", "session_id": "...", ...}
我们再把 result 文本用 json 解析, 拿候选人评分数组。

实现说明:
  - 用同步 subprocess.run + asyncio.run_in_executor 包装, 跨 event loop 稳
    (uvicorn 在 Windows 默认 SelectorEventLoop, 不支持 asyncio
    create_subprocess_exec, 会抛 NotImplementedError 空消息).
  - Windows 优先解析到 claude.exe 而非 .cmd shim, 避免老 shim 引用
    cli.js 导致 ENOENT (例: D:\Node2\... 老 npm 安装残留).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
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


def _resolve_claude_binary() -> Optional[str]:
    """解析 claude 真实可执行路径。

    Windows 上优先选 claude.exe, 跳过有问题的 .cmd / no-ext bash launcher
    (老 npm global install 残留可能让 .cmd 内调用 cli.js 不存在).

    返回 None 表示找不到。
    """
    explicit = os.environ.get("CLAUDE_CLI_PATH")
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        # explicit 可能是 'claude' 等名字, 走 which
        which = shutil.which(explicit)
        if which:
            return which
        return None

    if sys.platform == "win32":
        # 优先级:
        # 1. PATH 上 claude.cmd / claude (npm shim) 推它的 bin/claude.exe
        #    新版 (2.1.129+) 是单文件 .exe 自包含, 旧 standalone .exe 仍引 cli.js
        # 2. shutil.which("claude.exe") 兜底
        # 3. shutil.which("claude") 直 .cmd
        cmd = shutil.which("claude")
        if cmd:
            cmd_path = Path(cmd)
            candidate = cmd_path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            if candidate.is_file():
                return str(candidate)
        exe = shutil.which("claude.exe")
        if exe:
            # 检查 standalone .exe 同目录可执行性 (避免 npm orphan install)
            return exe
        return cmd

    # 非 Windows: shutil.which 即可
    return shutil.which("claude")


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
    """子进程句柄, 暴露给 worker 用于 cancel。

    用同步 subprocess.Popen 而非 asyncio Process。
    """

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None

    def terminate(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except (ProcessLookupError, OSError):
                pass


def _run_claude_sync(
    args: list[str],
    stdin_text: str,
    timeout: int,
    handle: Optional[ClaudeProcessHandle],
) -> tuple[int, bytes, bytes]:
    """同步跑 claude 子进程, 返 (returncode, stdout, stderr)。

    跨 event loop 稳 (不依赖 asyncio subprocess)。
    prompt 通过 stdin 传, 避免 --add-dir nargs='+' greedy 吃 positional prompt。
    """
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if handle is not None:
        handle.proc = proc
    try:
        stdout, stderr = proc.communicate(
            input=stdin_text.encode("utf-8"), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        raise CliError(f"claude batch timeout after {timeout}s")
    return proc.returncode, stdout, stderr


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

    binary = _resolve_claude_binary()
    if not binary:
        raise CliError(
            f"claude binary not found via PATH or CLAUDE_CLI_PATH={CLAUDE_BIN!r}"
        )

    # prompt 通过 stdin 传, 避免 --add-dir nargs='+' 吃掉位置参数
    args = [binary, "--print", "--output-format", "json"]
    # --add-dir 必须在 --print 之后, 但要在末尾 group, 后面跟非 nargs+ option
    for d in pdf_dirs:
        args.extend(["--add-dir", d])
    args.extend([
        "--append-system-prompt", SYSTEM_PROMPT,
        "--allowedTools", "Read",
        "--permission-mode", "bypassPermissions",
    ])

    logger.info(
        "spawning claude batch: bin=%s candidates=%d dirs=%d timeout=%ds",
        binary, len(candidates), len(pdf_dirs), timeout,
    )

    loop = asyncio.get_event_loop()
    try:
        rc, stdout, stderr = await loop.run_in_executor(
            None, _run_claude_sync, args, user_prompt, timeout, handle,
        )
    except FileNotFoundError as e:
        raise CliError(f"claude binary not found: {binary} ({e})")

    if rc != 0:
        # cancel 时 returncode 通常 = -15 / 1
        err_text = stderr.decode("utf-8", errors="replace")[:500] if stderr else ""
        raise CliError(f"claude exit={rc}; stderr={err_text}")

    return parse_claude_response(stdout)


def detect_claude_cli() -> bool:
    """快速检测 claude binary 是否可用。同步函数, 启动时调用。"""
    return _resolve_claude_binary() is not None
