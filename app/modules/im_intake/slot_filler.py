import json
import logging
import re
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

PROMPT_PARSE = (Path(__file__).parent / "prompts" / "parse_v1.txt").read_text(encoding="utf-8")


# Per-slot keyword whitelist used as a post-LLM sanity check.
# Background: glm-4-flash sometimes hallucinates by assigning a greeting-only
# message (e.g. "请问全栈工程师还在招吗？我很感兴趣") to free_slots, which then
# silences the follow-up question. We require the LLM-picked quote to contain
# at least one plausibly-related keyword before accepting it. Patterns are
# regex strings checked with re.search (case-insensitive).
_SLOT_KEYWORD_PATTERNS: dict[str, list[str]] = {
    "arrival_date": [
        r"到岗", r"入职", r"上岗", r"报到", r"立即", r"马上", r"随时",
        r"今天", r"明天", r"后天", r"本周", r"下周", r"下下周",
        r"周[一二三四五六日天]", r"星期[一二三四五六日天]",
        r"\d+\s*月", r"\d+\s*号", r"\d+\s*日", r"\d+\s*天",
        r"现在", r"目前", r"随时可", r"可以来", r"能来", r"过来",
        r"\d{1,2}[./-]\d{1,2}", r"假期", r"答辩", r"毕业",
    ],
    "intern_duration": [
        r"实习", r"个月", r"半年", r"\d+\s*月", r"\d+\s*周",
        r"长期", r"短期", r"持续", r"周期", r"做多久", r"干多久",
        r"\d+\s*年", r"\d+\s*天", r"一直", r"至少", r"最多",
        r"出勤", r"五天", r"每周", r"全职", r"兼职",
    ],
    # free_slots 要求"时间锚"——周X / 上午晚上 / 数字+点 / 面试/约时间/有空/没空
    # 等。故意不把单独的"可以/不行/方便"加入,否则会误判"希望可以进一步沟通"
    # 这种纯礼貌话术(2026-05-13 马婧 case)。
    "free_slots": [
        r"面试", r"约时间", r"约面", r"时段", r"空闲", r"有空", r"没空",
        r"上午", r"下午", r"晚上", r"中午", r"早上", r"傍晚",
        r"周[一二三四五六日天]", r"星期[一二三四五六日天]",
        r"\d{1,2}\s*[点:：]", r"\d{1,2}\s*-\s*\d{1,2}",
        r"今天", r"明天", r"后天", r"本周", r"下周",
        r"\d{1,2}[./-]\d{1,2}", r"出勤",
    ],
}


def _quote_matches_slot(slot_key: str, quote: str) -> bool:
    """Return True if `quote` contains at least one keyword indicating it is
    plausibly an answer for `slot_key`. Returns True for unknown slot keys
    (e.g. soft_q_*) to avoid over-filtering."""
    patterns = _SLOT_KEYWORD_PATTERNS.get(slot_key)
    if not patterns:
        return True
    return any(re.search(p, quote, flags=re.IGNORECASE) for p in patterns)


class LLMLike(Protocol):
    async def complete(self, messages: list[dict], response_format: str = "json", **kw) -> str: ...


class SlotFiller:
    """LLM-driven slot extractor.

    Approach: ask the LLM ONLY to pick which message indices belong to which
    slot, then look up the original message content server-side. The LLM
    never gets to rewrite, summarize, normalize, or invent text — those are
    the failure modes we kept hitting (e.g. '周三晚上8-9不行' getting flipped
    to '周三晚上8-9 可用', or '周五' being hallucinated when the candidate
    only mentioned 周二/周三). Whatever the candidate actually said is what
    shows up in the slot value, byte-for-byte.

    regex 方案早已移除：'明天晚上没空' 里的'明天'会被当作到岗时间，
    '4月25'里的'4月'会被当作'4个月'实习时长，语义完全错乱。
    """

    def __init__(self, llm: LLMLike | None = None):
        self.llm = llm

    async def parse_conversation(
        self,
        messages: list[dict],
        candidate_boss_id: str,
        pending_slot_keys: list[str],
    ) -> dict[str, tuple[Any, str]]:
        """Return {slot_key: (value, source)} for slots the LLM populated.

        `messages` is the full conversation [{sender_id, content}, ...].
        We number each non-empty message [#i] so the LLM can refer to it by
        index; we then reconstruct the slot value from the raw `content`.
        """
        if not messages or not pending_slot_keys or self.llm is None:
            return {}

        # Build numbered lines and a parallel index → original-content map.
        # We only retain candidate messages in the lookup table — even if the
        # LLM mistakenly returns an HR-message index, we won't surface HR
        # words as the candidate's quoted slot value.
        lines: list[str] = []
        candidate_msgs: dict[int, str] = {}
        idx = 0
        for m in messages:
            sender = m.get("sender_id")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            role = "候选人" if sender == candidate_boss_id else "HR"
            lines.append(f"[#{idx}] {role}: {content}")
            if role == "候选人":
                candidate_msgs[idx] = content
            idx += 1
        conversation = "\n".join(lines)

        safe_conversation = conversation.replace("{", "{{").replace("}", "}}")
        prompt = PROMPT_PARSE.format(conversation=safe_conversation, pending_keys=pending_slot_keys)
        try:
            raw = await self.llm.complete(
                messages=[{"role": "user", "content": prompt}],
                response_format="json",
                temperature=0.0,
                prompt_version="parse_v3_indices",
                f_stage="intake",
                entity_type="intake_slot",
            )
            data = json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"SlotFiller LLM parse failed: {e}")
            return {}

        result: dict[str, tuple[Any, str]] = {}
        for key in pending_slot_keys:
            v = data.get(key)
            if v in (None, "", []):
                continue
            # Accept either a list of ints (new schema) or, defensively, a
            # legacy string (old prompt). For lists, look up the original
            # candidate-message content; preserve the LLM's order so timeline
            # reads naturally.
            if isinstance(v, list):
                quotes: list[str] = []
                for raw_idx in v:
                    try:
                        i = int(raw_idx)
                    except (TypeError, ValueError):
                        continue
                    text = candidate_msgs.get(i)
                    if text and text not in quotes:
                        quotes.append(text)
                if not quotes:
                    continue
                # Sanity check: drop LLM picks whose quote text contains no
                # keyword plausibly related to this slot — defends against
                # the model harvesting greeting-only or off-topic messages
                # into hard slots (2026-05-13 马婧 case).
                quotes = [q for q in quotes if _quote_matches_slot(key, q)]
                if not quotes:
                    logger.info(
                        "SlotFiller dropped LLM pick for %s: no slot-relevant keyword in any quote",
                        key,
                    )
                    continue
                # Newline-joined so multi-line candidate utterances stay
                # readable — '|' delimited squashes them and reads worse.
                joined = "\n".join(quotes)
                result[key] = (joined, "llm")
            elif isinstance(v, str):
                s = v.strip()
                if s:
                    result[key] = (s, "llm")
        return result
