// The Waivers URL for a search typed with a position tab open: the trimmed query and the tab,
// or the tab alone when the query is blank.
export function waiverHref(q: string, pos: string): string {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  params.set("pos", pos);
  return `/waivers?${params}`;
}
