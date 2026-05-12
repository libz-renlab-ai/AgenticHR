"""JWT token 生成,跳过登录流程。"""
import os
import time

import jwt
import pytest

JWT_SECRET = os.getenv("JWT_SECRET", "agentichr-jwt-secret-change-in-production")


def make_token(user_id: int = 1, username: str = "qa_user", expires_in: int = 3600) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": int(time.time()) + expires_in,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="session")
def auth_token():
    return make_token()


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="session", autouse=True)
def ensure_qa_user(qa_db_path):
    """确保 user_id=1 的 qa_user 在 DB 中存在(JWT 鉴权后多数端点假设)。"""
    import sqlite3
    import bcrypt
    pwd_hash = bcrypt.hashpw(b"qa_pwd_2026", bcrypt.gensalt()).decode()
    with sqlite3.connect(qa_db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, display_name, is_active, created_at) "
            "VALUES (1, 'qa_user', ?, 'QA Test User', 1, datetime('now'))",
            (pwd_hash,)
        )
        c.commit()
