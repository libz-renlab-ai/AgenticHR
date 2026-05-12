"""测试套件起停 uvicorn / vite,绑独立 DB + 独立端口。"""
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent.parent.parent

QA_PORT = 8765
QA_HOST = "127.0.0.1"
QA_BASE = f"http://{QA_HOST}:{QA_PORT}"

QA_FRONTEND_PORT = 5174


@contextmanager
def uvicorn_running(db_url: str, log_path: Path):
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env["APP_PORT"] = str(QA_PORT)
    env["AGENTICHR_TEST_BYPASS_AUTH"] = "0"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "app.main:app",
         "--host", QA_HOST, "--port", str(QA_PORT)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        with httpx.Client(trust_env=False, timeout=2) as cli:
            for i in range(60):
                try:
                    r = cli.get(f"{QA_BASE}/api/health")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError(f"uvicorn 60s 内未起来; 日志: {log_path}")
        yield QA_BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()


@contextmanager
def vite_running(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    cmd = ["pnpm", "--filter", "frontend", "dev", "--port", str(QA_FRONTEND_PORT)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        shell=(os.name == "nt"),
    )
    try:
        with httpx.Client(trust_env=False, timeout=2) as cli:
            for i in range(120):
                try:
                    r = cli.get(f"http://127.0.0.1:{QA_FRONTEND_PORT}")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError(f"vite 120s 内未起来; 日志: {log_path}")
        yield f"http://127.0.0.1:{QA_FRONTEND_PORT}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
