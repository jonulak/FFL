import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ErrorCard({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <Card className="ring-destructive/50">
      <CardHeader>
        <CardTitle>Could not load</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="font-mono break-all">{msg}</p>
        <a className="underline" href="">Retry</a>
      </CardContent>
    </Card>
  );
}
