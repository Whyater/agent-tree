// Runs before first paint (classic script, no inline code because of the CSP):
// pick the saved theme, else follow the OS preference. Also sets the shell
// classes that change layout, so the first frame is already in its final shape.
(function () {
  var root = document.documentElement;
  var t = null;
  var side = null;
  try {
    t = localStorage.getItem("agent-tree.theme");
    side = localStorage.getItem("agent-tree.sidebar");
  } catch (e) { /* storage blocked */ }
  if (t !== "light" && t !== "dark") {
    t = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  root.setAttribute("data-theme", t);
  // store.set() in util.js saves JSON, so the saved strings include their quotes.
  // Same rule as sidebarRail() in app.js: forced when narrow, collapsed by choice, or auto on the thread page.
  var onThread = /^#\/thread\//.test(location.hash || "");
  if (window.innerWidth < 1000 || side === "\"collapsed\"" || (onThread && side !== "\"open\"" && window.innerWidth < 1500)) root.classList.add("sidebar-rail");
  // Inside the Mac app the title bar is transparent and the page runs under it.
  // See the note at the top of app.css.
  var native = /AgentTreeApp/.test(navigator.userAgent || "");
  try { native = native || new URLSearchParams(location.search).get("native") === "1"; } catch (e) { /* old engine */ }
  if (native) root.classList.add("native-window");
})();
