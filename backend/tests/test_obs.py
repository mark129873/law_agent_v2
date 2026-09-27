"""BE-10 验证：观测补全（Langfuse 默认关、启用路径、上报内容、失败不影响主流程）。"""

import asyncio

import pytest

from app.obs import (
    init_langfuse,
    record_llm_call,
    _langfuse_client,
)


@pytest.fixture()
def recorder(tmp_data_dir):
    """连到测试沙箱库的 recorder（与 test_agent_loop 同款）。"""
    from app.agent.tools import workspace_root
    from app import db
    from app.sessions.recorder import TurnRecorder

    workspace_root().mkdir(parents=True, exist_ok=True)
    db.init_db(tmp_data_dir)
    session = db.new_session()
    rec = TurnRecorder(session, "s1", model="test-model", max_tokens=1000)
    yield rec
    session.close()


class SettingsStub:
    """Langfuse 配置桩。"""

    def __init__(self, enabled: bool, base_url: str = "http://lf.local") -> None:
        self.langfuse_enabled = enabled
        self.langfuse_base_url = base_url
        self.langfuse_public_key = "pk"
        self.langfuse_secret_key = "sk"


class StubLangfuse:
    """Langfuse SDK 桩：记录调用链。"""

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.traces: list[dict] = []
        self.generations: list[dict] = []
        self.flushed = 0

    def trace(self, **kwargs):
        self.traces.append(kwargs)

        class Trace:
            def __init__(self, outer, trace_kwargs):
                self.outer = outer
                self.trace_kwargs = trace_kwargs

            def generation(self, **gen):
                self.outer.generations.append({"trace": self.trace_kwargs, "gen": gen})

        return Trace(self, kwargs)

    def flush(self) -> None:
        self.flushed += 1


def test_init_disabled_by_default(monkeypatch) -> None:
    """默认关闭：不创建客户端。"""
    created = {}

    def fake_langfuse(**kwargs):
        created["called"] = True
        return StubLangfuse(**kwargs)

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", fake_langfuse)
    init_langfuse(SettingsStub(enabled=False))
    assert _langfuse_client is None
    assert "called" not in created  # 根本没构造


def test_init_enabled_creates_client(monkeypatch) -> None:
    """开启时：用配置的 host/keys 构造客户端。"""
    holder = {}

    def fake_langfuse(**kwargs):
        holder.update(kwargs)
        return StubLangfuse(**kwargs)

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", fake_langfuse)
    init_langfuse(SettingsStub(enabled=True))
    assert holder.get("public_key") == "pk" and holder.get("host") == "http://lf.local"


def test_init_failure_degrades(monkeypatch) -> None:
    """初始化失败：吞异常、客户端为 None（不影响主流程）。"""

    def broken_langfuse(**kwargs):
        raise RuntimeError("配置错误")

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", broken_langfuse)
    init_langfuse(SettingsStub(enabled=True))
    assert _langfuse_client is None


def test_record_llm_call_payload(monkeypatch) -> None:
    """上报内容：trace=session、generation 带 turn/输入/输出/token 用量。"""
    stub = StubLangfuse()

    async def scenario():
        record_llm_call(
            session_id="sess1", turn_id="turn1", model="test-model",
            system="系统提示", messages=[{"role": "user", "content": "hi"}],
            output_text="回复", usage={"input_tokens": 11, "output_tokens": 7},
        )

    monkeypatch.setattr("app.obs._langfuse_client", stub)
    asyncio.run(scenario())
    assert stub.flushed == 1
    assert stub.generations[0]["trace"] == {"id": "sess1", "name": "session", "session_id": "sess1"}
    gen = stub.generations[0]["gen"]
    assert gen["metadata"] == {"turn_id": "turn1"}
    assert gen["model"] == "test-model"
    assert gen["output"] == "回复"
    assert gen["usage"] == {"input": 11, "output": 7, "unit": "TOKENS"}


def test_record_llm_call_failure_ignored(monkeypatch) -> None:
    """上报抛异常：吞掉（观测永远不挡主流程）。"""

    class Broken:
        def trace(self, **kwargs):
            raise RuntimeError("网络断了")

    monkeypatch.setattr("app.obs._langfuse_client", Broken())
    # 不应抛出
    record_llm_call("s", "t", "m", "sys", [], "out", {})


def test_turn_records_llm_call(recorder, monkeypatch) -> None:
    """端到端：跑一轮 turn，Langfuse 收到该步调用。"""
    from app.agent.loop import TurnDeps, run_turn
    from app.agent.tools import workspace_root

    workspace_root().mkdir(parents=True, exist_ok=True)

    stub = StubLangfuse()
    monkeypatch.setattr("app.obs._langfuse_client", stub)

    class FakeClient:
        model = "test-model"

        async def stream(self, *, system, messages, tools):
            yield {"type": "text_delta", "text": "回答"}
            yield {"type": "usage", "input_tokens": 9, "output_tokens": 3}

    deps = TurnDeps(
        client=FakeClient(), recorder=recorder, system_prompt="测试",
        history=[{"role": "user", "content": "你好"}],
    )

    async def run():
        return [e async for e in run_turn(deps)]

    asyncio.run(run())
    assert len(stub.generations) == 1
    gen = stub.generations[0]["gen"]
    assert gen["output"] == "回答"
    assert gen["usage"]["output"] == 3
    assert gen["input"]["messages"][0]["content"] == "你好"
