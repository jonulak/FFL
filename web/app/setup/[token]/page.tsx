import { AuthShell, PasswordForm } from "@/components/password-form";

export const dynamic = "force-dynamic";

// An invite's or a reset's setup link (bare, signed out; an admin made it in Settings › Users): a username and password,
// then signed in. Nothing is read first: the token is checked when the form is sent (an old link says so there).
export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return (
    <AuthShell lede="Choose your username and password to sign in to FFL.">
      <PasswordForm mode="invite" token={token} />
    </AuthShell>
  );
}
