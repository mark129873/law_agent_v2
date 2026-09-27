"""进行中 turn 的注册表（进程内）。

为什么放进程内存就够：
1. 产品规则"同会话单 turn"只需查"这个会话是否在跑"，进程崩溃时 turn 本来
   就随进程消失（回放层会把无收口事实的轮次标为 stopped）；
2. 跨会话并行天然支持：dict 按 session_id 分键。
"""

# session_id -> turn_id
_active: dict[str, str] = {}


def register(session_id: str, turn_id: str) -> None:
    """登记一个开始进行的 turn（同会话重复登记说明并发控制漏了，覆盖并告警由调用方负责）。"""
    _active[session_id] = turn_id


def unregister(session_id: str, turn_id: str) -> None:
    """turn 结束时注销；防止旧 turn 误删新 turn 的登记，校验 turn_id 一致才删。"""
    if _active.get(session_id) == turn_id:
        _active.pop(session_id, None)


def is_running(session_id: str) -> bool:
    """该会话是否有进行中的 turn（侧栏呼吸点、删除拦截、409 判断共用）。"""
    return session_id in _active


def running_session_ids() -> set[str]:
    """全部进行中的会话集合（回放层判断孤儿轮用）。"""
    return set(_active.keys())
