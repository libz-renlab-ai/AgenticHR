"""ai_parse worker 单测 — Round 10 BUG-132/143/144/146 修复验证."""
import asyncio

from sqlalchemy.orm import sessionmaker

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.resume._ai_parse_worker import _coerce_work_years
from app.modules.resume.models import Resume


class TestBug143CoerceWorkYears:
    """BUG-143: LLM 偶尔返字符串/含数字短语, work_years 应能容错为 int."""

    def test_int_passthrough(self):
        assert _coerce_work_years(5) == 5

    def test_float_truncated(self):
        assert _coerce_work_years(5.7) == 5

    def test_string_digit(self):
        assert _coerce_work_years("5") == 5

    def test_string_with_unit(self):
        assert _coerce_work_years("5 年") == 5
        assert _coerce_work_years("5年工作经验") == 5

    def test_string_decimal(self):
        assert _coerce_work_years("5.5") == 5

    def test_empty_or_invalid_returns_zero(self):
        assert _coerce_work_years(None) == 0
        assert _coerce_work_years("") == 0
        assert _coerce_work_years("无") == 0
        assert _coerce_work_years({"key": 5}) == 0

    def test_bool_returns_zero(self):
        # bool is subclass of int — 显式拒绝
        assert _coerce_work_years(True) == 0
        assert _coerce_work_years(False) == 0


class TestBug132NormalizeEducationFallback:
    """BUG-132: normalize_education 失败时, _ai_parse_worker 应落库 ""
    而非 raw LLM 值. 通过验证 normalize 行为间接覆盖."""

    def test_unknown_education_normalizes_to_empty_string(self):
        from app.modules.resume.pdf_parser import normalize_education
        # 这些是 BUG-126 字典覆盖不到的非规范值
        assert normalize_education("中专") == ""
        assert normalize_education("高中") == ""
        assert normalize_education("大学") == ""  # 模糊词不取


class TestDoParseAllCoversIntakeCandidates:
    """根因回归: /resumes 页面列表来自 IntakeCandidate; 批量 worker 必须解析它们。

    用户报告: 点'手动启动内容解析'后页面零效果 —— 旧 worker 只查 Resume 表。
    """

    def _setup_session(self, db_engine, monkeypatch):
        """把 worker 内部的 SessionLocal 指向测试引擎, LLM 与 F2 触发打桩。"""
        import app.modules.resume._ai_parse_worker as worker
        import app.modules.matching.triggers as triggers

        TestSession = sessionmaker(
            bind=db_engine, autocommit=False, autoflush=False
        )
        monkeypatch.setattr(worker, "SessionLocal", TestSession)

        async def _fake_parse(raw_text, ai_provider):
            return {"name": "已解析", "skills": "Go, gRPC"}

        monkeypatch.setattr(
            "app.modules.resume.pdf_parser.ai_parse_resume", _fake_parse
        )

        async def _noop_trigger(db, resume_id):
            return None

        monkeypatch.setattr(triggers, "on_resume_parsed", _noop_trigger)

    def test_processes_unparsed_intake_candidate(
        self, db_session, db_engine, monkeypatch
    ):
        from app.modules.resume._ai_parse_worker import _do_parse_all

        self._setup_session(db_engine, monkeypatch)
        c = IntakeCandidate(
            user_id=1, boss_id="bw1", name="未知",
            raw_text="候选人简历原文原文原文", ai_parsed="no",
            intake_status="complete", source="plugin",
        )
        db_session.add(c)
        db_session.commit()
        cid = c.id

        asyncio.run(_do_parse_all(user_id=1))

        db_session.expire_all()
        refreshed = db_session.query(IntakeCandidate).filter_by(id=cid).first()
        assert refreshed.ai_parsed == "yes", "批量 worker 应把 IntakeCandidate 解析为 yes"
        assert refreshed.skills == "Go, gRPC"
        # 应 promote 出 Resume 作 matching FK 锚点
        assert refreshed.promoted_resume_id is not None

    def test_skips_other_users_candidates(
        self, db_session, db_engine, monkeypatch
    ):
        from app.modules.resume._ai_parse_worker import _do_parse_all

        self._setup_session(db_engine, monkeypatch)
        other = IntakeCandidate(
            user_id=2, boss_id="bw2", name="他人",
            raw_text="他人简历原文原文", ai_parsed="no",
            intake_status="complete", source="plugin",
        )
        db_session.add(other)
        db_session.commit()
        oid = other.id

        asyncio.run(_do_parse_all(user_id=1))

        db_session.expire_all()
        refreshed = db_session.query(IntakeCandidate).filter_by(id=oid).first()
        assert refreshed.ai_parsed == "no", "worker 不应碰其他用户的候选人"
