import esbuild from "esbuild-wasm/lib/browser.js";
import { existsSync } from "node:fs";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, extname, isAbsolute, join, resolve } from "node:path";
import { dirname as posixDirname } from "node:path/posix";

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist");
const assetsDir = resolve(distDir, "assets");
const nodeModules = resolve(root, "node_modules");
const buildStamp = new Date().toISOString();

globalThis.self = globalThis;

const wasmModule = await WebAssembly.compile(await readFile(resolve(nodeModules, "esbuild-wasm/esbuild.wasm")));
await esbuild.initialize({ wasmModule, worker: false });

await rm(distDir, { recursive: true, force: true });
await mkdir(assetsDir, { recursive: true });

function extensionCandidates(path) {
  return extname(path) ? [path] : [`${path}.js`, `${path}.jsx`, `${path}.css`, join(path, "index.js")];
}

function toVirtualPath(path) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.startsWith("/") ? normalized : `/${normalized}`;
}

function fromVirtualPath(path) {
  const normalized = path.startsWith("/") && /^[A-Za-z]:/.test(path.slice(1)) ? path.slice(1) : path;
  return normalized.replace(/\//g, "\\");
}

async function resolveExisting(path) {
  for (const candidate of extensionCandidates(path)) {
    try {
      await readFile(candidate);
      return candidate;
    } catch {
      // Try the next extension.
    }
  }
  return path;
}

function packageEntry(specifier) {
  if (specifier === "react") return resolve(nodeModules, "react/index.js");
  if (specifier === "react/jsx-runtime") return resolve(nodeModules, "react/jsx-runtime.js");
  if (specifier === "react/jsx-dev-runtime") return resolve(nodeModules, "react/jsx-dev-runtime.js");
  if (specifier === "react-dom") return resolve(nodeModules, "react-dom/index.js");
  if (specifier === "react-dom/client") return resolve(nodeModules, "react-dom/client.js");
  if (specifier === "wavesurfer.js") return resolve(nodeModules, "wavesurfer.js/dist/wavesurfer.esm.js");
  return resolve(nodeModules, specifier);
}

const filePlugin = {
  name: "workspace-file-loader",
  setup(build) {
    build.onResolve({ filter: /.*/ }, async (args) => {
      if (args.path.startsWith("http:") || args.path.startsWith("https:")) {
        return { external: true };
      }
      const baseDir = args.resolveDir ? fromVirtualPath(args.resolveDir) : root;
      const rawPath = args.path.startsWith(".")
        ? resolve(baseDir, args.path)
        : isAbsolute(args.path)
          ? args.path
          : packageEntry(args.path);
      return { path: toVirtualPath(await resolveExisting(rawPath)), namespace: "workspace" };
    });

    build.onLoad({ filter: /.*/, namespace: "workspace" }, async (args) => {
      const actualPath = fromVirtualPath(args.path);
      const contents = await readFile(actualPath, "utf8");
      const ext = extname(args.path).toLowerCase();
      const loader = ext === ".css" ? "css" : ext === ".jsx" ? "jsx" : "js";
      return { contents, loader, resolveDir: posixDirname(args.path) };
    });
  },
};

const buildResult = await esbuild.build({
  entryPoints: [resolve(root, "src/main.jsx")],
  bundle: true,
  format: "esm",
  jsx: "automatic",
  target: ["es2020"],
  outdir: toVirtualPath(assetsDir),
  entryNames: "index",
  define: {
    "import.meta.env.VITE_API_BASE": "undefined",
    "import.meta.env.VITE_BOXBOX_BUILD_STAMP": JSON.stringify(buildStamp),
    "process.env.NODE_ENV": '"production"',
  },
  minify: true,
  sourcemap: false,
  logLevel: "info",
  write: false,
  plugins: [filePlugin],
});

for (const file of buildResult.outputFiles) {
  const outputPath = fromVirtualPath(file.path);
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, file.contents);
}

const cssTag = existsSync(resolve(assetsDir, "index.css"))
  ? '    <link rel="stylesheet" href="./assets/index.css" />\n'
  : "";

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>BoxBox</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
${cssTag}  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="./assets/index.js"></script>
  </body>
</html>
`;

await writeFile(resolve(distDir, "index.html"), html, "utf8");
console.log("WASM frontend build complete: frontend/dist");
