/* Light / dark theme for every naveenlabs site. Loaded in <head> (no inline script: the CSP forbids it), so the
   theme is set before the page paints. The choice lives in a cookie on .naveenlabs.tech, so it follows the visitor
   across sites; localStorage is the fallback (local development). Without a choice: the app's signature mode
   (<html data-default-theme>), else the system setting. */
(function () {
  var root = document.documentElement;
  var shared = /(^|\.)naveenlabs\.tech$/.test(location.hostname);
  function saved() {
    var m = document.cookie.match(/(?:^|;\s*)theme=(light|dark)/);
    if (m) return m[1];
    try { return localStorage.getItem("theme"); } catch (e) { return null; }
  }
  function save(theme) {
    if (shared) document.cookie = "theme=" + theme + "; domain=.naveenlabs.tech; path=/; max-age=31536000; SameSite=Lax; Secure";
    try { localStorage.setItem("theme", theme); } catch (e) {}
  }
  var theme = saved();
  if (theme !== "light" && theme !== "dark") {
    // No explicit choice: the app's signature mode, then the system setting.
    var signature = root.getAttribute("data-default-theme");
    theme = signature === "light" || signature === "dark" ? signature
      : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  root.setAttribute("data-theme", theme);
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-theme-toggle]");
    if (!button) return;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    save(next);
  });
})();
