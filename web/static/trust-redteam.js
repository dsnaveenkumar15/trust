/* Red team pages: start a run and follow it live. Loaded from a file (the CSP forbids inline script); every
   visitor- or model-visible string is set with textContent. */
(function () {
  "use strict";
  var form = document.getElementById("rt-form");
  if (form) {
    var text = document.getElementById("rt-text");
    form.querySelectorAll("input[name=kind]").forEach(function (r) {
      r.addEventListener("change", function () {
        text.placeholder = r.value === "repo" ? "https://github.com/owner/repo" : '{"tools": [{"name": "notes", "description": "..."}]}';
      });
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var error = document.getElementById("rt-error");
      button.disabled = true;
      fetch("/api/redteam/runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
        body: JSON.stringify({ kind: form.querySelector("input[name=kind]:checked").value, text: text.value,
                               consent: document.getElementById("rt-consent").checked })
      })
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            if (!r.ok) { throw new Error(typeof d.detail === "string" ? d.detail : "Something went wrong. Please try again."); }
            location.href = "/redteam/r/" + d.id;
          });
        })
        .catch(function (err) {
          button.disabled = false;
          error.textContent = err.message === "Failed to fetch" ? "The red team could not be reached. Check your connection and try again." : err.message;
          error.hidden = false;
        });
    });
  }

  var root = document.getElementById("rt-run");
  if (!root) { return; }
  var status = root.getAttribute("data-status");
  if (status !== "queued" && status !== "running") { return; }
  var log = document.getElementById("rt-log");
  var totals = document.getElementById("rt-totals");

  function line(t) {
    var li = document.createElement("li");
    li.textContent = t;
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function step(name, state) {
    var node = document.querySelector('[data-step="' + name + '"]');
    if (!node) { return; }
    node.classList.toggle("is-active", state === "start");
    node.classList.toggle("is-done", state !== "start");
  }

  var source = new EventSource("/api/redteam/runs/" + root.getAttribute("data-id") + "/events");
  source.onmessage = function (m) {
    var e = JSON.parse(m.data), p = e.payload || {};
    if (e.type === "node") { step(p.name, p.status); }
    else if (e.type === "usage") {
      totals.textContent = "paid $" + Number(p.total_usd).toFixed(4) + " of $" + totals.getAttribute("data-cap") + " · " + p.total_tokens + " tokens";
    }
    else if (e.type === "scenario") {
      var by = Object.keys(p.victims || {}).map(function (k) { return k + " " + String(p.victims[k]).replace("_", " "); }).join(", ");
      line(p.index + ". " + p.id + " on " + p.tool + ": " + (by || String(p.verdict).replace("_", " ")));
    }
    else if (e.type === "fixcheck") { line("   fix re-test for " + p.id + ": " + String(p.result).replace("_", " ")); }
    else if (e.type === "error") { line(p.text || ""); }
    else if (e.type === "result") { line(p.headline || ""); }
  };
  source.addEventListener("end", function () { source.close(); location.reload(); });
})();
