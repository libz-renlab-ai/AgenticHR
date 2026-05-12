"""调 claude -p haiku-4-5 判定截图。"""
import json
import subprocess
from pathlib import Path

import jinja2

TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "verifier_prompts"
TEMPLATE_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=False,
)


def verify_screenshot(
    screenshot_path: Path,
    test_id: str,
    feature_desc: str,
    expected_visible: list[str],
    expected_absent: list[str],
    artifacts_dir: Path,
) -> dict:
    """返回 {passed, reason, raw}。failed 时 raw 含原始 claude 输出供诊断。"""
    tmpl = TEMPLATE_ENV.get_template("ui_screenshot.md.j2")
    prompt = tmpl.render(
        test_id=test_id,
        feature_desc=feature_desc,
        expected_visible=expected_visible,
        expected_absent=expected_absent,
    )
    # claude CLI 不直接支持 --image; 把图片绝对路径写在 prompt 里
    full_prompt = (
        f"[图片路径: {screenshot_path}]\n\n请读取该 PNG 文件并判定。\n\n{prompt}"
    )
    cmd = [
        "claude", "-p", full_prompt,
        "--model", "claude-haiku-4-5",
        "--output-format", "json",
    ]
    log_dir = artifacts_dir / "verifier_calls"
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        (log_dir / f"{test_id}.timeout.txt").write_text("verifier timeout", encoding="utf-8")
        return {"passed": False, "reason": "verifier 120s timeout", "raw": ""}

    raw = res.stdout
    (log_dir / f"{test_id}.txt").write_text(raw, encoding="utf-8", errors="replace")
    try:
        outer = json.loads(raw)
        inner_text = outer.get("result", outer.get("content", raw))
        if isinstance(inner_text, str):
            # 尝试从内层文本里抠 JSON
            try:
                inner = json.loads(inner_text)
            except Exception:
                # 文本中可能有非 JSON 解释,试找 {...}
                import re
                m = re.search(r'\{[^{}]*"passed"[^{}]*\}', inner_text)
                inner = json.loads(m.group(0)) if m else {}
        else:
            inner = inner_text
        return {
            "passed": bool(inner.get("passed", False)),
            "reason": inner.get("reason", ""),
            "raw": raw,
        }
    except Exception as e:
        return {"passed": False, "reason": f"verifier 解析失败: {e}", "raw": raw[:500]}
