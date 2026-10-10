import type { MetadataRoute } from "next";

// Makes FFL a Home Screen app: iOS delivers Web Push only to one (16.4+). start_url is the page a notice-less launch wants.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "FFL", short_name: "FFL", description: "2026 season cockpit",
    start_url: "/roster", scope: "/", display: "standalone",
    background_color: "#f2f4f7", theme_color: "#f2f4f7", // globals.css --background, light
    icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }, { src: "/apple-icon.png", sizes: "180x180", type: "image/png" }],
  };
}
