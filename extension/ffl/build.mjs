// Builds the extension for one browser: node build.mjs --target chrome|safari
// into dist/<target>/: manifest.json, background.js, content.js, popup.html, popup.js and images/ (the icons, from
// ../icon/make-icons.sh). Safari's wrapper (Xcode) bundles exactly those five files and that folder from dist/safari,
// so their names never change. The manifest's version is package.json's: the Web Store refuses one version twice. No
// server is built in: one build serves every FFL, which the popup learns from the open tab and asks the browser for
// (optional_host_permissions) when the user connects.
import { build } from "esbuild";
import { copyFile, cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: { target: { type: "string" } } });
const target = values.target;
if (target !== "chrome" && target !== "safari") throw new Error("--target chrome|safari");

const out = `dist/${target}`;
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

await build({
  entryPoints: { background: "src/background.ts", content: "src/content.ts", popup: "src/popup.ts" },
  outdir: out,
  bundle: true,
  format: "iife",
  target: ["chrome120", "safari16"],
  legalComments: "none",
  logLevel: "warning",
});
await copyFile("src/popup.html", `${out}/popup.html`);
await cp("src/icons", `${out}/images`, { recursive: true });
const { version } = JSON.parse(await readFile("package.json", "utf8"));
const icons = Object.fromEntries([16, 32, 48, 128].map((s) => [s, `images/icon-${s}.png`]));

const manifest = {
  manifest_version: 3,
  name: "FFL",
  version,
  description: "Connects your ESPN fantasy leagues to FFL: sends this browser's ESPN login to your FFL account, and nothing else.",
  icons,
  // activeTab: the popup may read the open tab's FFL (/health, /auth/me) when the user clicks the icon
  permissions: ["cookies", "storage", "activeTab"],
  host_permissions: ["*://*.espn.com/*"],
  optional_host_permissions: ["http://*/*", "https://*/*"],
  background: { service_worker: "background.js" },
  content_scripts: [{ matches: ["*://*.espn.com/*"], js: ["content.js"], run_at: "document_idle" }],
  action: { default_title: "FFL", default_popup: "popup.html", default_icon: icons },
};
await writeFile(`${out}/manifest.json`, JSON.stringify(manifest, null, 2) + "\n");
console.log(`built ${out}`);
