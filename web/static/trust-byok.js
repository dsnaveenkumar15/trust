"use strict";
(function () {
  const form = document.getElementById("byok-form");
  const out = document.getElementById("byok-result");
  if (!form) return;

  function line(text, cls) {
    const p = document.createElement("p");
    p.textContent = text;
    if (cls) p.className = cls;
    return p;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {method: "POST", headers: {"content-type": "application/json"},
                                       body: JSON.stringify(body)});
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "request failed (" + response.status + ")");
    return data;
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const key = document.getElementById("byok-key").value.trim();
    const input = {manifest: form.manifest.value, code: form.code.value, filename: form.filename.value || "server.py"};
    out.replaceChildren(line("Preparing the review prompt…", "muted"));
    try {
      const prepared = await postJson("/api/byok/prompt", input);
      out.replaceChildren(line("Sending " + prepared.findings + " finding(s) to the model with your key…", "muted"));
      const model = form.model.value;
      const headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                       "anthropic-dangerous-direct-browser-access": "true", "content-type": "application/json"};
      const request = {model: model, max_tokens: 16000, system: prepared.system,
                       messages: [{role: "user", content: prepared.user}],
                       output_config: {format: {type: "json_schema", schema: prepared.schema}}};
      if (model !== "claude-haiku-4-5") {
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01";
        request.fallbacks = "default";
      }
      const response = await fetch("https://api.anthropic.com/v1/messages",
                                   {method: "POST", headers: headers, body: JSON.stringify(request)});
      const message = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(message.error && message.error.message ? message.error.message : "Model API error (" + response.status + ")");
      if (message.stop_reason === "refusal") throw new Error("The model declined to review this input.");
      const text = (message.content || []).find(function (block) { return block.type === "text"; });
      if (!text) throw new Error("The model returned no JSON.");
      const result = await postJson("/api/byok/apply", Object.assign({}, input, {output: JSON.parse(text.text)}));
      const list = document.createElement("div");
      window.atlasRenderFindings(list, result.findings);
      out.replaceChildren(line("Grade " + result.grade + " (score " + result.score + "): your review, not a published audit."), list);
      for (const item of result.needs_review) out.append(line("Needs a closer look: " + item, "muted"));
    } catch (err) {
      out.replaceChildren(line(String(err && err.message ? err.message : err)));
    }
  });
})();
