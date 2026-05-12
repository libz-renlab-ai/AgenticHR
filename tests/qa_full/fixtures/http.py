"""共享 httpx 客户端,绕开本地代理(避免 127.0.0.1 被代理拦截返 502)。"""
import httpx
import pytest


@pytest.fixture(scope="session")
def http():
    """直连本地后端,不走任何代理。"""
    with httpx.Client(trust_env=False, timeout=10) as c:
        yield c
