import base64
import binascii
import re

from trust.redteam.models import Candidate
from trust.redteam.scenarios import Scenario
from trust.rules_desc import BASE64_RE

HEX_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}){24,}\b")


def _readable(blob: str) -> bool:
    for decode in (lambda s: base64.b64decode(s + "=" * (-len(s) % 4)), bytes.fromhex):
        try:
            text = decode(blob).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if text and sum(ch.isprintable() for ch in text) / len(text) > 0.9:
            return True
    return False


class EncodedPayload(Scenario):
    id, title = "encoded-payload", "Encoded instructions"
    needs = "A long base64 or hex blob in a description that decodes to readable text."

    def applies(self, target, reach):
        out = []
        for t in target.tools:
            m = next((m for rx in (BASE64_RE, HEX_RE) for m in rx.finditer(t.description) if _readable(m.group(0))), None)
            if m:
                out.append(Candidate(self.id, t.name, m.group(0), 0.8))
        return out


SCENARIO = EncodedPayload()
