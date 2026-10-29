// The native side calls show(platform, enabled, canOpen) on load and each time the app comes back to the front:
// enabled is true/false, or undefined when the system cannot say (iOS before 26.2); canOpen is whether a button can
// open Settings at the extension itself (iOS 26.2+, macOS).
let shown = {};

function show(platform, enabled, canOpen) {
    shown = {platform, enabled, canOpen: canOpen && !shown.refused, refused: shown.refused};
    canOpen = shown.canOpen;
    document.body.classList.add(`platform-${platform}`);
    const state = typeof enabled === "boolean" ? (enabled ? "on" : "off") : "unknown";
    for (const el of document.querySelectorAll("[data-show], [data-open]")) {
        const wrongState = el.dataset.show !== undefined && !el.dataset.show.split(" ").includes(state);
        const wrongOpen = el.dataset.open !== undefined && el.dataset.open !== (canOpen ? "yes" : "no");
        el.hidden = wrongState || wrongOpen;
    }
}

// Settings refused to open at the extension (iOS limits how often): name the path by hand from now on.
function cannotOpen() {
    shown.refused = true;
    show(shown.platform, shown.enabled, false);
}

function send(message) {
    webkit.messageHandlers.controller.postMessage(message);
}

for (const b of document.querySelectorAll("button.open-settings")) b.addEventListener("click", () => send("open-settings"));
for (const b of document.querySelectorAll("button.open-espn")) b.addEventListener("click", () => send("open-espn"));
for (const b of document.querySelectorAll("button.open-preferences")) b.addEventListener("click", () => send("open-preferences"));

show("unknown");
