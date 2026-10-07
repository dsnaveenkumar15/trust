"""A suggested fix must itself pass Trust's description rules."""
from trust.models import ToolDef
from trust.rules_desc import description_findings

BLOCKING = {"hidden-instructions", "invisible-unicode", "tool-shadowing", "encoded-blob"}


def fix_is_clean(name: str, description: str) -> bool:
    found = description_findings([ToolDef(name, description, "fix", 1, "manifest")], {"fix": description})
    return not any(f.rule in BLOCKING for f in found)
