import { TriangleAlert } from "lucide-react";
import Link from "@/components/league-link";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { needsReconnect, type EspnStatus } from "@/lib/espn";

// Reconnect ESPN (#134): over every page while one of the user's ESPN logins is refused (or this server can't open it),
// since its leagues stop updating. Settings › ESPN connection says which login and how to fix it.
export function ReconnectBanner({ status }: { status: EspnStatus | null }) {
  if (!needsReconnect(status)) return null;
  return (
    <Alert role="status" className="mb-4 border-destructive/40 bg-destructive/10">
      <TriangleAlert aria-hidden className="text-destructive" />
      <AlertTitle>ESPN stopped taking your login</AlertTitle>
      <AlertDescription>Leagues on it won&apos;t update until you sign in to ESPN again.</AlertDescription>
      <AlertAction>
        <Link href="/settings#espn" className={buttonVariants({ variant: "outline", size: "sm" })}>Reconnect</Link>
      </AlertAction>
    </Alert>
  );
}
