"""进行中 turn 的注册表（进程内）。

为什么放进程内存就够：
1. 产品规则"同会话单 turn"只需查"这个会话是否在跑"，进程崩溃时 turn 本来
   就随进程消失（回放层会把无收口事实的轮次标为 stopped）；
2. 跨会话并行天然支持：dict 按 session_id 分键。
"""

import asyncio

# session_id -> turn_id
_active: dict[str, str] = {}
# session_id -> 停止信号（POST /stop 时置位，循环在每个检查点轮询）
_stop_events: dict[str, asyncio.Event] = {}


def register(session_id: str, turn_id: str) -> asyncio.Event:
    """登记一个开始进行的 turn，返回它的停止信号（循环的 stop_flag 数据源）。"""
    _active[session_id] = turn_id
    event = asyncio.Event()
    _stop_events[session_id] = event
    return event


def unregister(session_id: str, turn_id: str) -> None:
    """turn 结束时注销；防止旧 turn 误删新 turn 的登记，校验 turn_id 一致才删。"""
    if _active.get(session_id) == turn_id:
        _active.pop(session_id, None)
        _stop_events.pop(session_id, None)


def request_stop(session_id: str) -> bool:
    """请求停止该会话的当前 turn。返回是否真的有 turn 在跑。"""
    event = _stop_events.get(session_id)
    if event is None:
        return False
    event.set()
    return True


def is_running(session_id: str) -> bool:
    """该会话是否有进行中的 turn（侧栏呼吸点、删除拦截、409 判断共用）。"""
    return session_id in _active


def running_session_ids() -> set[str]:
    """全部进行中的会话集合（回放层判断孤儿轮用）。"""
    return set(_active.keys())


def running_turn_ids() -> set[str]:
    """回放按轮次判断运行态，不能传会话键集合。"""
    return set(_active.values())
