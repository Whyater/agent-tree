// Runs before first paint (classic script, no inline code because of the CSP):
// pick the saved theme, else follow the OS preference.
(function () {
  var t = null;
  try { t = localStorage.getItem("agent-tree.theme"); } catch (e) { /* storage blocked */ }
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", t);
})();
