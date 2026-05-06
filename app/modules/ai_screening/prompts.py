"""AI 智能筛选 prompt 模板。"""

SYSTEM_PROMPT = """你是资深 HR 面试官 + 技术评估专家。你需要综合岗位 JD 横向对比多份简历 PDF, 并给每位候选人 0-100 分。

打分标尺:
- 90-100: 完全胜任, 强烈推荐
- 75-89: 胜任要求, 推荐
- 60-74: 部分匹配, 可考虑
- 0-59: 不匹配, 不推荐

理由必须 ≤ 80 字, 引用简历具体内容或 JD 关键词, 严禁空话套话。

横向对比要求: 同一批简历整体评分需相对公平, 比如有 1 个非常强、其他平庸, 拉开档次。"""


def render_user_prompt(jd_text: str, candidates: list[dict]) -> str:
    """candidates: [{candidate_id:int, pdf_path:str}, ...]"""
    cand_lines = "\n".join(
        f"- candidate_id={c['candidate_id']}, pdf={c['pdf_path']}"
        for c in candidates
    )
    return f"""请按下面 JD 横向对比候选人, 给每位 0-100 分。

== 岗位 JD ==
{jd_text}

== 候选人简历 ==
{cand_lines}

请用 Read 工具读取每份 PDF 文件, 综合内容打分。

输出严格 JSON 数组, **无任何其他文字、无 markdown 代码块包裹**, 例如:
[{{"candidate_id":1, "score":85, "reason":"5 年 Java + Spring Boot, 主导过 QPS 万级系统, 完全对口 JD 核心要求"}}, ...]

每位候选人都必须出现在数组中。"""


SCREENING_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "candidate_id": {"type": "integer"},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string", "maxLength": 200},
        },
        "required": ["candidate_id", "score", "reason"],
        "additionalProperties": False,
    },
}
