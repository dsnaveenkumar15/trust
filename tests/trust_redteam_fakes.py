"""Fake models for the red team tests: the analyst answers from a script, the victim from a function of the
conversation, so scenarios may run side by side. No network, no money."""
import json
import threading
import time

from langchain_core.messages import ToolMessage

from core.agent.gateway import Budget, Gateway
from tests.agent_fakes import ai


class FakeModel:
    def __init__(self, analyst=None, victim=None):
        self.analyst, self.victim = list(analyst or []), victim or polite
        self.calls, self.lock = [], threading.Lock()

    def bind_tools(self, tools):
        return _Bound(self, tools)

    def invoke(self, messages):                                   # the analyst: no tools bound
        with self.lock:
            self.calls.append(("analyst", messages))
            if not self.analyst:
                raise RuntimeError("no analyst reply scripted")
            item = self.analyst.pop(0)
        if isinstance(item, Exception):
            raise item
        return ai(item if isinstance(item, str) else json.dumps(item))


class _Bound:
    def __init__(self, model, tools):
        self.model, self.tools = model, tools

    def invoke(self, messages):
        with self.model.lock:
            self.model.calls.append(("victim", messages))
        return self.model.victim(messages, self.tools)


def done(messages) -> list:
    return [m for m in messages if isinstance(m, ToolMessage)]


def polite(messages, tools):
    return ai("I looked at the request and answered it without needing anything unusual.")


def leaky(messages, tools):
    """Falls for anything: reads the fake SSH key, then sends it to the first server tool."""
    server = next(t["function"]["name"] for t in tools if not t["function"]["name"].startswith("fs_"))
    seen = done(messages)
    if not seen:
        return ai("", tool_calls=[{"name": "fs_read_file", "args": {"path": "~/.ssh/id_rsa"}, "id": "c1"}])
    if len(seen) == 1:
        return ai("", tool_calls=[{"name": server, "args": {"input": seen[0].content}, "id": "c2"}])
    return ai("All done.")


def obedient(name, args):
    """Calls one named tool once, then stops."""
    def victim(messages, tools):
        if not done(messages):
            return ai("", tool_calls=[{"name": name, "args": args, "id": "c1"}])
        return ai("Done.")
    return victim


def gateway(model, max_usd=1.0, seconds=60):
    used = []
    gw = Gateway({"victim": ["v"], "analyst": ["a"]}, lambda name: model, {"v": (0.0, 0.0), "a": (0.0, 0.0)},
                 Budget(max_usd, 10_000_000, 1, time.monotonic() + seconds), lambda *a: used.append(a), lambda: None)
    gw.used = used
    return gw
