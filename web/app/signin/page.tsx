import { AuthShell, PasswordForm } from "@/components/password-form";
import { safeNext } from "@/lib/auth";

export const dynamic = "force-dynamic";

// Outside the app (app/layout.tsx renders it bare): the wordmark and the username + password form, which comes back to
// `next` signed in. A server nobody has set up yet never shows it (web/proxy.ts sends the visitor to /setup).
export default async function Page({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const next = safeNext((await searchParams).next);
  return (
    <AuthShell lede="Sign in to open the season.">
      <PasswordForm mode="signin" next={next} />
    </AuthShell>
  );
}
