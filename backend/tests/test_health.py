"""BE-1 验证：骨架启动、健康检查、建库建表、日志落盘。"""

from pathlib import Path

from sqlalchemy import inspect

from app import db


def test_health_ok(client) -> None:
    """健康检查返回 200 与 ok（不依赖 LLM 配置）。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_db_tables_created(client, tmp_data_dir: Path) -> None:
    """启动后四张实体表都已建立，且库文件在测试沙箱内。"""
    engine = db.get_engine()
    tables = set(inspect(engine).get_table_names())
    assert {"session", "message", "part", "todo", "turn_usage"} <= tables
    # 绝不写真实数据目录：库文件必须位于 tests/.tmp-data/
    # engine.url 是 URL 编码形式（C%3A%5C...），用 database 属性取解码后的路径
    assert str(tmp_data_dir) in (engine.url.database or "")


def test_workspace_and_logs_created(client, tmp_data_dir: Path) -> None:
    """全自动初始化：workspace/ 与 logs/ 目录、日志文件已生成。"""
    assert (tmp_data_dir / "workspace").is_dir()
    assert (tmp_data_dir / "logs" / "app.log").exists()
