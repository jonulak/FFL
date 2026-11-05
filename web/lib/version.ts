// GET /app/version (admins): the commit this install runs against the `release` tag.
export type AppVersion = { commit: string | null; latest: string | null; behind: boolean; pinned: string | null };

const short = (commit: string) => commit.slice(0, 7);

export function versionLine(v: AppVersion): string {
  if (v.pinned) return `Pinned to ${v.pinned} by FFL_VERSION in .env.`;
  if (!v.commit) return "A development build, with no release to compare it to.";
  if (v.behind) return `A new version is out. This install runs ${short(v.commit)}; the latest is ${short(v.latest!)}.`;
  if (!v.latest) return `Running ${short(v.commit)}. GitHub couldn't be reached, so there's no word on a newer version.`;
  return `Up to date: ${short(v.commit)}.`;
}
