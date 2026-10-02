PLANETS = {
    "trust": {"name": "Trust", "tagline": "Evidence-based security grades for MCP servers and agent tools.",
              "lede": "Paste the tool list of an MCP server and see what it could do on your machine before you "
                      "install it: hidden instructions, shell access, credential reads.",
              "action": ("Check a server", "/check"), "second": ("How grades work", "/methodology")},
    "radar": {"name": "Radar", "tagline": "What changed in AI engineering, and whether it breaks your stack.",
              "lede": "A weekly digest of releases in the AI libraries people build on, and a check that tells you "
                      "which of those changes break your own dependency file.",
              "action": ("Check my dependencies", "/upgrade"), "second": ("Ask about a change", "/ask")},
    "blueprints": {"name": "Blueprints", "tagline": "Linted, living reference architectures for agentic systems.",
                   "lede": "Reference architectures for AI agents, each one checked by a linter, priced per month and "
                           "updated when the tools it relies on change.",
                   "action": ("Find one for my case", "/finder"), "second": ("Draft a custom one", "/custom")},
}
