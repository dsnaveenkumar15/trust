from pydantic import BaseModel

from tests.agent_fakes import ai


class Out(BaseModel):
    n: int


def test_structured_repairs_once_then_succeeds():
    import time
    from core.agent.gateway import Budget, Gateway
    from core.agent.structured import structured
    from tests.agent_fakes import scripted_factory
    gw = Gateway({"r": ["m"]}, scripted_factory({"m": [ai("not json"), ai('{"n": 3}')]}), {"m": (0, 0)},
                 Budget(1, 10**6, 1, time.monotonic() + 60), lambda *a: None, lambda: None)
    assert structured(gw, "r", "sys", {"x": 1}, Out).n == 3
