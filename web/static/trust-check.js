"use strict";
(function () {
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function renderFindings(target, findings) {
    target.replaceChildren();
    if (!findings.length) {
      target.append(el("p", "No findings."));
      return;
    }
    for (const f of findings) {
      const box = el("div", undefined, "finding sev-" + f.severity);
      box.append(el("p", "[" + f.severity + "] " + f.rule + (f.tool ? " · tool " + f.tool : ""), "mono"));
      box.append(el("p", f.message));
      if (f.quote) {
        box.append(el("p", f.file + ":" + f.line, "mono muted"));
        const pre = el("pre");
        pre.append(el("code", f.quote));
        box.append(pre);
      }
      if (f.explanation) box.append(el("p", "Reviewer note: " + f.explanation, "muted"));
      target.append(box);
    }
  }
  window.atlasRenderFindings = renderFindings;   // shared with trust-byok.js

  const form = document.getElementById("check-form");
  const out = document.getElementById("check-result");
  if (!form) return;
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    out.replaceChildren(el("p", "Checking…", "muted"));
    const response = await fetch("/api/check", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({manifest: form.manifest.value}),
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      out.replaceChildren(el("p", typeof data.detail === "string" ? data.detail : "Check failed (" + response.status + ")"));
      return;
    }
    const list = el("div");
    renderFindings(list, data.findings);
    out.replaceChildren(el("p", data.tools + " tool(s) checked, " + data.findings.length + " finding(s)."), list);
    if (data.explanation) out.append(el("p", "In plain words: " + data.explanation, "muted"));
  });
})();
