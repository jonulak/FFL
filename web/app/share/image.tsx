import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import type { ReactNode } from "react";
import type { Player, Side, Week } from "@/lib/api";
import { HEALTHY, isIdle, SLOT_LABEL, STATUS_SHORT } from "@/lib/board";
import { injuryTone, pts, record, shortName } from "@/lib/format";
import { gamePhase, toPlay } from "@/lib/game";
import { pairStarters } from "@/lib/lineup";
import { mixHex, type ShareKind } from "@/lib/share";
import { headshotArt, logoArt, team } from "@/lib/teams";

/* eslint-disable @next/next/no-img-element -- satori draws a plain img */

// The share image's own board. Satori lays out flexbox only, and takes neither oklch nor color-mix, so this is its own
// JSX in the dark board's colours as hex (globals.css .dark); it carries no call, reason or outlook, since it goes to
// the managers he plays. 1080 wide: on a phone it shows at about 0.36x, so no text goes below 24 px.
const WIDTH = 1080;
const MARGIN = 40;
const FIELD = "#07080b";
const PANEL = "#13161b";
const LINE = "#262a31";
const INK = "#fafafa";
const MUTED = "#a1a1a1";
const WARN = "#f7be2b";
const BAD = "#ff6367";
const TONE = { ok: MUTED, warn: WARN, bad: BAD };

const TITLE_H = 104;
const BUG_H = 300;
const ROW_H = 84;
const LABEL_H = 64;
const CARD_PAD = 16;
const TILE = 72;
const PORTRAIT_RATIO = 436 / 600; // ESPN's portraits are 600x436

export type Marks = { me: string | null; opp: string | null };
// The art that loaded, by its URL, as data URLs: satori fetches nothing itself.
export type Art = Map<string, string>;

const ART_MS = 1_500;
const ART_CSS = 80; // the width TeamPanel's tile asks ESPN for
export const ART_KEEP_S = 7 * 86400;

const portraitUrl = (p: Player) => headshotArt(p.player_id, ART_CSS);
const crestUrl = (p: Player) => (p.team ? logoArt(p.team, "light", ART_CSS) : null);

// A response as a data URL, or null for what satori cannot draw.
export async function dataUrl(r: Response): Promise<string | null> {
  const type = (r.headers.get("content-type") ?? "").split(";")[0];
  if (!r.ok || !/^image\/(png|jpeg|svg\+xml)$/.test(type)) return null;
  return `data:${type};base64,${Buffer.from(await r.arrayBuffer()).toString("base64")}`;
}

// Every player's portrait and club mark, side by side so the whole set waits at most ART_MS: the share sheet needs the
// tap still fresh when the image lands. Next's data cache keeps them, as the service worker keeps them for the pages.
export async function loadArt(players: Player[]): Promise<Art> {
  const urls = [...new Set(players.flatMap((p) => [portraitUrl(p), crestUrl(p)]).filter((u): u is string => u != null))];
  const got = await Promise.all(urls.map(async (u) => {
    try {
      return [u, await dataUrl(await fetch(u, { cache: "force-cache", next: { revalidate: ART_KEEP_S }, signal: AbortSignal.timeout(ART_MS) }))] as const;
    } catch {
      return [u, null] as const;
    }
  }));
  return new Map(got.filter((e): e is readonly [string, string] => e[1] != null));
}

const fonts = Promise.all([["SemiBold", 600], ["Bold", 700]].map(async ([file, weight]) => ({
  name: "Barlow Condensed", weight: weight as 600 | 700, style: "normal" as const,
  data: await readFile(join(process.cwd(), `public/fonts/BarlowCondensed-${file}.ttf`)),
})));

const slotCode = (s: string) => SLOT_LABEL[s] ?? s;
const started = (s: Side, now: number) => s.actual_total > 0 || s.lineup.some((p) => gamePhase(p, now) !== "pending");

function Mark({ src, abbrev, size }: { src: string | null; abbrev: string; size: number }) {
  if (src) return <img src={src} width={size} height={size} alt="" style={{ borderRadius: 10, objectFit: "contain" }} />;
  return (
    <div style={{ width: size, height: size, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 10, background: LINE, color: INK, fontSize: size * 0.34, fontWeight: 700 }}>
      {abbrev.slice(0, 4)}
    </div>
  );
}

