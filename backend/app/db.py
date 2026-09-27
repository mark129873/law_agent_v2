"""数据库引擎与会话工厂模块。

为什么把引擎初始化做成显式的 init_db() 而不是 import 时执行：
1. 测试需要先把 DATA_DIR 指到 tests/.tmp-data/ 再建引擎（docs/RELIABILITY.md）；
2. 同一套代码在真实启动（main.py lifespan）与测试（conftest）里复用。
"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# 进程内共享的引擎与会话工厂（由 init_db 初始化）
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _enable_sqlite_pragmas(dbapi_connection, _record) -> None:
    """每个新连接上开启必需的 PRAGMA（ZCode session-store 同款基线）。

    - journal_mode=WAL: 读写不互斥，写入原子提交，进程崩溃不损坏已提交数据；
    - busy_timeout: 遇到锁等待 5 秒而不是立刻报 database is locked；
    - foreign_keys: SQLite 默认不启用外键约束，必须显式打开。
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(data_dir: Path) -> Engine:
    """创建引擎、建表并初始化全局工厂。可重复调用（幂等）。"""
    global _engine, _session_factory

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "app.db"

    # check_same_thread=False：FastAPI 的同步依赖会在不同线程拿连接，
    # 线程安全由"每请求独立连接 + WAL"保证。
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _enable_sqlite_pragmas)

    # 建表（幂等：已存在的表跳过）。v1 表结构由 models.py 声明，
    # 后续结构演进再引入版本化迁移（ZCode migrations 的思路）。
    Base.metadata.create_all(engine)

    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    """取全局引擎；未初始化说明调用顺序有误，直接抛错暴露问题。"""
    if _engine is None:
        raise RuntimeError("数据库未初始化：请先调用 init_db()")
    return _engine


def get_db() -> Session:
    """FastAPI 依赖：每个请求一个独立 Session，用完即关。"""
    if _session_factory is None:
        raise RuntimeError("数据库未初始化：请先调用 init_db()")
    db = _session_factory()
    try:
        yield db
    finally:
        db.close()


def dispose_engine() -> None:
    """关闭全局引擎并释放连接池里的全部连接，回到未初始化状态。

    为什么需要：Windows 上 SQLite 连接未关闭时 app.db（含 -wal/-shm）被占用，
    测试清理（删除 .tmp-data/）会失败；测试收尾时调用，让下一个测试从干净状态开始。
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
