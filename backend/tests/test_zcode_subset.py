"""上游 SQL 对照与六表语义回归；不接外部模型。"""
import asyncio
import json
import sqlite3
from pathlib import Path

from sqlalchemy import select, text
from app import db
from app.models import Message, Part, Session, Todo, TurnUsage
from app.sessions import store, replay, approvals, execution_state
from app.sessions.recorder import TurnRecorder


def test_schema_is_upstream_subset(store_db):
    """每个保留列的 SQLite 类型/null/default/主键位置与上游相同。"""
    reference = sqlite3.connect(':memory:')
    reference.executescript(Path(__file__).with_name('zcode_schema_reference.sql').read_text())
    ours = store_db.connection()
    tables = {'session', 'message', 'part', 'local_setting', 'todo', 'turn_usage'}
    assert set(db.get_engine().dialect.get_table_names(ours)) == tables
    for table in tables:
        expected = {r[1]: r[2:] for r in reference.execute(f'pragma table_info({table})')}
        columns = ours.exec_driver_sql(f'pragma table_info({table})').all()
        assert columns
        for row in columns:
            assert row[1] in expected
            assert tuple(row[2:]) == expected[row[1]], (table, row, expected[row[1]])
        expected_fk = {tuple(r[2:]) for r in reference.execute(f'pragma foreign_key_list({table})')}
        actual_fk = {tuple(r[2:]) for r in ours.exec_driver_sql(f'pragma foreign_key_list({table})')}
        assert actual_fk == expected_fk
    reference.close()


def test_sequence_autofill_and_stable_update(store_db):
    store.ensure_session(store_db, 's', 'm', '问题')
    for mid in ('u', 'a'):
        store_db.execute(text('insert into message(id,session_id,time_created,time_updated,data) values(:id,\'s\',1,1,\'{}\')'), {'id': mid})
    store_db.commit()
    assert [m.sequence for m in store_db.scalars(select(Message).order_by(Message.sequence))] == [0, 1]
    store.upsert_message(store_db, 's', 'u', 'user', {}, 't')
    assert store_db.get(Message, 'u').sequence == 0


def test_full_turn_json_and_usage(store_db):
    r = TurnRecorder(store_db, 's', 'model-a', 100)
    r.begin_turn('问题')
    mid = r.step_message()
    r.add_usage(10, 4, 2, 1)
    r.add_usage(500, 30, subtask=True)
    r.upsert_tool_part(mid, 'p', {'tool_call_id': 'c', 'name': 'read_file', 'input': {'path':'x'}, 'status':'denied', 'output':'Error: 拒绝'})
    r.write_error_part(mid, '模型失败')
    r.end_turn('failed')
    message = json.loads(store_db.get(Message, mid).data)
    assert message['anchor']['turnId'] == r.turn_id
    assert message['modelId'] == 'model-a'
    assert message['tokens']['input'] == 10
    assert message['error'] == {'name':'APIError', 'data':{'message':'模型失败'}}
    raw = json.loads(store_db.get(Part, 'p').data)
    assert raw['state']['status'] == 'error' and raw['state']['error'] == 'Error: 拒绝'
    assert 'output' not in raw['state'] and 'duration_ms' not in raw['state']
    assert raw['state']['time']['end'] >= raw['state']['time']['start']
    usage = store_db.get(TurnUsage, ('s', r.turn_id))
    assert usage.status == 'error' and usage.input_tokens == 510 and usage.output_tokens == 34
    assert replay.load_replay(store_db, 's')['session']['context_used'] == 10
    assert not any(store.part_kind(p) in ('error','todo','subtask') for p in store_db.scalars(select(Part)))


def test_todo_replacement_rollback_and_cascade(store_db):
    r = TurnRecorder(store_db, 's', 'm', 100); r.begin_turn('第一轮')
    mid = r.step_message()
    items = [{'content':'A','status':'pending'}]
    r.write_todos(items)
    r.upsert_tool_part(mid,'p',{'name':'todo_write','input':{'items':items},'status':'completed'})
    r.end_turn('success')
    r2 = TurnRecorder(store_db, 's', 'm', 100); r2.begin_turn('第二轮')
    r2.write_todos([{'content':'B','status':'completed'}]); r2.end_turn('success')
    assert store.read_todos(store_db,'s')[0]['content'] == 'B'
    store.rollback_turn(store_db,'s',r2.turn_id,r2.first_user_sequence)
    assert store.read_todos(store_db,'s') == items
    store_db.delete(store_db.get(Session,'s')); store_db.commit()
    assert not store_db.scalars(select(Todo)).all()
    assert not store_db.scalars(select(Part)).all()
    assert not store_db.scalars(select(TurnUsage)).all()


def test_approval_refresh_but_no_restart_history(store_db):
    async def run():
        r=TurnRecorder(store_db,'s','m',100); r.begin_turn('等待')
        queue=asyncio.Queue()
        callback=approvals.InteractiveApprover(store_db,'s',r,queue)
        task=asyncio.create_task(callback('bash',{'command':'echo x > a'},'写文件',allow_full_access=False))
        event=await queue.get()
        snap=replay.load_replay(store_db,'s')
        assert snap['pending_approval']['full_access'] is False
        assert not any(o['option_id']=='fullAccess' for o in snap['pending_approval']['options'])
        approvals.resolve(event['request_id'],True)
        await task
        assert replay.load_replay(store_db,'s')['pending_approval'] is None
        approvals._events.clear(); approvals._pending.clear()
        assert not any(i['kind']=='approval' for t in replay.load_replay(store_db,'s')['turns'] for i in t['work_items'])
    asyncio.run(run())


def test_project_identity_matches_session(store_db, tmp_data_dir):
    row=store.ensure_session(store_db,'s','m','问题')
    assert row.project_id == execution_state.project_id(tmp_data_dir)
    assert row.directory == execution_state.project_directory(tmp_data_dir)
