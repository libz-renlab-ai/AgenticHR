"""QA full coverage 测试套件全局 fixtures。"""
from pathlib import Path

import pytest

QA_ROOT = Path(__file__).parent
REPO_ROOT = QA_ROOT.parent.parent
ARTIFACTS_BASE = REPO_ROOT / "artifacts"


def pytest_addoption(parser):
    parser.addoption("--round", type=int, default=1, help="QA round number")
    parser.addoption("--boss", action="store_true", help="enable Boss zhipin tests")


@pytest.fixture(scope="session")
def round_no(request):
    return request.config.getoption("--round")


@pytest.fixture(scope="session")
def artifacts_dir(round_no):
    d = ARTIFACTS_BASE / f"round-{round_no}"
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    (d / "responses").mkdir(parents=True, exist_ok=True)
    (d / "verifier_calls").mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    return d


def pytest_collection_modifyitems(config, items):
    if config.getoption("--boss"):
        return
    skip_boss = pytest.mark.skip(reason="--boss not provided")
    for item in items:
        if "boss" in item.keywords:
            item.add_marker(skip_boss)


# fixtures 接入
from tests.qa_full.fixtures.db import qa_db_path, qa_db_url
from tests.qa_full.fixtures.auth import auth_token, auth_headers, ensure_qa_user
from tests.qa_full.fixtures.http import http
from tests.qa_full.runners.budget_guard import BudgetGuard


@pytest.fixture(scope="session")
def budget(artifacts_dir):
    return BudgetGuard(artifacts_dir)


@pytest.fixture(scope="session")
def api_base(qa_db_url, artifacts_dir):
    from tests.qa_full.runners.server_lifecycle import uvicorn_running
    log = artifacts_dir / "logs" / "uvicorn.log"
    with uvicorn_running(qa_db_url, log) as base:
        yield base


@pytest.fixture(scope="session")
def frontend_base(api_base, artifacts_dir):
    from tests.qa_full.runners.server_lifecycle import vite_running
    log = artifacts_dir / "logs" / "vite.log"
    with vite_running(log) as base:
        yield base


# Playwright 相关只在装了才接入
try:
    from tests.qa_full.fixtures.browser import (
        playwright_instance, browser, page, shoot,
    )
except ImportError:
    pass
