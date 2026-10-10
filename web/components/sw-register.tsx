"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { openRequest } from "@/lib/notify";

// Registers web/public/sw.js on every page, so a push can arrive whichever page was last open, and takes a
// notification tap to its page: the worker posts {type: "open", url} and the page navigates itself (iOS's Home
// Screen app ignores the worker's navigate()). A tap for the page already showing re-renders it: it was rendered
// before the event the notice is about. The page says hello on load and on coming back to the foreground, so a tap
// that cold-started or woke the app is replayed here; "opened" tells the worker it landed.
export function SwRegister() {
  const router = useRouter();
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const sw = navigator.serviceWorker;
    const onMessage = (e: MessageEvent) => {
      const path = openRequest(e.data, window.location.origin);
      if (!path) return;
      (e.source as ServiceWorker | null)?.postMessage({ type: "opened" });
      if (path === window.location.pathname + window.location.search + window.location.hash) router.refresh();
      else router.push(path);
    };
    const hello = () => sw.ready.then((reg) => reg.active?.postMessage({ type: "hello" })).catch(() => {});
    const onVisible = () => {
      if (document.visibilityState === "visible") hello();
    };
    sw.addEventListener("message", onMessage);
    document.addEventListener("visibilitychange", onVisible);
    sw.startMessages();
    sw.register("/sw.js", { scope: "/", updateViaCache: "none" })
      .catch((e) => console.error("service worker registration failed", e));
    hello();
    return () => {
      sw.removeEventListener("message", onMessage);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [router]);
  return null;
}