// TeamPanel's tile: the club's gradient, the mark as a watermark trailing off the left edge, the portrait at the foot.
// No portrait (a D/ST, or a missing file): the mark full size. A player with neither is the bare gradient.
function Tile({ p, art }: { p: Player; art: Art }) {
  const t = team(p.team);
  const portrait = art.get(portraitUrl(p) ?? "");
  const crest = art.get(crestUrl(p) ?? "");
  return (
    <div style={{ position: "relative", width: TILE, height: TILE, flexShrink: 0, display: "flex", overflow: "hidden", borderRadius: 6, opacity: isIdle(p) ? 0.6 : 1, backgroundImage: `linear-gradient(168deg, ${t.primary} 0%, ${mixHex(t.primary, t.secondary, 0.62)} 100%)` }}>
      {crest && (portrait
        ? <img src={crest} alt="" width={TILE * 1.3} height={TILE * 1.3} style={{ position: "absolute", left: -TILE * 0.35, top: TILE * 0.18, opacity: 0.16 }} />
        : <img src={crest} alt="" width={TILE * 0.72} height={TILE * 0.72} style={{ position: "absolute", left: TILE * 0.14, top: TILE * 0.14 }} />)}
      {portrait && <img src={portrait} alt="" width={TILE * 1.28} height={TILE * 1.28 * PORTRAIT_RATIO} style={{ position: "absolute", left: -TILE * 0.14, bottom: 0 }} />}
    </div>
  );
}

// His number and what kind it is: a forecast stays quiet, a posted score stands in ink.
function Number_({ p, now, opp }: { p: Player; now: number; opp: boolean }) {
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
  return (
    <div style={{ width: 92, flexShrink: 0, display: "flex", flexDirection: "column", alignItems: opp ? "flex-start" : "flex-end" }}>
      <span style={{ fontSize: 40, fontWeight: 700, lineHeight: 1, color: on ? INK : MUTED }}>{pts(on ? p.actual : p.proj)}</span>
      <span style={{ marginTop: 4, fontSize: 24, fontWeight: 600, lineHeight: 1, letterSpacing: 1, color: MUTED }}>{p.bye ? "BYE" : on ? phase.toUpperCase() : "PROJ"}</span>
    </div>
  );
}

function Who({ p, opp }: { p: Player; opp: boolean }) {
  const hurt = !HEALTHY.has(p.injury);
  const status = p.injury.replaceAll("_", " ");
  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: opp ? "flex-end" : "flex-start" }}>
      <span style={{ maxWidth: "100%", fontSize: 36, fontWeight: 600, lineHeight: 1.1, color: INK, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{shortName(p.name)}</span>
      <div style={{ display: "flex", flexDirection: opp ? "row-reverse" : "row", fontSize: 26, fontWeight: 600, lineHeight: 1.1, color: MUTED }}>
        <span>{p.pos}</span>
        {hurt && <span style={{ margin: "0 8px", color: TONE[injuryTone(p.injury)], fontWeight: 700 }}>{STATUS_SHORT[status] ?? status}</span>}
      </div>
    </div>
  );
}

// One half of a board row, the opponent's mirrored; an empty slot stays a blank half.
function Half({ p, now, art, opp = false }: { p: Player | undefined; now: number; art: Art; opp?: boolean }) {
  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", flexDirection: opp ? "row-reverse" : "row", gap: 12 }}>
      {p && <Tile p={p} art={art} />}
      {p && <Who p={p} opp={opp} />}
      {p && <Number_ p={p} now={now} opp={opp} />}
    </div>
  );
}

const Slot = ({ s }: { s: string }) => (
  <span style={{ width: 64, flexShrink: 0, textAlign: "center", justifyContent: "center", display: "flex", fontSize: 26, fontWeight: 600, letterSpacing: 1, color: MUTED }}>{slotCode(s)}</span>
);

const Row = ({ children, first }: { children: ReactNode; first: boolean }) => (
  <div style={{ height: ROW_H, display: "flex", alignItems: "center", gap: 8, borderTop: first ? "none" : `1px solid ${LINE}` }}>{children}</div>
);

// A column, because satori lays a fragment out as a row.
const Stack = ({ children }: { children: ReactNode }) => <div style={{ width: "100%", display: "flex", flexDirection: "column" }}>{children}</div>;

const Board = ({ children }: { children: ReactNode }) => (
  <div style={{ display: "flex", flexDirection: "column", padding: `${CARD_PAD}px 24px`, background: PANEL, borderRadius: 14 }}>{children}</div>
);

const Title = ({ title, week }: { title: string; week: number }) => (
  <div style={{ height: TITLE_H, display: "flex", alignItems: "flex-start", fontSize: 72, fontWeight: 700, lineHeight: 1, letterSpacing: -1 }}>
    <span>{title}</span>
    <span style={{ marginLeft: 20, fontWeight: 600, color: MUTED }}>Week {week}</span>
  </div>
);

function Total({ side, mark, now, opp = false }: { side: Side; mark: string | null; now: number; opp?: boolean }) {
  const on = started(side, now);
  const left = on ? toPlay(side.lineup, isIdle, now) : 0;
  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: opp ? "flex-end" : "flex-start" }}>
      <div style={{ width: "100%", height: 150, display: "flex", flexDirection: opp ? "row-reverse" : "row", gap: 16 }}>
        <Mark src={mark} abbrev={side.abbrev} size={88} />
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: opp ? "flex-end" : "flex-start" }}>
          <span style={{ fontSize: 40, fontWeight: 600, lineHeight: 1.05, color: INK, textAlign: opp ? "right" : "left", lineClamp: 2, display: "block" }}>{side.team}</span>
          <span style={{ marginTop: 8, fontSize: 28, fontWeight: 600, color: MUTED }}>{record(side)}</span>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", flexDirection: opp ? "row-reverse" : "row", gap: 12 }}>
        <span style={{ fontSize: 96, fontWeight: 700, lineHeight: 1, letterSpacing: -1, color: INK }}>{pts(on ? side.actual_total : side.proj_total)}</span>
        <span style={{ fontSize: 28, fontWeight: 600, letterSpacing: 2, color: MUTED }}>{!on ? "PROJ" : left > 0 ? `${left} TO PLAY` : "FINAL"}</span>
      </div>
    </div>
  );
}

