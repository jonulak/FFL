import { ErrorCard } from "@/components/error-card";
import { StaleBadge } from "@/components/stale-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiGet, type League } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { pts } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Page() {
  let d: League;
  try {
    d = await apiGet<League>("/league");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">League · week {d.week}</h1>
        <StaleBadge since={d.stale_since} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Standings</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <TableHead>Team</TableHead>
                    <TableHead>W-L</TableHead>
                    <TableHead className="text-right">PF</TableHead>
                    <TableHead className="text-right">PA</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.standings.map((t) => (
                    <TableRow key={t.team_id} className={t.me ? "font-semibold" : ""}>
                      <TableCell className="tabular-nums">{t.rank}</TableCell>
                      <TableCell>{t.team}</TableCell>
                      <TableCell className="tabular-nums whitespace-nowrap">
                        {t.wins}-{t.losses}
                        {t.ties ? `-${t.ties}` : ""}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{pts(t.points_for)}</TableCell>
                      <TableCell className="text-right tabular-nums">{pts(t.points_against)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Scoreboard</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              {d.scoreboard.map((m, i) => (
                <div key={i} className="flex justify-between gap-2 tabular-nums">
                  <span>
                    {m.away} @ {m.home}
                  </span>
                  <span className="whitespace-nowrap">
                    {pts(m.away_score)} – {pts(m.home_score)}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Power rankings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              {d.power.map((p, i) => (
                <div key={p.team} className="flex justify-between gap-2">
                  <span>
                    {i + 1}. {p.team}
                  </span>
                  <span className="tabular-nums">{pts(p.score)}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
