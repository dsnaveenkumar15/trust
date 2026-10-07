# tests/agent_fakes.py
from langchain_core.messages import AIMessage


class ScriptedModel:
    """Stands in for a chat model: returns queued AIMessages (or raises queued exceptions) in order."""

    def __init__(self, responses):
        self.responses, self.calls, self.tools = list(responses), [], None

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def scripted_factory(script):
    models = {name: ScriptedModel(responses) for name, responses in script.items()}
    return lambda name: models[name]


def ai(content="", tool_calls=None, tokens=(100, 20), cost=None, provider="p"):
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    msg.usage_metadata = {"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)}
    msg.response_metadata = {"token_usage": ({"cost": cost} if cost is not None else {}), "model_provider": provider}
    return msg
