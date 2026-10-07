"""Red team data: the target under test, scenario candidates and picks, and the victim's recorded trace."""
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    file: str = ""
    line: int = 0


@dataclass
class Target:
    kind: str                                   # manifest | repo
    tools: list[Tool]
    files: dict[str, str]                       # repo source, or the pretty-printed manifest under MANIFEST_FILE
    repo: str | None = None
    sha: str | None = None
    findings: list[dict] = field(default_factory=list)   # static rule findings (Finding.to_dict())
    notes: list[str] = field(default_factory=list)

    def tool(self, name: str) -> Tool | None:
        return next((t for t in self.tools if t.name == name), None)


@dataclass(frozen=True)
class Candidate:
    scenario: str
    tool: str
    quote: str                                  # verbatim text in the tool's name, description or source
    score: float


@dataclass
class Pick:
    scenario: str
    tool: str
    quote: str
    why: str
    risk: str                                   # high | medium | low
    source: str                                 # analyst | rules
    candidate: Candidate


@dataclass
class Call:
    turn: int
    tool: str                                   # the original tool name
    args: dict
    output: str
    host: bool                                  # a tool built into the simulated app, not the server under test


@dataclass
class Trace:
    calls: list[Call]
    final: str
    turns: int

    def to_dict(self) -> dict:
        return {"calls": [asdict(c) for c in self.calls], "final": self.final, "turns": self.turns}
