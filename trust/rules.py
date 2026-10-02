from trust.models import Finding, ToolDef
from trust.rules_code import code_findings, dependency_findings, meta_findings
from trust.rules_desc import description_findings

RULESET_VERSION = "1.0.0"


def run_rules(files: dict[str, str], tools: list[ToolDef], meta: dict, now: float) -> list[Finding]:
    found = (description_findings(tools, files) + code_findings(files) + dependency_findings(files)
             + meta_findings(meta, files, now))
    unique: dict[tuple[str, str, int], Finding] = {}
    for finding in found:
        unique.setdefault((finding.rule, finding.file, finding.line), finding)
    return list(unique.values())


RULE_CATALOG = [
    {"rule": "hidden-instructions", "category": "injection", "severity": "critical",
     "checks": "Tool descriptions with hidden instruction blocks, instruction overrides, requests to hide behaviour "
               "from the user, or references to local credential files."},
    {"rule": "invisible-unicode", "category": "injection", "severity": "critical",
     "checks": "Zero-width and other invisible characters in tool descriptions."},
    {"rule": "tool-shadowing", "category": "injection", "severity": "critical",
     "checks": "Descriptions that instruct the agent about other tools."},
    {"rule": "auditor-addressed", "category": "injection", "severity": "high",
     "checks": "Text addressed to security reviewers or scanners."},
    {"rule": "encoded-blob", "category": "injection", "severity": "high",
     "checks": "Long encoded blobs inside tool descriptions."},
    {"rule": "name-collision", "category": "injection", "severity": "medium",
     "checks": "Tool names that collide with common tools, so agents may call the wrong one."},
    {"rule": "opaque-description", "category": "transparency", "severity": "medium",
     "checks": "Descriptions built at runtime that could only be partly checked."},
    {"rule": "shell-exec", "category": "capabilities", "severity": "high",
     "checks": "Runs shell commands or external programs."},
    {"rule": "dynamic-eval", "category": "capabilities", "severity": "high",
     "checks": "Evaluates dynamically built code."},
    {"rule": "sensitive-path", "category": "exfiltration", "severity": "high",
     "checks": "References local credential files (SSH, cloud, agent client configs)."},
    {"rule": "hardcoded-url-call", "category": "exfiltration", "severity": "medium",
     "checks": "Network calls to hard-coded external URLs."},
    {"rule": "hardcoded-secret", "category": "supply_chain", "severity": "high",
     "checks": "Credentials committed in source (the published quote stops before the secret)."},
    {"rule": "unpinned-deps", "category": "supply_chain", "severity": "low",
     "checks": "Dependencies without version pins."},
    {"rule": "archived", "category": "supply_chain", "severity": "medium", "checks": "Archived repository."},
    {"rule": "stale", "category": "supply_chain", "severity": "low", "checks": "No commits for over a year."},
    {"rule": "no-license", "category": "transparency", "severity": "low", "checks": "No license declared."},
    {"rule": "no-readme", "category": "transparency", "severity": "low", "checks": "No README."},
    {"rule": "llm-review", "category": "any", "severity": "any",
     "checks": "Added by the Claude reviewer; kept only when its quote matches the source verbatim."},
]
