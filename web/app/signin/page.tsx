import { TriangleAlert } from "lucide-react";
import { DevSignIn } from "@/components/dev-signin";
import { ErrorCard } from "@/components/error-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { FieldSeparator } from "@/components/ui/field";
import { apiGet } from "@/lib/api";
import { safeNext } from "@/lib/auth";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

type Providers = { google: boolean; apple: boolean; dev: boolean };

// The two ways back from Google's round trip that did not sign in (ffl/auth.py's callback).
const ERRORS: Record<string, string> = {
  closed: "Sign-up isn't open yet.",
  failed: "Google sign-in didn't finish. Try again.",
};

// Outside the app (app/layout.tsx renders it bare): the wordmark, why sign-in failed if it did, and the ways in. Google
// is a plain link, a full page load to the API's /auth/google/start, which comes back to `next` signed in.
export default async function Page({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const sp = await searchParams;
  const next = safeNext(sp.next);
  const error = sp.error ? (ERRORS[sp.error] ?? null) : null;
  let providers: Providers;
  try {
    providers = await apiGet<Providers>("/auth/providers");
  } catch (e) {
    return <main className="mx-auto max-w-sm px-4 py-12"><ErrorCard {...errorFields(e)} /></main>;
  }
  const none = !providers.google && !providers.dev;
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-sm flex-col justify-center gap-8 px-4 pt-12 pb-[max(1.75rem,calc(env(safe-area-inset-bottom)+0.75rem))]">
      <div className="space-y-2">
        <h1 className="font-display text-[2.6rem] font-bold leading-none tracking-tight">FFL</h1>
        <p className="text-sm text-muted-foreground">Sign in to open the season.</p>
      </div>
      {error && (
        <Alert variant="destructive" className="border-destructive/40 bg-destructive/10 px-4 py-3">
          <TriangleAlert aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-col gap-6">
        {providers.google && (
          <a
            href={`/api/auth/google/start?next=${encodeURIComponent(next)}`}
            className={buttonVariants({ size: "lg", className: "h-12 w-full text-base" })}
          >
            Continue with Google
          </a>
        )}
        {providers.google && providers.dev && <FieldSeparator>or</FieldSeparator>}
        {providers.dev && <DevSignIn next={next} />}
        {none && <p className="text-sm text-muted-foreground">No sign-in is set up on this server.</p>}
      </div>
    </main>
  );
}
