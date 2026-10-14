"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { openRequest } from "@/lib/notify";

// on-device trace of the tap path (ffl-e9x.10), stamped by the API; sendBeacon survives a page going away
function trace(event: string, fields: Record<string, unknown> = {}) {
  try {
    navigator.sendBeacon("/api/trace", new Blob([JSON.stringify({ event, fields })], { type: "application/json" }));
  } catch {
    // a trace never breaks the page
  }
}

// Registers web/public/sw.js on every page, so a push can arrive whichever page was last open, and takes a
// notification tap to its page: the worker posts {type: "open", url} and the page navigates itself (iOS's Home
// Screen app ignores the worker's navigate()). A tap for the page already showing re-renders it: it was rendered
// before the event the notice is about. The page says hello on load and on coming back to the foreground, so a tap
// that cold-started or woke the app is replayed here; "opened" tells the worker it landed.
export function SwRegister() {
  const router = useRouter();
  // "nav": the page the router actually arrived at. This component lives in the root layout and never remounts on a
  // soft navigation, so an "open" row followed by no "nav" row is a router.push that went nowhere.
  const path = usePathname();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) first.current = false;
    else trace("nav", { here: path });
  }, [path]);
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const sw = navigator.serviceWorker;
    const onMessage = (e: MessageEvent) => {
      const path = openRequest(e.data, window.location.origin);
      if (!path) return;
      const here = window.location.pathname + window.location.search + window.location.hash;
      trace("open", { path, here, visibility: document.visibilityState });
      (e.source as ServiceWorker | null)?.postMessage({ type: "opened" });
      if (path === here) router.refresh();
      else router.push(path);
    };
    const hello = () => {
      trace("hello", { here: window.location.pathname, visibility: document.visibilityState, controlled: Boolean(sw.controller) });
      return sw.ready.then((reg) => reg.active?.postMessage({ type: "hello" })).catch(() => {});
    };
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
