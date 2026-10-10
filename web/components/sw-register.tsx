"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { openRequest } from "@/lib/notify";

// Registers web/public/sw.js on every page, so a push can arrive whichever page was last open, and takes a
// notification tap to its page: the worker posts {type: "open", url} and the page navigates itself (iOS's Home
// Screen app ignores the worker's navigate()). On load the page says hello, so a tap that cold-started the app
// (which iOS may open at start_url) is replayed here; "opened" tells the worker it landed.
export function SwRegister() {
  const router = useRouter();
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const sw = navigator.serviceWorker;
    const onMessage = (e: MessageEvent) => {
      const path = openRequest(e.data, window.location.origin);
      if (!path) return;
      (e.source as ServiceWorker | null)?.postMessage({ type: "opened" });
      if (path !== window.location.pathname + window.location.search + window.location.hash) router.push(path);
    };
    sw.addEventListener("message", onMessage);
    sw.startMessages();
    sw.register("/sw.js", { scope: "/", updateViaCache: "none" })
      .catch((e) => console.error("service worker registration failed", e));
    sw.ready.then((reg) => reg.active?.postMessage({ type: "hello" })).catch(() => {});
    return () => sw.removeEventListener("message", onMessage);
  }, [router]);
  return null;
}
