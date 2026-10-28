// A session's device as Settings names it (ffl-soc.6.2.4), from the user agent its sign-in sent: "Chrome on Mac",
// "Safari on iPhone". Rough on purpose: enough to tell one's own devices apart, never a fingerprint. An agent it cannot
// read is "Unknown device".
const BROWSERS: [RegExp, string][] = [
  [/Edg\//, "Edge"], [/OPR\//, "Opera"], [/Firefox\/|FxiOS\//, "Firefox"], [/CriOS\/|Chrome\//, "Chrome"],
  [/Safari\//, "Safari"],
];
const SYSTEMS: [RegExp, string][] = [
  [/iPhone/, "iPhone"], [/iPad/, "iPad"], [/Android/, "Android"], [/Macintosh|Mac OS X/, "Mac"],
  [/Windows/, "Windows"], [/CrOS/, "ChromeOS"], [/Linux/, "Linux"],
];

export function deviceLabel(ua: string): string {
  const browser = BROWSERS.find(([re]) => re.test(ua))?.[1];
  const system = SYSTEMS.find(([re]) => re.test(ua))?.[1];
  if (browser && system) return `${browser} on ${system}`;
  // an iPhone Home Screen app's WKWebView drops "Safari/" from the agent
  if (system && /AppleWebKit/.test(ua)) return `Safari on ${system}`;
  return browser ?? system ?? "Unknown device";
}
