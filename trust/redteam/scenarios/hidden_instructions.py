import re

from trust.redteam.models import Candidate
from trust.redteam.scenarios import TAG_BLOCK, Scenario
from trust.rules_desc import HIDDEN_PATTERNS

PATTERNS = [p for i, (p, _) in enumerate(HIDDEN_PATTERNS) if i != 2]      # 'hide from the user' has its own scenario
IMPERATIVE = re.compile(r"\b(?:you must|always|before (?:using|calling)|after (?:using|calling)|make sure to|be sure to)\b[^.\n]{0,160}", re.I)


class HiddenInstructions(Scenario):
    id, title = "hidden-instructions", "Hidden instructions"
    needs = "A tool description that contains instructions addressed to the agent."

    def applies(self, target, reach):
        out = []
        for t in target.tools:
            m = next((m for p in PATTERNS if (m := p.search(t.description))), None)
            if m:                                  # a tag-style block is quoted whole, so the reader sees what it says
                block = next((b.group(0) for b in TAG_BLOCK.finditer(t.description) if b.start() <= m.start() < b.end()), m.group(0))
                out.append(Candidate(self.id, t.name, block[:300], 0.9))
            elif (m := IMPERATIVE.search(t.description)):
                out.append(Candidate(self.id, t.name, m.group(0), 0.5))
        return sorted(out, key=lambda c: -c.score)


SCENARIO = HiddenInstructions()