function Matchup({ data, marks, art, now }: { data: Week; marks: Marks; art: Art; now: number }) {
  const rows = pairStarters(data.slots, data.me.lineup, data.opp?.lineup ?? []);
  return (
    <Stack>
      <Title title="Matchup" week={data.week} />
      <div style={{ height: BUG_H - 16, display: "flex", alignItems: "center", gap: 24, padding: "0 24px", marginBottom: 16, background: PANEL, borderRadius: 14 }}>
        <Total side={data.me} mark={marks.me} now={now} />
        {data.opp && <span style={{ alignSelf: "flex-end", marginBottom: 48, fontSize: 30, fontWeight: 600, letterSpacing: 4, color: MUTED }}>VS</span>}
        {data.opp
          ? <Total side={data.opp} mark={marks.opp} now={now} opp />
          : <span style={{ flex: 1, textAlign: "right", justifyContent: "flex-end", display: "flex", fontSize: 32, fontWeight: 600, color: MUTED }}>No opponent this week</span>}
      </div>
      <Board>
        {rows.map(([slot, a, b], i) => (
          <Row key={`${slot}-${i}`} first={i === 0}>
            <Half p={a} now={now} art={art} />
            <Slot s={slot} />
            {data.opp && <Half p={b} now={now} art={art} opp />}
          </Row>
        ))}
      </Board>
    </Stack>
  );
}

function Section({ title, players, now, art, slots }: { title: string; players: Player[]; now: number; art: Art; slots: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span style={{ height: LABEL_H, display: "flex", alignItems: "center", fontSize: 30, fontWeight: 600, letterSpacing: 4, textTransform: "uppercase", color: MUTED }}>{title}</span>
      {players.map((p, i) => (
        <Row key={p.player_id} first={i === 0}>
          <span style={{ width: 72, flexShrink: 0, display: "flex", fontSize: 26, fontWeight: 600, letterSpacing: 1, color: MUTED }}>{slots ? slotCode(p.slot) : ""}</span>
          <Half p={p} now={now} art={art} />
        </Row>
      ))}
    </div>
  );
}

function RosterImage({ data, mark, art, now }: { data: Week; mark: string | null; art: Art; now: number }) {
  const me = data.me;
  return (
    <Stack>
      <div style={{ height: 136, display: "flex", alignItems: "flex-start", gap: 20 }}>
        <Mark src={mark} abbrev={me.abbrev} size={104} />
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <span style={{ fontSize: 64, fontWeight: 700, lineHeight: 1, letterSpacing: -1, color: INK, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{me.team}</span>
          <span style={{ marginTop: 8, fontSize: 32, fontWeight: 600, color: MUTED }}>{`${record(me)} · Week ${data.week}`}</span>
        </div>
      </div>
      <Board>
        <Section title="Starters" players={me.lineup} now={now} art={art} slots />
        {me.bench.length > 0 && <Section title="Bench" players={me.bench} now={now} art={art} slots={false} />}
        {me.ir.length > 0 && <Section title="IR" players={me.ir} now={now} art={art} slots={false} />}
      </Board>
    </Stack>
  );
}

// Satori needs the image's height up front, so it is the sum of what the kind stacks.
function height(kind: ShareKind, data: Week): number {
  const frame = 2 * MARGIN + 2 * CARD_PAD;
  if (kind === "matchup") {
    return frame + TITLE_H + BUG_H + ROW_H * pairStarters(data.slots, data.me.lineup, data.opp?.lineup ?? []).length;
  }
  const { lineup, bench, ir } = data.me;
  return frame + 136 + ROW_H * (lineup.length + bench.length + ir.length) + LABEL_H * (1 + Number(bench.length > 0) + Number(ir.length > 0));
}

export async function shareImage(kind: ShareKind, data: Week, marks: Marks, art: Art, now: number): Promise<ImageResponse> {
  return new ImageResponse(
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", padding: MARGIN, background: FIELD, color: INK, fontFamily: "Barlow Condensed" }}>
      {kind === "matchup" ? <Matchup data={data} marks={marks} art={art} now={now} /> : <RosterImage data={data} mark={marks.me} art={art} now={now} />}
    </div>,
    { width: WIDTH, height: height(kind, data), fonts: await fonts, headers: { "cache-control": "no-store" } },
  );
}
