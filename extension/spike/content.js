// Path (ii): document.cookie on an espn.com page. Only what a cookie looks like leaves this script (present, length,
// braces), never its value; the background keeps the last report for the popup.
const api = globalThis.browser ?? globalThis.chrome;

function shape(name) {
  const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  if (!hit) return { present: false };
  const v = decodeURIComponent(hit.slice(name.length + 1));
  return { present: true, length: v.length, braces: v.startsWith("{") && v.endsWith("}") };
}

api.runtime.sendMessage({
  kind: "page",
  host: location.host,
  at: new Date().toISOString(),
  espn_s2: shape("espn_s2"),
  SWID: shape("SWID"),
});
