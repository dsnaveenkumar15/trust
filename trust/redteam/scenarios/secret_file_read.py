from trust.redteam import reach as reach_mod
from trust.redteam.models import Candidate
from trust.redteam.scenarios import Scenario, sentence_with
from trust.rules_desc import HIDDEN_PATTERNS

SENSITIVE = HIDDEN_PATTERNS[4][0]


class SecretFileRead(Scenario):
    id, title = "secret-file-read", "Reading secret files"
    needs = "A tool that asks for, or whose code reads, credential files such as SSH keys or .env files."
    weak = ("secret_read",)

    def applies(self, target, reach):
        out = []
        for t in target.tools:
            m = SENSITIVE.search(t.description)
            if m:
                out.append(Candidate(self.id, t.name, sentence_with(t.description, m.start(), m.end()), 0.9))
        seen = {c.tool for c in out}
        for f in target.findings:
            if f.get("rule") == "sensitive-path":
                name = reach_mod.tool_of(target, f.get("file", ""), int(f.get("line") or 0))
                if name and name not in seen and f.get("quote"):
                    out.append(Candidate(self.id, name, f["quote"], 0.7))
                    seen.add(name)
        return out


SCENARIO = SecretFileRead()
