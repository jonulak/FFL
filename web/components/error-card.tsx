import { TriangleAlert } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";

// Plain serialisable props only: an Error object passed through JSX props can carry a
// `cause` chain (AggregateError from a refused fetch) that React Flight cannot serialise.
export function ErrorCard({ status, source, message }: { status: number; source: string; message: string }) {
  return (
    <Alert variant="destructive" className="log-in border-destructive/40 bg-destructive/10 px-4 py-3">
      <TriangleAlert aria-hidden />
      <AlertTitle>Could not load</AlertTitle>
      <AlertDescription className="space-y-1.5 text-wrap [&_p:not(:last-child)]:mb-0 [&_a]:no-underline">
        <p className="text-xs text-muted-foreground">{source} · status {status}</p>
        <p className="font-mono break-all">{message}</p>
        {/* a plain reload of the page: an anchor with an empty href, styled as the outline button */}
        <a href="" className={buttonVariants({ variant: "outline", size: "sm", className: "mt-1 text-foreground" })}>Retry</a>
      </AlertDescription>
    </Alert>
  );
}
