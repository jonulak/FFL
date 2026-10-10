"use client";

import { useEffect } from "react";

// Registers web/public/sw.js on every page, so a push can arrive whichever page was last open.
export function SwRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" })
        .catch((e) => console.error("service worker registration failed", e));
    }
  }, []);
  return null;
}
