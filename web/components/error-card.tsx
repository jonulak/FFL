import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Plain serialisable props only: an Error object passed through JSX props can carry a
// `cause` chain (AggregateError from a refused fetch) that React Flight cannot serialise.
export function ErrorCard({
  status,
  source,
  message,
}: {
  status: number;
  source: string;
  message: string;
}) {
  return (
    <Card className="log-in ring-destructive/50">
      <CardHeader>
        <CardTitle>Could not load</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-xs text-muted-foreground">
          {source} · status {status}
        </p>
        <p className="font-mono break-all">{message}</p>
        <a className="underline decoration-foreground/40 transition-[text-decoration-color] hover:decoration-foreground" href="">Retry</a>
      </CardContent>
    </Card>
  );
}
