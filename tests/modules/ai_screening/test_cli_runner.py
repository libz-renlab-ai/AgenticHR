"""cli_runner 单测。子进程交互用 mock; 仅 parse 用真实数据。"""
import json

import pytest

from app.modules.ai_screening.cli_runner import (
    CliError,
    _strip_markdown_fence,
    parse_claude_response,
    _resolve_pdf_dirs,
)


class TestStripFence:
    def test_no_fence(self):
        assert _strip_markdown_fence("[1,2]") == "[1,2]"

    def test_with_json_fence(self):
        assert _strip_markdown_fence("```json\n[1,2]\n```") == "[1,2]"

    def test_with_plain_fence(self):
        assert _strip_markdown_fence("```\n[1,2]\n```") == "[1,2]"


class TestParseResponse:
    def _wrap(self, text: str) -> bytes:
        return json.dumps({"result": text}).encode("utf-8")

    def test_clean_array(self):
        text = '[{"candidate_id":1,"score":85,"reason":"good"}]'
        out = parse_claude_response(self._wrap(text))
        assert out == [{"candidate_id": 1, "score": 85, "reason": "good"}]

    def test_with_markdown_fence(self):
        text = '```json\n[{"candidate_id":2,"score":70,"reason":"ok"}]\n```'
        out = parse_claude_response(self._wrap(text))
        assert out[0]["candidate_id"] == 2

    def test_clamps_score_range(self):
        text = '[{"candidate_id":1,"score":150,"reason":"x"},{"candidate_id":2,"score":-10,"reason":"y"}]'
        out = parse_claude_response(self._wrap(text))
        assert out[0]["score"] == 100
        assert out[1]["score"] == 0

    def test_skips_invalid_items(self):
        text = '[{"candidate_id":1,"score":80,"reason":"a"},{"score":90,"reason":"missing id"},{"candidate_id":3,"score":"NaN","reason":"x"}]'
        out = parse_claude_response(self._wrap(text))
        ids = [o["candidate_id"] for o in out]
        assert 1 in ids
        assert 3 not in ids  # score not numeric

    def test_extracts_array_from_chatty_text(self):
        text = '这是结果:\n[{"candidate_id":5,"score":60,"reason":"r"}]\n谢谢'
        out = parse_claude_response(self._wrap(text))
        assert out[0]["candidate_id"] == 5

    def test_empty_result_raises(self):
        with pytest.raises(CliError):
            parse_claude_response(json.dumps({"result": ""}).encode())

    def test_invalid_wrapper_raises(self):
        with pytest.raises(CliError):
            parse_claude_response(b"not json at all")

    def test_non_array_raises(self):
        with pytest.raises(CliError):
            parse_claude_response(self._wrap('{"foo": "bar"}'))


class TestResolveDirs:
    def test_dedup_dirs(self, tmp_path):
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.touch()
        f2.touch()
        sub = tmp_path / "sub"
        sub.mkdir()
        f3 = sub / "c.pdf"
        f3.touch()
        dirs = _resolve_pdf_dirs([str(f1), str(f2), str(f3)])
        assert len(dirs) == 2
        assert str(tmp_path) in dirs
        assert str(sub) in dirs

    def test_skips_empty_paths(self):
        dirs = _resolve_pdf_dirs(["", None])
        assert dirs == []
