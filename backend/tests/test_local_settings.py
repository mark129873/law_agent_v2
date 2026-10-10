"""权限配置存储回归：隔离空库，禁止连接真实模型。"""
import json

import pytest
from sqlalchemy import inspect, select

from app import db
from app.agent.permission_service import evaluate
from app.models import LocalSetting
from app.sessions import execution_state as state


def test_zcode_local_setting_schema(store_db):
    schema = inspect(store_db.bind)
    assert set(schema.get_table_names()) == {"session", "message", "part", "session_entry", "local_setting"}
    assert {c["name"] for c in schema.get_columns("local_setting")} == {
        "scope", "scope_id", "namespace", "key", "value", "schema_version", "time_created", "time_updated"}
    assert schema.get_pk_constraint("local_setting")["constrained_columns"] == ["scope", "scope_id", "namespace", "key"]
    assert {i["name"] for i in schema.get_indexes("local_setting")} == {"local_setting_scope_idx", "local_setting_namespace_key_idx"}


def test_two_settings_and_upsert_keep_creation_time(tmp_data_dir):
    state.save_execution_state(tmp_data_dir, "edit")
    state.save_permission_rules(tmp_data_dir, {"version": 1, "allow": [{"tool": "write_file"}]})
    with db.new_session() as session:
        row = session.get(LocalSetting, ("project", state.project_id(tmp_data_dir), "permission", "mode"))
        row.time_created = 123
        session.commit()
    state.save_execution_state(tmp_data_dir, "yolo")
    with db.new_session() as session:
        rows = list(session.scalars(select(LocalSetting)))
        assert len(rows) == 2
        mode = next(r for r in rows if r.key == "mode")
        assert mode.time_created == 123 and mode.time_updated > 123
        assert mode.schema_version == 1 and json.loads(mode.value) == {"mode": "yolo"}
        assert {r.key for r in rows} == {"mode", "ruleset"}


def test_project_scope_isolation(tmp_data_dir):
    state.save_execution_state(tmp_data_dir, "yolo", project_id="project-a")
    state.save_permission_rules(tmp_data_dir, {"allow": [{"tool": "bash"}]}, project_id="project-a")
    assert state.load_execution_state(tmp_data_dir, project_id="project-a") == {"mode": "yolo"}
    assert state.load_execution_state(tmp_data_dir, project_id="project-b") == {"mode": "build"}
    assert state.load_permission_rules(tmp_data_dir, project_id="project-b")["allow"] == []
    assert state.project_id(tmp_data_dir) == state.project_id(tmp_data_dir / ".")
    assert state.project_id(tmp_data_dir) != state.project_id(tmp_data_dir / "other")


def test_modes_and_rules_survive_engine_restart(tmp_data_dir):
    state.save_execution_state(tmp_data_dir, "edit")
    state.add_permission_rule(tmp_data_dir, "allow", "bash", "echo:*")
    db.dispose_engine()
    assert state.load_execution_state(tmp_data_dir) == {"mode": "edit"}
    assert state.load_permission_rules(tmp_data_dir)["allow"] == [{"tool": "bash", "content": "echo:*"}]


def test_legacy_json_ignored_and_untouched(tmp_data_dir):
    files = {"execution_state.json": '{"mode":"yolo"}', "permission_rules.json": '{"allow":[{"tool":"bash"}]}'}
    for name, content in files.items():
        (tmp_data_dir / name).write_text(content)
    assert state.load_execution_state(tmp_data_dir) == {"mode": "build"}
    assert state.load_permission_rules(tmp_data_dir)["allow"] == []
    state.save_execution_state(tmp_data_dir, "edit")
    state.add_permission_rule(tmp_data_dir, "deny", "bash", "nope")
    for name, content in files.items():
        assert (tmp_data_dir / name).read_text() == content


def test_rules_roundtrip_and_dedup(tmp_data_dir):
    state.save_permission_rules(tmp_data_dir, {"allow": [], "deny": [{"tool": "bash", "content": "blocked"}], "ask": [{"tool": "read_file"}]})
    state.add_permission_rule(tmp_data_dir, "allow", "bash", "echo:*")
    state.add_permission_rule(tmp_data_dir, "allow", "bash", "echo:*")
    rules = state.load_permission_rules(tmp_data_dir)
    assert len(rules["allow"]) == 1
    assert rules["ask"] == [{"tool": "read_file"}]
    assert rules["deny"] == [{"tool": "bash", "content": "blocked"}]


def test_evaluate_reads_sqlite_rules_and_hard_deny(tmp_data_dir):
    state.save_permission_rules(tmp_data_dir, {"allow": [{"tool": "write_file"}], "deny": [{"tool": "read_file"}]})
    assert evaluate(tmp_data_dir, "write_file", {"path": "a.txt"})["decision"] == "allow"
    assert evaluate(tmp_data_dir, "read_file", {"path": "a.txt"})["decision"] == "deny"
    state.save_execution_state(tmp_data_dir, "yolo")
    assert evaluate(tmp_data_dir, "bash", {"command": "rm -rf /"})["decision"] == "deny"
    assert evaluate(tmp_data_dir, "write_file", {"path": "../outside"})["decision"] == "deny"


def test_bad_value_falls_back_safely(tmp_data_dir):
    state.save_execution_state(tmp_data_dir, "edit")
    state.save_permission_rules(tmp_data_dir, {"allow": "invalid", "deny": [None], "ask": None})
    with db.new_session() as session:
        row = session.get(LocalSetting, ("project", state.project_id(tmp_data_dir), "permission", "mode"))
        row.value = "{invalid"
        session.commit()
    assert state.load_execution_state(tmp_data_dir) == {"mode": "build"}
    assert state.load_permission_rules(tmp_data_dir) == {"version": 1, "allow": [], "deny": [], "ask": []}
    with pytest.raises(ValueError):
        state.save_execution_state(tmp_data_dir, "invalid")


def test_parallel_rule_additions_preserved(tmp_data_dir):
    from concurrent.futures import ThreadPoolExecutor
    state.save_permission_rules(tmp_data_dir, {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: state.add_permission_rule(tmp_data_dir, "allow", "bash", f"command-{i}"), range(8)))
    assert len(state.load_permission_rules(tmp_data_dir)["allow"]) == 8


def test_add_table_keeps_existing_session_data(tmp_data_dir):
    """新增表无需改四表；已有当前结构的会话不应丢失。"""
    from app.models import Session
    from app.sessions import store
    db.init_db(tmp_data_dir)
    with db.new_session() as session:
        store.ensure_session(session, "keep", "fake", "保留会话")
        session.commit()
    LocalSetting.__table__.drop(db.get_engine())
    db.dispose_engine()
    assert state.load_execution_state(tmp_data_dir) == {"mode": "build"}
    with db.new_session() as session:
        assert session.get(Session, "keep").title == "保留会话"
