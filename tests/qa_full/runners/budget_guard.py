"""跟踪本轮的外部调用次数,超额抛错。"""
import json
from pathlib import Path
from threading import Lock


class BudgetExceeded(Exception):
    pass


class BudgetGuard:
    def __init__(self, artifacts_dir: Path):
        self.path = artifacts_dir / "budget.json"
        self._lock = Lock()
        self._caps = {
            "asr_seconds": 300,
            "llm_tokens": 200_000,
            "feishu_calls": 100,
            "tencent_meeting_create": 3,
            "boss_operations": 0,
        }
        if self.path.exists():
            self._used = json.loads(self.path.read_text())
        else:
            self._used = {k: 0 for k in self._caps}

    def consume(self, key: str, amount: int = 1):
        with self._lock:
            if key not in self._caps:
                return
            self._used[key] = self._used.get(key, 0) + amount
            if self._used[key] > self._caps[key]:
                raise BudgetExceeded(
                    f"{key}: 用了 {self._used[key]} 超过 {self._caps[key]}"
                )
            self.path.write_text(json.dumps(self._used, indent=2))

    def report(self) -> dict:
        return {k: f"{self._used.get(k, 0)}/{self._caps[k]}" for k in self._caps}
