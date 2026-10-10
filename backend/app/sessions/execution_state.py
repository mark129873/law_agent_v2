"""项目权限配置：ZCode local_setting 结构；不读取/迁移旧 JSON 文件。"""
import hashlib
import json
import os
import threading
from pathlib import Path

from sqlalchemy.dialects.sqlite import insert

from app import db
from app.models import LocalSetting, now_ms

_MODES = ("build", "edit", "yolo")
_RULE_LOCK = threading.Lock()


def project_directory(data_dir) -> str:
    """当前固定工作区的规范路径；未来多工作区可传入各自项目标识。"""
    return os.path.normcase(str((Path(data_dir) / "workspace").resolve()))


def project_id(data_dir) -> str:
    """稳定项目标识，不用易冲突的目录名，也不使用每次变化的随机值。"""
    return hashlib.sha256(project_directory(data_dir).encode()).hexdigest()


def _read(data_dir, key: str, scope_id: str | None = None) -> dict:
    db.init_db(Path(data_dir))
    with db.new_session() as session:
        row = session.get(LocalSetting, ("project", scope_id or project_id(data_dir), "permission", key))
        try:
            value = json.loads(row.value) if row else {}
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}  # 配置内容损坏时回到安全默认；数据库故障不静默吞掉。


def _write(data_dir, key: str, value: dict, scope_id: str | None = None) -> None:
    db.init_db(Path(data_dir))
    now = now_ms()
    values = dict(scope="project", scope_id=scope_id or project_id(data_dir), namespace="permission",
                  key=key, value=json.dumps(value, ensure_ascii=False), schema_version=1,
                  time_created=now, time_updated=now)
    statement = insert(LocalSetting).values(**values)
    # 原子 upsert 保留创建时间，避免并行首次保存造成联合主键冲突。
    statement = statement.on_conflict_do_update(
        index_elements=["scope", "scope_id", "namespace", "key"],
        set_={name: values[name] for name in ("value", "schema_version", "time_updated")})
    with db.new_session() as session:
        session.execute(statement)
        session.commit()


def load_execution_state(data_dir, *, project_id: str | None = None) -> dict:
    mode = _read(data_dir, "mode", project_id).get("mode")
    return {"mode": mode if mode in _MODES else "build"}


def save_execution_state(data_dir, mode: str, *, project_id: str | None = None) -> dict:
    if mode not in _MODES:
        raise ValueError(f"非法协作模式：{mode}")
    state = {"mode": mode}
    _write(data_dir, "mode", state, project_id)
    return state


def load_permission_rules(data_dir, *, project_id: str | None = None) -> dict:
    data = _read(data_dir, "ruleset", project_id)
    rules = {"version": 1}
    for behavior in ("allow", "deny", "ask"):
        bucket = data.get(behavior, [])
        rules[behavior] = [r for r in bucket if isinstance(r, dict) and isinstance(r.get("tool"), str)] if isinstance(bucket, list) else []
    return rules


def save_permission_rules(data_dir, ruleset: dict, *, project_id: str | None = None) -> None:
    """项目规则存 SQLite；会话授权从不调用此函数。"""
    _write(data_dir, "ruleset", ruleset, project_id)


def add_permission_rule(data_dir, behavior: str, tool: str, content: str | None, *, project_id: str | None = None) -> dict:
    """保留现有审批调用契约，追加项目规则；同工具/内容去重。"""
    if behavior not in ("allow", "deny"):
        raise ValueError(f"非法规则行为：{behavior}")
    # 同进程审批端点可能并行执行，串行追加避免读改写丢失另一条规则。
    with _RULE_LOCK:
        ruleset = load_permission_rules(data_dir, project_id=project_id)
        rule = {"tool": tool}
        if content:
            rule["content"] = content
        if not any(r.get("tool") == tool and (r.get("content") or "") == (content or "") for r in ruleset[behavior]):
            ruleset[behavior].append(rule)
            save_permission_rules(data_dir, ruleset, project_id=project_id)
        return ruleset
