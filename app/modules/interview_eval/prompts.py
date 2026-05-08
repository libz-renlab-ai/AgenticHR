"""F-interview-eval LLM Prompt 模板 + 版本锁."""
import json

PROMPT_VERSION = "interview_eval_v1"

SYSTEM = """你是一位资深招聘面试评估专家。基于面试转录稿，按给定考察维度对候选人评分。

硬性要求：
1. 所有打分必须基于转录稿中的真实证据，禁止编造，禁止推测候选人未说过的内容
2. 输出严格符合 JSON Schema，禁止额外文字、禁止 markdown 代码块包裹
3. 每个维度至少 1 个证据片段（含 start_ms/end_ms/speaker/text）
4. 转录稿可能有 ASR 误识别，遇到明显错字推断原意，但不改 speaker 归属
5. 禁止评估候选人的口音/语速/外貌/情绪——仅评估表达内容；这是合规红线
"""


def build_user_message(interview_ctx: dict, transcript: list[dict]) -> str:
    """渲染用户侧 prompt（候选人/岗位/转录稿/输出 schema 全套）."""
    transcript_lines = [
        f"[{seg['start_ms']}-{seg['end_ms']}ms][{seg['speaker']}] {seg['text']}"
        for seg in transcript
    ]
    transcript_block = "\n".join(transcript_lines)

    dims_json = json.dumps(
        interview_ctx["assessment_dimensions"], ensure_ascii=False, indent=2,
    )

    return f"""# 候选人
姓名：{interview_ctx['candidate_name']}
学历：{interview_ctx['candidate_education']}
工作经验：{interview_ctx['candidate_years']} 年
当前技能：{interview_ctx['candidate_skills']}

# 岗位
职位：{interview_ctx['job_title']}
考察维度（与下方 dimensions 数组 1-1 对应，name 必须一致）：
{dims_json}

# 面试转录稿（说话人 + 时间戳）
{transcript_block}

# 输出格式（严格 JSON，禁止 markdown 包裹）
{{
  "dimensions": [
    {{
      "name": "...",
      "score": 1-10 整数,
      "reasoning": "≤200 字打分理由",
      "evidence": [{{"start_ms": int, "end_ms": int, "speaker": "interviewer|candidate", "text": "原话"}}]
    }}
  ],
  "hire_recommendation": "strong_hire|hire|hold|no_hire",
  "strengths": ["≤5 条核心优势"],
  "risks": ["≤5 条风险/疑虑"],
  "followups": ["≤5 条建议追问"]
}}
"""
