import { ErrorCard } from "@/components/error-card";
import { PlaysLive } from "@/components/plays-live";
import { Badge } from "@/components/ui/badge";
import { apiGet, type Plays } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseTeam } from "@/lib/matchup-link";

export const dynamic = "force-dynamic";

// Every play-feed row of the week for one game, newest first: mine, or with `?team=` the game of
// that team (a League board's strip links its left team). The card's own heading line leads back.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const team = parseTeam((await searchParams).team);
  const path = team == null ? "/plays" : `/plays?team=${team}`;
  let data: Plays;
  try {
    data = await apiGet<Plays>(path);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
            Plays
            {data.week != null && <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>}
          </h1>
          {data.live && <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge>}
        </div>
        {/* another game's plays say whose they are; my own page needs no caption */}
        {!data.mine && data.me && (
          <p className="font-display text-lg font-semibold leading-tight tracking-tight text-foreground/80">
            {data.me.team} <span className="font-medium uppercase tracking-[0.14em] text-muted-foreground text-sm">vs</span> {data.opp?.team ?? "bye"}
          </p>
        )}
      </div>
      <PlaysLive initial={data} path={path} />
    </div>
  );
}
