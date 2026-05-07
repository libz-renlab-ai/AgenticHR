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

# BUG-136: 改 permission-mode 默认 'acceptEdits' (Edit/Write 自动放行 + Read 仅 add-dir
# 内自动放行, 之外仍需提示). 历史 bypassPermissions 让 LLM 越狱后可读任意文件
# (~/.claude/credentials.json, .env 等), 即使 SYSTEM_PROMPT 加了 BUG-104 边界提示也只
# 是指令级软约束。
# 真正非交互需要 + 用户允许 broad 读取时, 可设 CLAUDE_PERMISSION_MODE=bypassPermissions 显式回退。
# 取值: default | acceptEdits | bypassPermissions | plan
_ALLOWED_PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "plan"}
_DEFAULT_PERMISSION_MODE = "acceptEdits"


def _resolve_permission_mode() -> str:
    raw = (os.environ.get("CLAUDE_PERMISSION_MODE") or "").strip()
    if raw in _ALLOWED_PERMISSION_MODES:
        return raw
    return _DEFAULT_PERMISSION_MODE


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


def _try_extract_json_arrays(text: str) -> list:
    """尝试从文本里提取 JSON 数组。BUG-094: 改非贪婪 + 多次尝试 raw_decode。

    会扫描所有候选 `[...]` JSON 数组, 优先返回含 dict 元素 (即评分对象数组) 的那一个;
    否则降级返回任意第一个 list。
    """
    decoder = json.JSONDecoder()
    candidates: list[list] = []
    i = 0
    while i < len(text):
        idx = text.find("[", i)
        if idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            i = idx + 1
            continue
        if isinstance(obj, list):
            candidates.append(obj)
            # 第一个含 dict 的数组就是我们要的评分对象数组
            if any(isinstance(x, dict) for x in obj):
                return obj
        i = idx + end
    return candidates[0] if candidates else None  # type: ignore[return-value]


def parse_claude_response(stdout: bytes) -> list[dict]:
    """从 claude --print --output-format json 的 stdout 解析候选人评分数组。

    Claude 输出格式 (json):
      {"result": "<text>", ...}
    text 期望是 JSON 数组 (可能被 markdown 包裹)。

    单候选人级别 try/except: BUG-093 (NaN crash), BUG-101 (cid float), BUG-113 (越界).
    """
    try:
        wrapper = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise CliError(f"claude wrapper json decode failed: {e}; stdout={stdout[:300]!r}")

    result_text = wrapper.get("result", "")
    if not result_text:
        raise CliError(f"claude result empty; wrapper={wrapper}")

    # BUG-138: claude CLI 升级后 result 字段可能是 dict/list (非 string),
    # 直接 .strip() 会 AttributeError 500. 转 CliError 让 worker 走 _mark_batch_error 兜底。
    if not isinstance(result_text, str):
        raise CliError(
            f"claude result field is not a string (type={type(result_text).__name__}, "
            f"upstream CLI may have changed output schema); preview={str(result_text)[:200]}"
        )

    cleaned = _strip_markdown_fence(result_text)
    arr = None
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, list):
            arr = loaded
    except json.JSONDecodeError:
        pass
    if arr is None:
        arr = _try_extract_json_arrays(cleaned)
    if arr is None:
        raise CliError(f"no JSON array in result: {cleaned[:300]}")
    if not isinstance(arr, list):
        raise CliError(f"expected JSON array, got {type(arr).__name__}")

    out = []
    seen_cids: set[int] = set()  # BUG-137: 同 cid 多次时仅保留首个
    for item in arr:
        if not isinstance(item, dict):
            continue
        cid = item.get("candidate_id")
        score = item.get("score")
        reason = item.get("reason", "")
        # BUG-101: cid 容许 float 但要求无小数部分
        if isinstance(cid, float):
            if not cid.is_integer():
                continue
            cid = int(cid)
        if not isinstance(cid, int) or isinstance(cid, bool):
            continue
        # BUG-137: 重复 cid 取首个并 log warning (LLM stuttering 时, last-write-wins
        # 会让分数错乱). 这里 dedup 在 parse 边界, worker 收到清洁数据。
        if cid in seen_cids:
            logger.warning(
                "duplicate candidate_id=%s in LLM output; keeping first, dropping subsequent",
                cid,
            )
            continue
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        # BUG-093: NaN/Infinity 单候选跳过, 不影响整批
        try:
            f = float(score)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):
            continue
        # BUG-113: 越界不静默 clamp, 单候选标记返回 (worker 写 error, 不进 pass)
        if f < 0 or f > 100:
            out.append({
                "candidate_id": cid,
                "score": None,
                "reason": "",
                "error": f"LLM 评分越界: {f}",
            })
            seen_cids.add(cid)
            continue
        out.append({
            "candidate_id": cid,
            "score": int(f),
            "reason": str(reason)[:500],
        })
        seen_cids.add(cid)
    return out


_SENSITIVE_PAT = re.compile(
    r"(sk-[a-zA-Z0-9_\-]{8,}|api[_-]?key=\S+|token=[A-Za-z0-9._\-]+|"
    r"Bearer\s+[A-Za-z0-9._\-]+)",
    re.IGNORECASE,
)


def _redact_sensitive(text: str) -> str:
    """BUG-105: 屏蔽 stderr 中的 token/api key 等敏感字符串。"""
    if not text:
        return text
    return _SENSITIVE_PAT.sub("[REDACTED]", text)


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
    binary_path: Optional[str] = None,
) -> list[dict]:
    """跑一批候选人横向打分。

    candidates: [{candidate_id, pdf_path}, ...]  长度建议 ≤ 10
    返回: [{candidate_id, score, reason}, ...]
    raise CliError on failure.

    BUG-102: binary_path 给定时不再 resolve, 直接用. router.start 时锁定写到
    ScreeningJob.cli_path, worker 跑时读出来传进, 避免 router/worker 两次
    resolve 结果不一致 (PATH 环境变更窗口)。
    """
    user_prompt = render_user_prompt(jd_text, candidates)
    pdf_dirs = _resolve_pdf_dirs([c["pdf_path"] for c in candidates])

    binary = binary_path or _resolve_claude_binary()
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
        # BUG-136: 默认 acceptEdits 限定 Read 仅在 --add-dir 内自动放行;
        # 之外路径要求人工确认 (--print 模式下会失败但不静默泄露). 用户显式
        # 设 CLAUDE_PERMISSION_MODE=bypassPermissions 才回退到旧的全开放行为。
        "--permission-mode", _resolve_permission_mode(),
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
        # BUG-105: stderr 含 token/api key 等敏感字符串 → 屏蔽再入库
        raw_err = stderr.decode("utf-8", errors="replace") if stderr else ""
        err_text = _redact_sensitive(raw_err)[:500]
        raise CliError(f"claude exit={rc}; stderr={err_text}")

    return parse_claude_response(stdout)


def detect_claude_cli() -> bool:
    """快速检测 claude binary 是否可用。同步函数, 启动时调用。"""
    return _resolve_claude_binary() is not None


def resolve_claude_binary() -> Optional[str]:
    """对外暴露的 binary path resolver, 供 router.start 调用锁定到 ScreeningJob.cli_path。"""
    return _resolve_claude_binary()
