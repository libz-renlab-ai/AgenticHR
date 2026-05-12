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
