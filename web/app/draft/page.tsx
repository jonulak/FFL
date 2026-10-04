import { ErrorCard } from "@/components/error-card";
import { Markdown } from "@/components/markdown";
import { PlayerTrigger } from "@/components/player-dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiGet, type Draft } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { NAME_LINK } from "@/lib/player-log";

export const dynamic = "force-dynamic";

const COLS = [
  "overall",
  "round",
  "pick",
  "team",
  "player",
  "pos",
  "nfl",
  "adp_2qb",
  "board_rank_at_pick",
  "proj",
];

export default async function Page() {
  let d: Draft;
  try {
    d = await apiGet<Draft>("/draft");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Draft 2026</h1>
      <Tabs defaultValue="recap">
        <TabsList>
          <TabsTrigger value="recap">Recap</TabsTrigger>
          <TabsTrigger value="picks">Picks ({d.picks.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="recap">
          <Markdown text={d.recap_md} />
        </TabsContent>
        <TabsContent value="picks">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {COLS.map((c) => (
                    <TableHead key={c}>{c}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.picks.map((r) => (
                  <TableRow key={r.overall}>
                    {COLS.map((c) => (
                      <TableCell key={c} className="whitespace-nowrap">
                        {c === "player" && r.player_id ? (
                          <PlayerTrigger p={{ player_id: Number(r.player_id), name: r.player, pos: r.pos, team: r.nfl }} className={`rounded-sm ${NAME_LINK}`}>
                            {r.player}
                          </PlayerTrigger>
                        ) : r[c]}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
