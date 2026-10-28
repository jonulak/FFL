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

// The two ways back from a provider's round trip that did not sign in (ffl/auth.py's callback).
const ERRORS: Record<string, string> = {
  closed: "Sign-up isn't open yet.",
  failed: "Sign-in didn't finish. Try again.",
};

// Apple's mark, which its guidelines put on the button (lucide carries no brand marks).
function AppleMark() {
  return (
    <svg viewBox="0 0 814 1000" aria-hidden className="size-[1.05em] -translate-y-px fill-current">
      <path d="M788.1 340.9c-5.8 4.5-108.2 62.2-108.2 190.5 0 148.4 130.3 200.9 134.2 202.2-.6 3.2-20.7 71.9-68.7 141.9-42.8 61.6-87.5 123.1-155.5 123.1s-85.5-39.5-164-39.5c-76.5 0-103.7 40.8-165.9 40.8s-105.6-57-155.5-127C46.7 790.7 0 663 0 541.8c0-194.4 126.4-297.5 250.8-297.5 66.1 0 121.2 43.4 162.7 43.4 39.5 0 101.1-46 176.3-46 28.5 0 130.9 2.6 198.3 99.2zm-234-181.5c31.1-36.9 53.1-88.1 53.1-139.3 0-7.1-.6-14.3-1.9-20.1-50.6 1.9-110.8 33.7-147.1 75.8-28.5 32.4-55.1 83.6-55.1 135.5 0 7.8 1.3 15.6 1.9 18.1 3.2.6 8.4 1.3 13.6 1.3 45.4 0 102.5-30.4 135.5-71.3z" />
    </svg>
  );
}

// Outside the app (app/layout.tsx renders it bare): the wordmark, why sign-in failed if it did, and the ways in. Each
// provider is a plain link, a full page load to the API's /auth/<provider>/start, which comes back to `next` signed in.
// Apple's button is its guidelines' black (white in dark), which the primary button already is, at Google's size.
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
  const any = providers.google || providers.apple;
  const none = !any && !providers.dev;
  const button = buttonVariants({ size: "lg", className: "h-12 w-full gap-2 text-base" });
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
        {any && (
          <div className="flex flex-col gap-3">
            {providers.google && (
              <a href={`/api/auth/google/start?next=${encodeURIComponent(next)}`} className={button}>
                Continue with Google
              </a>
            )}
            {providers.apple && (
              <a href={`/api/auth/apple/start?next=${encodeURIComponent(next)}`} className={button}>
                <AppleMark />
                Continue with Apple
              </a>
            )}
          </div>
        )}
        {any && providers.dev && <FieldSeparator>or</FieldSeparator>}
        {providers.dev && <DevSignIn next={next} />}
        {none && <p className="text-sm text-muted-foreground">No sign-in is set up on this server.</p>}
      </div>
    </main>
  );
}
