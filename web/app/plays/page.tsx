import Link from "next/link";
import { ErrorCard } from "@/components/error-card";
import { PlaysLive } from "@/components/plays-live";
import { Badge } from "@/components/ui/badge";
import { apiGet, type Plays } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// Every play-feed row of the week, newest first; reached from the Matchup page's strip.
export default async function Page() {
  let data: Plays;
  try {
    data = await apiGet<Plays>("/plays");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Plays
          {data.week != null && <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>}
        </h1>
        {data.live && <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge>}
        <Link href="/" className="ml-auto text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
          <span aria-hidden>←</span> Matchup
        </Link>
      </div>
      <PlaysLive initial={data} />
    </div>
  );
}
