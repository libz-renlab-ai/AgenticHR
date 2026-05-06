"""共享测试 fixtures"""
import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
import app.modules.resume.models  # noqa: F401 — ensure models registered with Base
import app.modules.screening.models  # noqa: F401
import app.modules.scheduling.models  # noqa: F401
import app.modules.notification.models  # noqa: F401
import app.modules.matching.models  # noqa: F401
import app.modules.matching.decision_model  # noqa: F401 — JobCandidateDecision
import app.modules.ai_screening.models  # noqa: F401 — ScreeningJob / ScreeningJobItem
import app.core.audit.models  # noqa: F401
import app.modules.im_intake.models  # noqa: F401
import app.modules.im_intake.candidate_model  # noqa: F401
import app.modules.im_intake.settings_model  # noqa: F401
import app.modules.im_intake.outbox_model  # noqa: F401 — intake_outbox table
import app.modules.auth.models  # noqa: F401 — IntakeCandidate.user_id FK -> users.id

# Allow test client requests to pass through the JWT auth HTTP middleware.
# The middleware checks both this env var AND PYTEST_CURRENT_TEST (set by pytest
# automatically). With bypass active the middleware skips token validation; the
# get_current_user_id dependency is then overridden separately in the client
# fixture below to return a fixed user_id of 1.
os.environ.setdefault("AGENTICHR_TEST_BYPASS_AUTH", "1")


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        "sqlite:///./test.db",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    # Seed baseline users referenced by FKs (e.g. IntakeCandidate.user_id).
    # The client fixture overrides get_current_user_id to return 1, so user 1
    # must exist. User 2 is also seeded for multi-tenancy scoping tests.
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "INSERT OR IGNORE INTO users (id, username, password_hash, display_name, is_active, daily_cap) "
            "VALUES (0,'legacy','x','Legacy',1,1000), "
            "(1,'tester1','x','Tester1',1,1000), "
            "(2,'tester2','x','Tester2',1,1000)"
        ))
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="function")
def client(db_session):
    from app.main import app as fastapi_app
    from app.modules.auth.deps import get_current_user_id

    def override_get_db():
        yield db_session

    def override_get_current_user_id():
        return 1  # fixed test user_id; bypasses JWT auth for router tests

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    with TestClient(fastapi_app) as c:
        yield c
    fastapi_app.dependency_overrides.clear()
