import pytest

from takopi import jarvis
from takopi.model import CompletedEvent, ResumeToken
from takopi.runners import codex


def notification(duration, method="item/started", kind="sleep"):
    return {"method": method, "params": {"item": {"type": kind, "durationMs": duration}}}


def test_guard_only_blocks_long_sleep_start():
    assert codex._blocking_chat_sleep(notification(300_000))
    assert not codex._blocking_chat_sleep(notification(10_000))
    assert not codex._blocking_chat_sleep(notification(300_000, "item/completed"))
    assert not codex._blocking_chat_sleep(notification(300_000, kind="mcpToolCall"))
    assert not codex._blocking_chat_sleep({"method": "item/started", "params": None})


@pytest.mark.anyio
async def test_long_sleep_interrupts_turn_and_releases_subscription(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path))
    monkeypatch.setattr(codex, "get_run_base_dir", lambda: tmp_path)
    token = ResumeToken(engine="codex", value="thread-test")
    monkeypatch.setattr(jarvis, "active_session", lambda cwd: token)
    monkeypatch.setattr(jarvis, "remember_session", lambda cwd, thread: None)
    monkeypatch.setattr(jarvis, "model_override", lambda cwd: None)

    class Client:
        interrupted = False
        unsubscribed = False

        async def start(self): pass
        async def ensure_thread_loaded(self, thread): pass

        async def turn_start(self, thread, params):
            assert "не подключены" in params["input"][0]["text"]
            return {"turn": {"id": "turn-test"}}

        async def subscribe_turn(self, turn):
            async def events():
                yield notification(300_000)
                raise AssertionError("Must not wait for the sleeping turn")

            class Stream:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def __aiter__(self): return events()
            return Stream()

        async def turn_interrupt(self, thread, turn):
            self.interrupted = True

        async def unsubscribe_turn(self, turn):
            self.unsubscribed = True

    runner = codex.AppServerCodexRunner(codex_cmd="unused", extra_args=[])
    client = Client()
    runner._client = client
    events = [event async for event in runner.run_impl("Напомни через 5 минут", None)]
    assert client.interrupted and client.unsubscribed
    assert isinstance(events[-1], CompletedEvent)
    assert events[-1].ok is False
    assert "не поставил напоминание" in events[-1].answer
