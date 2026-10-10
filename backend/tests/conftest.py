"""pytest 全局夹具。

按 docs/RELIABILITY.md 的测试干净环境约束：
1. 在任何 app 模块导入之前，把 DATA_DIR 指到 tests/.tmp-data/（项目内目录）；
2. 每个测试前清空重建该目录，测试后删除，保证仓库干净；
3. 一律不真实调用 LLM（agent 相关测试用假客户端，后续功能提供）。
"""

import os
import shutil
from pathlib import Path

# 必须在导入 app.* 之前设置环境变量（config.py 在 import 时读取）
_TEST_DATA_DIR = Path(__file__).resolve().parent / ".tmp-data"
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
# model-io JSONL 同样指进测试沙箱（真实 log/ 绝不被测试触碰），随 fixture 一并清理
os.environ["MODELIO_DIR"] = str(_TEST_DATA_DIR / "modelio")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def tmp_data_dir() -> Path:
    """每个测试独享一个干净的 .tmp-data/，测试后删除。"""
    from app import db
    from app.obs import shutdown_logging

    def _cleanup() -> None:
        # Windows 上 SQLite 连接/日志句柄未释放时目录删不掉，先全部释放再删
        shutdown_logging()
        db.dispose_engine()
        from app.sessions import approvals
        approvals._events.clear()
        approvals._pending.clear()
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)

    if _TEST_DATA_DIR.exists():
        _cleanup()
    _TEST_DATA_DIR.mkdir(parents=True)
    yield _TEST_DATA_DIR
    _cleanup()


@pytest.fixture()
def client(tmp_data_dir: Path, monkeypatch) -> TestClient:
    """带初始化完成的 TestClient：lifespan 会用 DATA_DIR 建库。"""
    from app.main import create_app

    from app.api.sessions import get_llm_client
    from app.config import settings

    # 测试不依赖开发者的 .env，也绝不构造可联网的真实模型客户端。
    monkeypatch.setattr(settings, "anthropic_api_key", "test-only-not-a-secret")
    monkeypatch.setattr(settings, "model_id", "test-model")
    class UnusedClient:
        async def stream(self, **kwargs):
            raise AssertionError("此测试必须显式提供模型脚本")
            yield  # 保持异步生成器接口

    app = create_app()
    app.dependency_overrides[get_llm_client] = lambda: UnusedClient()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def store_db(tmp_data_dir: Path):
    """连到测试沙箱库的 ORM 会话；测试结束自动关闭（防 Windows 文件锁）。"""
    from app import db

    db.init_db(tmp_data_dir)
    session = db.new_session()
    yield session
    session.close()
