import { DecisionForm } from "@/components/decision-form";
import { ErrorCard } from "@/components/error-card";
import { Markdown } from "@/components/markdown";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiGet, type Journal } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

const COLS = ["date", "type", "team", "player", "bid", "reasoning"];

export default async function Page() {
  let d: Journal;
  try {
    d = await apiGet<Journal>("/journal");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  const rows = [...d.transactions].reverse();
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Journal</h1>
        <DecisionForm />
      </div>
      <Tabs defaultValue="decisions">
        <TabsList>
          <TabsTrigger value="decisions">Decisions</TabsTrigger>
          <TabsTrigger value="tx">Transactions ({rows.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="decisions">
          <Markdown text={d.decisions_md} />
        </TabsContent>
        <TabsContent value="tx">
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
                {rows.map((r) => (
                  <TableRow key={r.id}>
                    {COLS.map((c) => (
                      <TableCell key={c} className={c === "reasoning" ? "" : "whitespace-nowrap"}>
                        {r[c]}
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
