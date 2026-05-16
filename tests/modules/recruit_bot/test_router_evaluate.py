"""POST /api/recruit/evaluate_and_record."""
import pytest
from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbp = tmp_path / "t.db"
    url = f"sqlite:///{dbp}"
    # reuse conftest's _seed_m2_schema pattern
    from tests.modules.recruit_bot.conftest import _seed_m2_schema
    _seed_m2_schema(str(dbp))
    cfg = Config("migrations/alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, "0009")
    command.upgrade(cfg, "head")
    engine = sa.create_engine(url, connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.engine", engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    from app.core.audit import logger as audit_mod
    monkeypatch.setattr(audit_mod, "_session_factory", factory)

    # Mock F2 scorer internals like conftest does
    async def _mock_llm_enhance(base_ev, resume, dim_scores):
        return base_ev
    from app.modules.matching.scorers import evidence as evidence_mod
    monkeypatch.setattr(evidence_mod, "enhance_evidence_with_llm", _mock_llm_enhance)
    from app.modules.matching.scorers import skill as skill_mod
    def _mock_vec(name, candidates, db_session=None, _resume_emb_cache=None):
        if name in candidates: return 0.95
        return 0.0
    monkeypatch.setattr(skill_mod, "_max_vector_similarity", _mock_vec)

    session = factory()
    from app.modules.auth.models import User
    from app.modules.screening.models import Job
    u = User(username="hr1", password_hash="x", daily_cap=1000); session.add(u); session.commit()
    j = Job(
        user_id=u.id, title="后端", jd_text="招 Python",
        competency_model={
            "schema_version": 1,
            "hard_skills": [{"name":"Python","weight":9,"must_have":True}],
            "soft_skills":[],"experience":{"years_min":2,"years_max":5,"industries":[]},
            "education":{"min_level":"本科"},"job_level":"","bonus_items":[],
            "exclusions":[],"assessment_dimensions":[],
            "source_jd_hash":"h","extracted_at":"2026-04-21T00:00:00Z",
        },
        competency_model_status="approved", greet_threshold=30,
    )
    session.add(j); session.commit()
    jid, uid = j.id, u.id
    session.close()

    from app.main import app
    from app.modules.auth.deps import get_current_user_id
    app.dependency_overrides[get_current_user_id] = lambda: uid
    try:
        with TestClient(app) as c:
            yield c, jid, uid
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)


def _body(job_id, boss_id="b1", name="张三", education="本科",
          min_level="本科", prestigious_tags=None, require_prestigious=False):
    return {
        "job_id": job_id,
        "candidate": {
            "name": name, "boss_id": boss_id,
            "education": education, "work_years": 3,
            "intended_job": "后端", "skill_tags": ["Python"],
        },
        "education_filter": {
            "min_level": min_level,
            "prestigious_tags": prestigious_tags or [],
            "require_prestigious": require_prestigious,
        },
    }


def test_evaluate_requires_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.delenv("AGENTICHR_TEST_BYPASS_AUTH", raising=False)
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/recruit/evaluate_and_record", json={"job_id": 1, "candidate": {"name":"a","boss_id":"b"}})
    assert r.status_code == 401


def test_evaluate_returns_should_greet(client):
    c, jid, uid = client
    r = c.post("/api/recruit/evaluate_and_record", json=_body(jid))
    assert r.status_code == 200
    d = r.json()
    assert d["decision"] == "should_greet"
    assert d["resume_id"] is not None


def test_evaluate_rejects_foreign_job(client):
    c, jid, uid = client
    r = c.post("/api/recruit/evaluate_and_record", json=_body(99999))
    assert r.status_code == 404


def test_evaluate_validates_missing_boss_id(client):
    c, jid, uid = client
    body = _body(jid); body["candidate"]["boss_id"] = ""
    r = c.post("/api/recruit/evaluate_and_record", json=body)
    assert r.status_code == 422


def test_evaluate_missing_education_filter_422(client):
    """Task 4: education_filter 必填, 缺失返 422."""
    c, jid, uid = client
    r = c.post("/api/recruit/evaluate_and_record", json={
        "job_id": jid,
        "candidate": {"name": "A", "boss_id": "b1"},
    })
    assert r.status_code == 422


def test_evaluate_require_prestigious_no_tags_422(client):
    """Task 4: require_prestigious=True 但 tags=[] 应 422 (EducationFilter validator)."""
    c, jid, uid = client
    r = c.post("/api/recruit/evaluate_and_record", json={
        "job_id": jid,
        "candidate": {"name": "A", "boss_id": "b1", "education": "本科"},
        "education_filter": {
            "min_level": "本科", "prestigious_tags": [], "require_prestigious": True,
        },
    })
    assert r.status_code == 422


def test_evaluate_rejected_low_education(client):
    """Task 4: 学历低于门槛 → 200 + decision='rejected_low_education'."""
    c, jid, uid = client
    body = _body(jid, education="大专", min_level="硕士")
    r = c.post("/api/recruit/evaluate_and_record", json=body)
    assert r.status_code == 200
    assert r.json()["decision"] == "rejected_low_education"
