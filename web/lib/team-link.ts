// Where a fantasy team's name goes: its roster list. Ids arrive as numbers off the API's payloads
// and as strings off the journal CSVs; anything that is not a positive integer (a
// transaction whose team has left the league) gets no link.
export function teamHref(id: number | string | null | undefined): string | null {
  const n = parseTeamParam(String(id ?? ""));
  return n == null ? null : `/team/${n}`;
}

// The route's segment back as a number; null when it is not a positive integer.
export function parseTeamParam(id: string): number | null {
  const n = Number(id);
  return id !== "" && Number.isInteger(n) && n > 0 ? n : null;
}
