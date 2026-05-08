import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist");
const htmlPath = resolve(distDir, "index.html");
const jsPath = resolve(distDir, "assets/index.js");
const cssPath = resolve(distDir, "assets/index.css");

const [html, js, css] = await Promise.all([
  readFile(htmlPath, "utf8"),
  readFile(jsPath, "utf8"),
  readFile(cssPath, "utf8"),
]);

const checks = [
  ["HTML references JS bundle", html.includes("./assets/index.js")],
  ["HTML references CSS bundle", html.includes("./assets/index.css")],
  ["JS includes DAW Lock card", js.includes("DAW Lock")],
  ["JS includes DAW-lock diagnostics", js.includes("daw_lock_diagnostics")],
  ["JS includes warp-jump localization", js.includes("Largest jump around")],
  ["JS includes grid-check download", js.includes("Download Grid Check")],
  ["JS includes grid-check playback", js.includes("Grid Check")],
  ["JS includes metronome-check output key", js.includes("metronome_check_audio")],
  ["JS includes runtime config strip", js.includes("Runtime configuration") && js.includes("runtime_config")],
  ["JS includes active engine preflight", js.includes("Active Engine") && js.includes("/api/runtime-config")],
  ["JS includes visible build stamp", js.includes("Build Stamp") && /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(js)],
  ["JS uses automatic JSX runtime", js.includes("react-jsx-runtime") && !js.includes("React.createElement")],
  ["CSS includes lock-card styles", css.includes(".lock-card")],
  ["CSS includes runtime strip styles", css.includes(".runtime-strip")],
  ["CSS includes runtime panel styles", css.includes(".runtime-panel")],
  ["CSS includes build stamp styles", css.includes(".build-stamp")],
  ["CSS includes grid-check playback note", css.includes(".wave-note")],
];

const failed = checks.filter(([, passed]) => !passed);
for (const [label, passed] of checks) {
  console.log(`${passed ? "PASS" : "FAIL"} ${label}`);
}

if (failed.length > 0) {
  throw new Error(`Static dist verification failed: ${failed.map(([label]) => label).join(", ")}`);
}
