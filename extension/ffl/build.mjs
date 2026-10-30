// Builds the extension for one browser: node build.mjs --target chrome|safari [--origin https://...]
// into dist/<target>/: manifest.json, background.js, content.js, popup.html, popup.js and images/ (the icons, from
// ../icon/make-icons.sh). Safari's wrapper (Xcode) bundles exactly those five files and that folder from dist/safari,
// so their names never change. The manifest's version is package.json's: the Web Store refuses one version twice. --origin is FFL's address, fixed into the
// build (--origin, else the FFL_ORIGIN environment variable, else production; a test server's for a dev build) and
// granted as the extension's second host.
import { build } from "esbuild";
import { copyFile, cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: { target: { type: "string" }, origin: { type: "string" } } });
const target = values.target;
if (target !== "chrome" && target !== "safari") throw new Error("--target chrome|safari");
const origin = (values.origin ?? process.env.FFL_ORIGIN ?? "https://ffl.whitebridgelabs.com").replace(/\/+$/, "");
if (!/^https:\/\/[^/]+$/.test(origin)) throw new Error(`--origin must be https://host[:port], not ${origin}`);
// The site as a match pattern, without the port: Chrome and Safari both match the host on any port then, so a test
// server's :3302 needs no pattern of its own. (Safari lists a site in Settings only once it's allowed on that site, in
// Safari's page menu, whatever the pattern: the iOS simulator, 2026-10-29.)
const site = `https://${new URL(origin).hostname}/*`;

const out = `dist/${target}`;
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });

await build({
  entryPoints: { background: "src/background.ts", content: "src/content.ts", popup: "src/popup.ts" },
  outdir: out,
  bundle: true,
  format: "iife",
  target: ["chrome120", "safari16"],
  define: { __FFL_ORIGIN__: JSON.stringify(origin), __FFL_SITE__: JSON.stringify(site) },
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
  permissions: ["cookies", "storage"],
  host_permissions: ["*://*.espn.com/*", site],
  background: { service_worker: "background.js" },
  // the same script on FFL's site answers its Connect step (content.ts onFfl): linking with one tap, no code
  content_scripts: [{ matches: ["*://*.espn.com/*", site], js: ["content.js"], run_at: "document_idle" }],
  action: { default_title: "FFL", default_popup: "popup.html", default_icon: icons },
};
await writeFile(`${out}/manifest.json`, JSON.stringify(manifest, null, 2) + "\n");
console.log(`built ${out} for ${origin}`);
