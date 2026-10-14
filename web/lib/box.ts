import type { BoxItem, Volume } from "./api";

// A count as the box score prints it: 250, 1.5 sacks.
export const count = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
// Points to the hundredth, as ESPN scores them (5.52, 8.00), so the line items add up on the
// page to the total beneath them.
export const points = (n: number) => {
  const s = Math.abs(n).toFixed(2);
  return n < 0 ? `−${s}` : s;
};
// The league's rate: "+0.04 each", "−2 each"; a band row is a flat award, "+1 flat".
export const rule = (b: BoxItem) => `${b.rate < 0 ? "−" : "+"}${Math.abs(b.rate)} ${b.flat ? "flat" : "each"}`;
// The line items summed to the hundredth, the total the breakdown rules off to.
export const boxSum = (box: BoxItem[]) => Math.round(box.reduce((t, b) => t + b.points, 0) * 100) / 100;
// His volume under the breakdown: "9 targets", "18 carries"; nothing for a K or a D/ST.
export const volumeText = (v: Volume | null | undefined) => (v ? `${v.count} ${v.label}` : undefined);
