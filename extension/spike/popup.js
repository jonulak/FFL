const api = globalThis.browser ?? globalThis.chrome;
const out = document.getElementById("out");
const verdict = document.getElementById("verdict");
let text = "";

function both(r) {
  return r && r.espn_s2 && r.espn_s2.present && r.SWID && r.SWID.present;
}

async function run() {
  out.textContent = "reading…";
  const report = await api.runtime.sendMessage({ kind: "report" });
  const viaApi = report.apiReads.filter(both).map((r) => r.label);
  const viaPage = both(report.page);
  verdict.innerHTML =
    `<div class="${viaApi.length ? "ok" : "no"}">cookies API: ${viaApi.length ? "both read (" + viaApi.join("; ") + ")" : "not both"}</div>` +
    `<div class="${viaPage ? "ok" : "no"}">document.cookie: ${viaPage ? "both read" : report.page ? "not both" : "no espn.com page seen yet"}</div>`;
  text = JSON.stringify(report, null, 1);
  out.textContent = text;
}

document.getElementById("run").addEventListener("click", run);
document.getElementById("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(text);
    verdict.insertAdjacentHTML("beforeend", "<div>copied</div>");
  } catch (e) {
    verdict.insertAdjacentHTML("beforeend", `<div class="no">copy failed: ${e}</div>`);
  }
});
run();
