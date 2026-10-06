PLANETS = {
    "trust": {
        "name": "Trust", "tagline": "Evidence-based security grades for MCP servers and agent tools.",
        "headline": "Know what an agent tool can do before you install it.",
        "lede": "Paste the tool list of an MCP server. Trust reads every description and shows hidden instructions, "
                "shell access and credential reads, with the exact text that triggered each one.",
        "action": ("Check a server", "/check"), "second": ("How grades work", "/methodology"),
        "theme": {"default": "dark", "css": "/static/themes/trust/theme.css?v=3", "mark": "/static/themes/trust/mark.svg",
                  "preload": ["/static/themes/trust/fonts/ibm-plex-sans-latin.woff2"]},
        "steps": [("Paste the tool list", "Copy the JSON your server returns for tools/list."),
                  ("Every description is read", "Rules look for hidden instructions, tool shadowing, invisible "
                                                "characters and risky capabilities."),
                  ("See each finding", "Each one comes with its severity and the exact text. Nothing you paste is "
                                       "stored.")],
    },
    "radar": {
        "name": "Radar", "tagline": "What changed in AI engineering, and whether it breaks your stack.",
        "headline": "Know which AI library changes break your code.",
        "lede": "Radar follows the releases of the libraries AI apps are built on and tells you which breaking "
                "changes, deprecations and advisories sit between your versions and the latest.",
        "action": ("Check my dependencies", "/upgrade"), "second": ("Ask about a change", "/ask"),
        "theme": {"default": "dark", "css": "/static/themes/radar/theme.css?v=5", "mark": "/static/themes/radar/mark.svg",
                  "preload": ["/static/themes/radar/fonts/jetbrains-mono-latin.woff2"]},
        "steps": [("Paste your dependency file", "requirements.txt, package.json or a lock file. It is read in your "
                                                 "browser."),
                  ("Versions are compared", "Your pinned versions are matched against every release Radar has "
                                            "tracked."),
                  ("See what changes for you", "Breaking changes, deprecations, migrations and security advisories, "
                                               "version by version.")],
    },
    "blueprints": {
        "name": "Blueprints", "tagline": "Linted, living reference architectures for agentic systems.",
        "headline": "Start your AI agent from a checked, priced design.",
        "lede": "Reference architectures for AI agents. Each one passes a linter, comes with a monthly cost "
                "estimate and is updated when the tools it relies on change.",
        "action": ("Find one for my case", "/finder"), "second": ("Draft a custom one", "/custom"),
        "steps": [("Describe your case", "Answer a few questions about the task, its risk and its volume."),
                  ("Get matching blueprints", "Each shows its topology, required controls, failure modes and cost "
                                              "per month."),
                  ("Compare or customise", "Put two side by side, or draft your own and check it with the same "
                                           "linter.")],
    },
}
