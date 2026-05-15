"""list_matched_for_job 严格按 job_id 过滤 — spec-2026-05-15-job-binding Round 2."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.modules.auth.models  # noqa: F401
import app.modules.resume.models  # noqa: F401
import app.modules.screening.models  # noqa: F401
import app.modules.im_intake.candidate_model  # noqa: F401
import app.modules.im_intake.models  # noqa: F401
import app.modules.matching.decision_model  # noqa: F401

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.im_intake.templates import HARD_SLOT_KEYS
from app.modules.screening.models import Job
from app.modules.resume.intake_view_service import list_matched_for_job


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _job(db, *, title, user_id=1) -> Job:
    j = Job(user_id=user_id, title=title)
    db.add(j); db.commit(); db.refresh(j)
    return j


def _candidate(db, *, boss_id, name, job_id, user_id=1) -> IntakeCandidate:
    """新建 candidate + 四项齐全 hard slots + pdf_path,默认能进 _complete_query。"""
    c = IntakeCandidate(
        user_id=user_id, boss_id=boss_id, name=name,
        job_id=job_id, pdf_path="data/x.pdf",
        intake_status="collecting",
        education="本科", school_tier="985",
    )
    db.add(c); db.commit(); db.refresh(c)
    for key in HARD_SLOT_KEYS:
        db.add(IntakeSlot(
            candidate_id=c.id, slot_key=key, slot_category="hard",
            value="v", ask_count=1,
        ))
    db.commit()
    return c


class TestStrictJobBinding:
    """严格模式: 默认只返 candidate.job_id == job_id 的候选人."""

    def test_strict_returns_only_own_job_candidates(self, db):
        pm = _job(db, title="产品经理")
        dev = _job(db, title="开发")
        _candidate(db, boss_id="b_pm_1", name="产A", job_id=pm.id)
        _candidate(db, boss_id="b_pm_2", name="产B", job_id=pm.id)
        _candidate(db, boss_id="b_dev_1", name="开A", job_id=dev.id)

        pm_rows = list_matched_for_job(db, user_id=1, job_id=pm.id, strict=True)
        dev_rows = list_matched_for_job(db, user_id=1, job_id=dev.id, strict=True)

        assert {r["name"] for r in pm_rows} == {"产A", "产B"}, (
            f"产品岗 strict 模式只应见产 A/B,实际 {[r['name'] for r in pm_rows]}"
        )
        assert {r["name"] for r in dev_rows} == {"开A"}, (
            f"开发岗 strict 模式只应见开 A,实际 {[r['name'] for r in dev_rows]}"
        )

    def test_strict_hides_null_job_id_orphans(self, db):
        pm = _job(db, title="产品经理")
        _candidate(db, boss_id="b_orphan", name="孤儿", job_id=None)
        _candidate(db, boss_id="b_pm", name="产A", job_id=pm.id)

        rows = list_matched_for_job(db, user_id=1, job_id=pm.id, strict=True)
        assert {r["name"] for r in rows} == {"产A"}, (
            f"strict 模式应隐藏 job_id=NULL 的孤儿,实际 {[r['name'] for r in rows]}"
        )

    def test_show_all_returns_all_passing_hard_gates(self, db):
        """show_all 等价于 strict=False,旧行为兜底."""
        pm = _job(db, title="产品经理")
        dev = _job(db, title="开发")
        _candidate(db, boss_id="b_pm", name="产A", job_id=pm.id)
        _candidate(db, boss_id="b_dev", name="开A", job_id=dev.id)
        _candidate(db, boss_id="b_orphan", name="孤儿", job_id=None)

        rows = list_matched_for_job(db, user_id=1, job_id=pm.id, strict=False)
        assert {r["name"] for r in rows} == {"产A", "开A", "孤儿"}, (
            f"strict=False 应返全部过硬筛,实际 {[r['name'] for r in rows]}"
        )

    def test_default_strict_is_false_at_service_level(self, db):
        """服务层默认 strict=False (后向兼容已有调用方);
        HTTP 入口层默认 strict=True 见 test_router_passed_resumes_strict.py。"""
        pm = _job(db, title="产品经理")
        dev = _job(db, title="开发")
        _candidate(db, boss_id="b_pm", name="产A", job_id=pm.id)
        _candidate(db, boss_id="b_dev", name="开A", job_id=dev.id)

        rows_default = list_matched_for_job(db, user_id=1, job_id=pm.id)
        # 默认应等价 strict=False,见两个人
        assert len(rows_default) == 2, (
            f"服务层默认 strict=False,产品岗调用应见 2 个,实际 {len(rows_default)}"
        )

    def test_strict_user_scoping_preserved(self, db):
        """strict 与 user_id 隔离正交,不应让其他用户的候选人漏进来."""
        pm_u1 = _job(db, title="产品经理", user_id=1)
        # 用户 2 也建一个产品经理岗位 + 候选人, 用 user_id 1 查 user_id 1 的岗位
        # 不应漏入用户 2 的人
        pm_u2 = Job(user_id=2, title="产品经理"); db.add(pm_u2); db.commit(); db.refresh(pm_u2)
        _candidate(db, boss_id="b_u1", name="U1", job_id=pm_u1.id, user_id=1)
        _candidate(db, boss_id="b_u2", name="U2", job_id=pm_u2.id, user_id=2)

        rows = list_matched_for_job(db, user_id=1, job_id=pm_u1.id, strict=True)
        assert {r["name"] for r in rows} == {"U1"}


class TestHttpEndpointDefaultStrict:
    """HTTP /api/matching/passed-resumes/{job_id} 默认 strict=True."""

    def test_http_default_filters_by_job(self, client, db_session):
        pm = Job(user_id=1, title="产品经理")
        dev = Job(user_id=1, title="开发")
        db_session.add(pm); db_session.add(dev); db_session.commit()
        db_session.refresh(pm); db_session.refresh(dev)
        for boss_id, name, job_id in [
            ("b_h_pm", "产", pm.id),
            ("b_h_dev", "开", dev.id),
        ]:
            c = IntakeCandidate(
                user_id=1, boss_id=boss_id, name=name, job_id=job_id,
                pdf_path="data/x.pdf", intake_status="collecting",
                education="本科", school_tier="985",
            )
            db_session.add(c); db_session.commit(); db_session.refresh(c)
            for key in HARD_SLOT_KEYS:
                db_session.add(IntakeSlot(
                    candidate_id=c.id, slot_key=key, slot_category="hard",
                    value="v", ask_count=1,
                ))
        db_session.commit()

        resp = client.get(f"/api/matching/passed-resumes/{pm.id}")
        assert resp.status_code == 200
        names = {it["name"] for it in resp.json()}
        assert names == {"产"}, (
            f"HTTP 默认严格,产品岗只应见 '产',实际 {names}"
        )

    def test_http_show_all_overrides(self, client, db_session):
        pm = Job(user_id=1, title="产品经理")
        dev = Job(user_id=1, title="开发")
        db_session.add(pm); db_session.add(dev); db_session.commit()
        db_session.refresh(pm); db_session.refresh(dev)
        for boss_id, name, job_id in [
            ("b_h_pm2", "产", pm.id),
            ("b_h_dev2", "开", dev.id),
        ]:
            c = IntakeCandidate(
                user_id=1, boss_id=boss_id, name=name, job_id=job_id,
                pdf_path="data/x.pdf", intake_status="collecting",
                education="本科", school_tier="985",
            )
            db_session.add(c); db_session.commit(); db_session.refresh(c)
            for key in HARD_SLOT_KEYS:
                db_session.add(IntakeSlot(
                    candidate_id=c.id, slot_key=key, slot_category="hard",
                    value="v", ask_count=1,
                ))
        db_session.commit()

        resp = client.get(f"/api/matching/passed-resumes/{pm.id}?show_all=true")
        assert resp.status_code == 200
        names = {it["name"] for it in resp.json()}
        assert names == {"产", "开"}, (
            f"show_all=true 应返全部过硬筛,实际 {names}"
        )
