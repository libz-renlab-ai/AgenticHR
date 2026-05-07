from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot
from app.modules.resume.models import Resume
from app.modules.im_intake.promote import promote_to_resume


def _s():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_promote_creates_resume_and_links():
    s = _s()
    c = IntakeCandidate(
        user_id=1,
        boss_id="bx1", name="李四", job_id=None, intake_status="collecting",
        source="plugin", pdf_path="/tmp/bx1.pdf",
        intake_started_at=datetime.now(timezone.utc),
    )
    s.add(c); s.commit()

    resume = promote_to_resume(s, c, user_id=1)
    s.commit()

    assert resume.id is not None
    assert resume.boss_id == "bx1"
    assert resume.name == "李四"
    assert resume.pdf_path == "/tmp/bx1.pdf"
    assert resume.intake_status == "complete"
    assert resume.status == "passed"

    s.refresh(c)
    assert c.promoted_resume_id == resume.id
    assert c.intake_status == "complete"
    assert c.intake_completed_at is not None


def test_promote_idempotent():
    s = _s()
    c = IntakeCandidate(user_id=1, boss_id="bx1", name="A",
                        intake_status="collecting", source="plugin")
    s.add(c); s.commit()
    r1 = promote_to_resume(s, c, user_id=1); s.commit()
    r2 = promote_to_resume(s, c, user_id=1); s.commit()
    assert r1.id == r2.id
    assert s.query(Resume).count() == 1


def test_promote_rejects_orphan_user_id():
    """BUG-047: user_id<=0 raises ValueError instead of creating orphan row."""
    import pytest
    s = _s()
    c = IntakeCandidate(user_id=1, boss_id="orph", name="O",
                        intake_status="collecting", source="plugin")
    s.add(c); s.commit()
    with pytest.raises(ValueError):
        promote_to_resume(s, c, user_id=0)
    with pytest.raises(ValueError):
        promote_to_resume(s, c, user_id=-1)


def test_promote_copies_structured_fields_to_new_resume():
    """BUG-123: promote 必须把 candidate 上抽出的结构化字段一并复制到新 Resume,
    否则下游五维 scorer 拿到的全是空值, skill/experience/industry 全 0."""
    s = _s()
    c = IntakeCandidate(
        user_id=1, boss_id="b123", name="王五",
        intake_status="collecting", source="plugin",
        pdf_path="/tmp/w5.pdf", raw_text="原文",
        # 结构化字段——以前 promote 全部丢
        phone="13800001234", email="w5@example.com",
        education="硕士", bachelor_school="清华大学",
        master_school="北京大学", phd_school="",
        skills="Python, RAG, LangGraph",
        work_experience="字节跳动 - 后端实习生 - Agent 中台",
        project_experience="多智能体编排平台",
        self_evaluation="主动学习",
        job_intention="后端工程师",
        work_years=1, seniority="初级",
        expected_salary_min=12000.0, expected_salary_max=18000.0,
        qr_code_path="data/qr/b123.png",
        ai_parsed="yes", ai_summary="自驱型应届生",
    )
    s.add(c); s.commit()

    r = promote_to_resume(s, c, user_id=1); s.commit()
    s.refresh(r)

    assert r.phone == "13800001234"
    assert r.email == "w5@example.com"
    assert r.education == "硕士"
    assert r.bachelor_school == "清华大学"
    assert r.master_school == "北京大学"
    assert r.skills == "Python, RAG, LangGraph"
    assert r.work_experience == "字节跳动 - 后端实习生 - Agent 中台"
    assert r.project_experience == "多智能体编排平台"
    assert r.self_evaluation == "主动学习"
    assert r.job_intention == "后端工程师"
    assert r.work_years == 1
    assert r.seniority == "初级"
    assert r.expected_salary_min == 12000.0
    assert r.expected_salary_max == 18000.0
    assert r.qr_code_path == "data/qr/b123.png"
    assert r.ai_parsed == "yes"
    assert r.ai_summary == "自驱型应届生"


def test_promote_merge_does_not_clobber_richer_resume():
    """BUG-123: 已存在的 boss_id Resume 走 merge 路径时, 原来非空字段不被覆盖,
    只补空缺字段; F3 抓取的丰富数据应被保留."""
    s = _s()
    pre = Resume(
        user_id=1, boss_id="bm1", name="老张",
        skills="Java, Spring",
        education="本科",
        work_experience="老经历",
        source="boss_zhipin",
    )
    s.add(pre); s.commit()

    c = IntakeCandidate(
        user_id=1, boss_id="bm1", name="老张",
        intake_status="collecting", source="plugin",
        pdf_path="/tmp/bm1.pdf",
        skills="Python",          # ← 不应覆盖 Resume 已有 "Java, Spring"
        education="硕士",          # ← 不应覆盖 Resume 已有 "本科"
        bachelor_school="复旦大学",  # ← Resume 没填, 应该补上
        work_years=3,
    )
    s.add(c); s.commit()

    r = promote_to_resume(s, c, user_id=1); s.commit()
    s.refresh(r)

    # 已有非空字段保留
    assert r.skills == "Java, Spring"
    assert r.education == "本科"
    assert r.work_experience == "老经历"
    # 空缺字段被补上
    assert r.bachelor_school == "复旦大学"
    assert r.work_years == 3
