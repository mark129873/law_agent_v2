"""测试构造器：直接生成新结构，旧 fixture 参数仅用于描述测试场景。"""
from sqlalchemy import select
from app.models import Part, TurnUsage
from app.sessions import store, approvals


def seed_record(db, sid, kind, data, turn_id='', **kwargs):
    label = turn_id or data.get('turn_id', '')
    if kind == 'turn':
        row = TurnUsage(session_id=sid, turn_id=label, started_at=int(data.get('started_at', 0)),
            completed_at=data.get('ended_at'), duration_ms=data.get('active_ms'),
            status={'success': 'completed', 'failed': 'error', 'stopped': 'cancelled'}.get(data.get('state'), 'completed'),
            output_tokens=data.get('tokens_used', 0), input_tokens=data.get('input_tokens', 0))
        db.add(row); db.commit()
        return row
    if kind == 'approval':
        rid = data['request_id']
        approvals._events.setdefault(sid, {})[rid] = data
        if data['status'] == 'requested':
            approvals.register(rid)
        else:
            approvals.resolve(rid, data['status'] == 'approved')
        return data
    if kind == 'compaction':
        return store.save_compaction(db, sid, label, data['before_sequence'], data['summary_text'],
                                     data.get('tokens_before'), data.get('tokens_after'))
    raise AssertionError(f'不支持的旧 fixture 类型 {kind}')


def compactions(db, sid):
    return [p for p in db.scalars(select(Part).where(Part.session_id == sid)) if store.part_kind(p) == 'compaction']
