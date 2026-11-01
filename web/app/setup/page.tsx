import { redirect } from "next/navigation";
import { ErrorCard } from "@/components/error-card";
import { AuthShell, PasswordForm } from "@/components/password-form";
import { apiGet } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// First-run setup (bare, signed out): while nobody has a password the first login made here is the admin's. Once set
// up the page is gone: to the start page, which sends a signed-out visitor on to sign in.
export default async function Page() {
  let needed: boolean;
  try {
    needed = (await apiGet<{ needs_setup: boolean }>("/setup/status")).needs_setup;
  } catch (e) {
    return <main className="mx-auto max-w-sm px-4 py-12"><ErrorCard {...errorFields(e)} /></main>;
  }
  if (!needed) redirect("/");
  return (
    <AuthShell title="Set up FFL" lede="Choose the admin's username and password. You invite everyone else from Settings.">
      <PasswordForm mode="setup" />
    </AuthShell>
  );
}
