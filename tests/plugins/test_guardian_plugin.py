"""Tests for the bundled Guardian audit plugin."""

import importlib.util
import queue
import sys
import types
from pathlib import Path


def _load_guardian_plugin():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugins" / "guardian"
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.guardian",
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.guardian"
    mod.__path__ = [str(plugin_dir)]
    sys.modules["hermes_plugins.guardian"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_guardian_plugin_registers_expected_hooks():
    mod = _load_guardian_plugin()
    calls = []

    class Ctx:
        def register_hook(self, name, fn):
            calls.append((name, fn))

    mod.register(Ctx())
    assert [name for name, _ in calls] == [
        "pre_tool_call",
        "post_tool_call",
        "pre_api_request",
        "post_api_request",
        "on_session_start",
        "on_session_end",
    ]


def test_guardian_tool_hook_enqueues_redacted_event(monkeypatch):
    mod = _load_guardian_plugin()
    events = []

    class FakeQueue:
        def put_nowait(self, event):
            events.append(event)

    monkeypatch.setattr(mod, "_start_worker", lambda: None)
    monkeypatch.setattr(mod, "_queue", FakeQueue())

    mod._on_pre_tool_call(
        tool_name="terminal",
        args={
            "command": "echo ok",
            "api_key": "sk-secret-value-that-should-not-survive",
            "Authorization": "Bearer secret-token",
        },
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
    )

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "tool_call"
    assert event["sourceAgent"]["framework"] == "hermes"
    assert event["sessionId"] == "session-1"
    assert event["data"]["args"]["api_key"] == "[REDACTED]"
    assert event["data"]["args"]["Authorization"] == "[REDACTED]"


def test_guardian_enqueue_fails_open_when_queue_is_full(monkeypatch):
    mod = _load_guardian_plugin()

    class FullQueue:
        def put_nowait(self, event):
            raise queue.Full()

    monkeypatch.setattr(mod, "_start_worker", lambda: None)
    monkeypatch.setattr(mod, "_queue", FullQueue())

    mod._on_post_tool_call(
        tool_name="terminal",
        args={"command": "echo ok"},
        result="ok",
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
    )
