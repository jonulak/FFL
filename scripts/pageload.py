"""Page load times for every season page on a throttled phone (4x CPU, ~fast 4G), cold cache each page.

    uv run python scripts/pageload.py [base] [path ...]     # default: production :3300, every page

Prints TTFB, FCP, LCP (and its element), DOMContentLoaded, load and transferred bytes by kind. Cross-origin
images (ESPN's CDN) send no Timing-Allow-Origin, so their bytes read 0 here; the load event still waits on
them. For page weight including images, run Lighthouse: `npx lighthouse@12.6.1 <url> --only-categories=performance`.
"""
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3300"
PAGES = sys.argv[2:] or ["/", "/roster", "/league", "/nfl", "/players", "/players?fa=0", "/transactions",
                         "/decisions", "/team/1", "/settings"]
OBS = """
window.__lcp = []; new PerformanceObserver(l => { for (const e of l.getEntries())
  window.__lcp.push([Math.round(e.startTime), e.element ? e.element.tagName + '.' + (e.element.className||'').toString().slice(0,40) : e.url.slice(0,60)]) })
  .observe({type: 'largest-contentful-paint', buffered: true});
"""
SUM = """() => { const n = performance.getEntriesByType('navigation')[0];
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  const by = {}; for (const r of performance.getEntriesByType('resource')) { by[r.initiatorType] = (by[r.initiatorType]||0) + r.transferSize }
  return { ttfb: Math.round(n.responseStart), fcp: fcp && Math.round(fcp.startTime), dcl: Math.round(n.domContentLoadedEventEnd),
           load: Math.round(n.loadEventEnd), doc: Math.round(n.transferSize/1024), lcp: window.__lcp,
           kb: Object.fromEntries(Object.entries(by).map(([k,v]) => [k, Math.round(v/1024)])) } }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for path in PAGES:
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        # ~ "fast 4G": 9 Mbps down, 1.5 up, 60 ms RTT
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 60,
                 "downloadThroughput": 9_000_000 / 8, "uploadThroughput": 1_500_000 / 8})
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        page.add_init_script(OBS)
        page.goto(BASE + path, wait_until="load", timeout=120_000)
        page.wait_for_timeout(1500)
        r = page.evaluate(SUM)
        lcp = r.pop("lcp")
        print(f"{path:18} ttfb {r['ttfb']:5} fcp {r['fcp']:5} lcp {lcp[-1][0] if lcp else '-':5} dcl {r['dcl']:5} load {r['load']:6} doc {r['doc']}K {r['kb']}  lcp-el {lcp[-1][1] if lcp else ''}")
        ctx.close()
    b.close()
