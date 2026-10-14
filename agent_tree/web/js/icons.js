// Inline outline icons on a 16x16 grid: 1.5px rounded strokes in currentColor,
// drawn to sit next to the system font the way SF Symbols do. No external assets.
const SVG_NS = "http://www.w3.org/2000/svg";

const ICONS = {
  // navigation
  overview: "M2.75 3.75a1 1 0 0 1 1-1h2.5a1 1 0 0 1 1 1v2.5a1 1 0 0 1-1 1h-2.5a1 1 0 0 1-1-1zM8.75 3.75a1 1 0 0 1 1-1h2.5a1 1 0 0 1 1 1v2.5a1 1 0 0 1-1 1h-2.5a1 1 0 0 1-1-1zM2.75 9.75a1 1 0 0 1 1-1h2.5a1 1 0 0 1 1 1v2.5a1 1 0 0 1-1 1h-2.5a1 1 0 0 1-1-1zM8.75 9.75a1 1 0 0 1 1-1h2.5a1 1 0 0 1 1 1v2.5a1 1 0 0 1-1 1h-2.5a1 1 0 0 1-1-1z",
  threads: "M4.25 2.75v4.5a2 2 0 0 0 2 2h1.5M4.25 5.5v6.25a1.5 1.5 0 0 0 1.5 1.5h2M3 2.75h2.5M10 9.25h3.25M10 13.25h3.25",
  projects: "M2.25 4.75a1.5 1.5 0 0 1 1.5-1.5h2.4l1.4 1.5h4.7a1.5 1.5 0 0 1 1.5 1.5v5.5a1.5 1.5 0 0 1-1.5 1.5h-8.5a1.5 1.5 0 0 1-1.5-1.5zM2.25 6.75h11.5",
  models: "M4.75 3.25h6.5a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5h-6.5a1.5 1.5 0 0 1-1.5-1.5v-6.5a1.5 1.5 0 0 1 1.5-1.5zM6.25 6.25h3.5v3.5h-3.5zM6 1.25v2M10 1.25v2M6 12.75v2M10 12.75v2M1.25 6h2M1.25 10h2M12.75 6h2M12.75 10h2",
  flags: "M3.25 14.25V2.25M3.25 2.75h8.25l-1.75 3 1.75 3H3.25",
  sidebar: "M2.25 4a1.5 1.5 0 0 1 1.5-1.5h8.5a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-8.5a1.5 1.5 0 0 1-1.5-1.5zM6.25 2.5v11M3.75 5h1M3.75 7h1",
  // actions and glyphs
  search: "M7 12.25a5.25 5.25 0 1 0 0-10.5 5.25 5.25 0 0 0 0 10.5Zm3.75-1.5 3.5 3.5",
  caret: "M6 3.75 10.25 8 6 12.25",
  close: "M4.25 4.25l7.5 7.5M11.75 4.25l-7.5 7.5",
  sun: "M8 10.75a2.75 2.75 0 1 0 0-5.5 2.75 2.75 0 0 0 0 5.5ZM8 1.25v1.25M8 13.5v1.25M1.25 8H2.5M13.5 8h1.25M3.2 3.2l.9.9M11.9 11.9l.9.9M12.8 3.2l-.9.9M4.1 11.9l-.9.9",
  moon: "M13.25 9.6A5.75 5.75 0 0 1 6.4 2.75a5.5 5.5 0 1 0 6.85 6.85Z",
  refresh: "M13.25 8a5.25 5.25 0 1 1-1.54-3.71M13.25 2.25v2.5a.5.5 0 0 1-.5.5h-2.5",
  download: "M8 2.25v8M4.75 7 8 10.25 11.25 7M2.75 13.25h10.5",
  keyboard: "M3.25 3.75h9.5a1.5 1.5 0 0 1 1.5 1.5v5.5a1.5 1.5 0 0 1-1.5 1.5h-9.5a1.5 1.5 0 0 1-1.5-1.5v-5.5a1.5 1.5 0 0 1 1.5-1.5zM4.5 6.5h.01M7 6.5h.01M9.5 6.5h.01M12 6.5h.01M5.5 9.25h5",
  copy: "M6.25 5.25h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-6a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1zM2.75 10V3.75a1 1 0 0 1 1-1H10",
  check: "M3.5 8.5 6.5 11.5 12.5 4.5",
  x: "M5 5l6 6M11 5l-6 6",
  run: "M8 3v5l3 2",
  alertCircle: "M8 1.75a6.25 6.25 0 1 0 0 12.5 6.25 6.25 0 0 0 0-12.5ZM8 4.75v3.75M8 11v.01",
  info: "M8 1.75a6.25 6.25 0 1 0 0 12.5 6.25 6.25 0 0 0 0-12.5ZM8 7.25v4M8 4.75v.01",
  tree: "M4 2.5v11M4 6h5M4 12h5",
  timeline: "M2.25 4.25h7M5.25 8h8.5M3.25 11.75h6",
  empty: "M2.25 9.25h3l1 1.75h3.5l1-1.75h3M2.25 9.25l1.6-5a1 1 0 0 1 .95-.75h6.4a1 1 0 0 1 .95.75l1.6 5v3a1 1 0 0 1-1 1h-10.5a1 1 0 0 1-1-1z",
  plug: "M6 1.75v3M10 1.75v3M4.25 4.75h7.5v2.5a3.75 3.75 0 0 1-7.5 0zM8 11v3.25",
  filter: "M2.25 3.5h11.5M4.25 8h7.5M6.5 12.5h3",
};

export function icon(name, size = 14) {
  const svg = document.createElementNS(SVG_NS, "svg");
  const attrs = { class: "ic", width: size, height: size, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.5, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" };
  for (const [k, v] of Object.entries(attrs)) svg.setAttribute(k, String(v));
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || "");
  svg.append(path);
  return svg;
}
