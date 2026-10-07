# tests/test_core_agent_gateway.py
import time

import pytest

pytest.importorskip("langchain_core")

from core.agent.gateway import Budget, BudgetExceeded, Gateway  # noqa: E402
from tests.agent_fakes import ai, scripted_factory  # noqa: E402


def budget(**kw):
    return Budget(**{"max_usd": 1.0, "max_tokens": 10_000, "max_tool_calls": 5, "deadline": time.monotonic() + 60, **kw})


def test_falls_back_to_the_next_model_and_counts_the_cost():
    seen = []
    gw = Gateway({"bulk": ["free", "paid"]}, scripted_factory({"free": [RuntimeError("rate limited")], "paid": [ai("ok", cost=0.01)]}),
                 {}, budget(), lambda *a: seen.append(a), lambda: None)
    assert gw.invoke([], role="bulk").content == "ok"
    assert gw.budget.usd == pytest.approx(0.01) and seen[0][0] == "paid"


def test_stops_before_a_call_once_the_run_budget_is_spent():
    gw = Gateway({"bulk": ["m"]}, scripted_factory({"m": [ai("x")]}), {}, budget(max_usd=0.0), lambda *a: None, lambda: None)
    with pytest.raises(BudgetExceeded):
        gw.invoke([], role="bulk")


def test_a_model_priced_at_zero_costs_nothing_when_the_provider_reports_no_cost():
    gw = Gateway({"bulk": ["free"]}, scripted_factory({"free": [ai("x", tokens=(5000, 5000))]}), {"free": (0.0, 0.0)},
                 budget(), lambda *a: None, lambda: None)
    gw.invoke([], role="bulk")
    assert gw.budget.usd == 0.0 and gw.budget.tokens == 10_000
