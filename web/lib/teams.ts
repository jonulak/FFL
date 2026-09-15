// The 32 NFL teams keyed by ESPN's abbreviation (the one the API's `team` and `opponent` carry:
// WSH not WAS, JAX not JAC). Colors are each club's primary and secondary brand hexes; the
// card panel blends them, no text is ever set on them, so contrast is not a concern here.
export type Team = { name: string; primary: string; secondary: string };

export const TEAMS: Record<string, Team> = {
  ARI: { name: "Cardinals", primary: "#97233F", secondary: "#000000" },
  ATL: { name: "Falcons", primary: "#A71930", secondary: "#000000" },
  BAL: { name: "Ravens", primary: "#241773", secondary: "#9E7C0C" },
  BUF: { name: "Bills", primary: "#00338D", secondary: "#C60C30" },
  CAR: { name: "Panthers", primary: "#0085CA", secondary: "#101820" },
  CHI: { name: "Bears", primary: "#0B162A", secondary: "#C83803" },
  CIN: { name: "Bengals", primary: "#FB4F14", secondary: "#000000" },
  CLE: { name: "Browns", primary: "#311D00", secondary: "#FF3C00" },
  DAL: { name: "Cowboys", primary: "#003594", secondary: "#869397" },
  DEN: { name: "Broncos", primary: "#FB4F14", secondary: "#002244" },
  DET: { name: "Lions", primary: "#0076B6", secondary: "#B0B7BC" },
  GB: { name: "Packers", primary: "#203731", secondary: "#FFB612" },
  HOU: { name: "Texans", primary: "#03202F", secondary: "#A71930" },
  IND: { name: "Colts", primary: "#002C5F", secondary: "#A2AAAD" },
  JAX: { name: "Jaguars", primary: "#006778", secondary: "#D7A22A" },
  KC: { name: "Chiefs", primary: "#E31837", secondary: "#FFB81C" },
  LV: { name: "Raiders", primary: "#000000", secondary: "#A5ACAF" },
  LAR: { name: "Rams", primary: "#003594", secondary: "#FFA300" },
  LAC: { name: "Chargers", primary: "#0080C6", secondary: "#FFC20E" },
  MIA: { name: "Dolphins", primary: "#008E97", secondary: "#FC4C02" },
  MIN: { name: "Vikings", primary: "#4F2683", secondary: "#FFC62F" },
  NE: { name: "Patriots", primary: "#002244", secondary: "#C60C30" },
  NO: { name: "Saints", primary: "#D3BC8D", secondary: "#101820" },
  NYG: { name: "Giants", primary: "#0B2265", secondary: "#A71930" },
  NYJ: { name: "Jets", primary: "#125740", secondary: "#000000" },
  PHI: { name: "Eagles", primary: "#004C54", secondary: "#A5ACAF" },
  PIT: { name: "Steelers", primary: "#FFB612", secondary: "#101820" },
  SF: { name: "49ers", primary: "#AA0000", secondary: "#B3995D" },
  SEA: { name: "Seahawks", primary: "#002244", secondary: "#69BE28" },
  TB: { name: "Buccaneers", primary: "#D50A0A", secondary: "#FF7900" },
  TEN: { name: "Titans", primary: "#0C2340", secondary: "#4B92DB" },
  WSH: { name: "Commanders", primary: "#5A1414", secondary: "#FFB612" },
};

const FALLBACK: Team = { name: "", primary: "#3f3f46", secondary: "#18181b" };

export function team(abbr: string | null | undefined): Team {
  return (abbr && TEAMS[abbr]) || FALLBACK;
}

// ESPN's public image CDN. `500-dark` is the same mark re-cut for dark grounds (navy and black
// logos get a light keyline); not every team differs, but every team has the file.
const CDN = "https://a.espncdn.com/i";

export function logoUrl(abbr: string, variant: "light" | "dark" = "light"): string {
  return `${CDN}/teamlogos/nfl/${variant === "dark" ? "500-dark" : "500"}/${abbr.toLowerCase()}.png`;
}

// Transparent-background portrait, 600x436, cut at the chest. D/ST entries have negative ids and
// no portrait: callers show the team logo instead.
export function headshotUrl(playerId: number): string | null {
  return playerId > 0 ? `${CDN}/headshots/nfl/players/full/${playerId}.png` : null;
}

// A fantasy club's mark. ESPN serves an owner's own upload only with the league cookies, so those
// go through the API (/logo/{team_id}, via the /api rewrite); ESPN's stock marks load directly.
export function clubLogoSrc(c: { logo: string; team_id: number }): string {
  return c.logo.includes("mystique-api.fantasy.espn.com") ? `/api/logo/${c.team_id}` : c.logo;
}
