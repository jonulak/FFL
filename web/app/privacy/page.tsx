import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacy · FFL" };

// The date the text below last changed: move it with every edit to the policy.
const UPDATED = "31 October 2026";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="font-display text-[1.4rem] font-semibold leading-tight tracking-tight text-balance">{title}</h2>
      {children}
    </section>
  );
}

// Outside the app (app/layout.tsx renders it bare) and open signed out (lib/auth.ts): the extension stores link here.
// Every claim is what the code does today; a change to what FFL stores or sends changes this page in the same PR.
export default function Page() {
  return (
    <main className="mx-auto w-full max-w-2xl px-4 pt-12 pb-[max(3rem,calc(env(safe-area-inset-bottom)+1.5rem))]">
      <header className="space-y-2 pb-8">
        <h1 className="font-display text-[2.6rem] font-bold leading-none tracking-tight">FFL Privacy</h1>
        <p className="text-sm text-muted-foreground">Updated {UPDATED}</p>
      </header>
      <div className="space-y-9 text-[0.9375rem] leading-relaxed [&_a]:underline [&_a]:underline-offset-4 [&_li]:pl-1 [&_p]:max-w-[68ch] [&_ul]:max-w-[68ch] [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5">
        <p>
          FFL is a self-hosted fantasy football companion for ESPN leagues. This install is run by its operator, the
          person who set it up, on a server they control. This page says what FFL and its browser extension collect,
          what they do with it, and how to have it deleted.
        </p>

        <Section title="What the extension reads">
          <ul>
            <li>
              Two cookies from espn.com, <code className="font-mono text-[0.875em]">espn_s2</code> and{" "}
              <code className="font-mono text-[0.875em]">SWID</code>, which hold your ESPN sign-in. Nothing else: no
              other cookies, no other sites, no browsing history, no page content.
            </li>
            <li>
              It sends them only to FFL&apos;s own server, over HTTPS, after you connect it to your FFL account, and
              again when your ESPN sign-in changes.
            </li>
            <li>
              On your device it keeps a token linking it to your FFL account and a one-way fingerprint of the last
              sign-in it sent, so it does not send the same one twice. It never stores the cookies themselves.
            </li>
            <li>It also runs on FFL&apos;s own site, only so the site can tell that the extension is installed.</li>
          </ul>
        </Section>

        <Section title="What FFL keeps">
          <ul>
            <li>Your account: your username, the email and name on your account if any, and your sign-ins (when, and the browser&apos;s name) so you can review and end them.</li>
            <li>Your ESPN sign-in (the two cookies above), whether the extension sent it or you pasted it.</li>
            <li>Your leagues and team, and what you do in FFL: lineup moves, waiver claims, the reasons you record for them, players you watch, and your settings.</li>
            <li>If you turn on notifications, your device&apos;s push address.</li>
          </ul>
        </Section>

        <Section title="What it is used for">
          <p>
            FFL uses your ESPN sign-in to read your leagues from ESPN and to make the moves you ask for (a lineup
            change, a claim). Nothing else. Everything else above is kept to show you your own pages and history.
          </p>
        </Section>

        <Section title="How it is kept">
          <p>
            Your ESPN sign-in and push address are encrypted in the database, each under your own account, with a key
            kept outside the database. They are never written to logs. Everything travels over HTTPS when the operator
            serves FFL over HTTPS. FFL runs on the operator&apos;s own server.
          </p>
        </Section>

        <Section title="Who else sees it">
          <p>FFL does not sell or rent your data, shows no ads, and uses no analytics or tracking. Your data reaches others only where FFL needs them to work:</p>
          <ul>
            <li>ESPN, which receives the requests FFL makes with your sign-in.</li>
            <li>Your browser&apos;s push service (Apple, Google or Mozilla), which delivers the notifications you turned on.</li>
            <li>The operator, who runs the server, the database and its backups.</li>
          </ul>
          <p>Player photos and team logos load from ESPN&apos;s image servers.</p>
        </Section>

        <Section title="How long, and how to delete it">
          <ul>
            <li>
              <strong className="font-semibold">Disconnect ESPN</strong> (Settings › ESPN connection) deletes your ESPN
              sign-in at once and unlinks the extension.
            </li>
            <li>
              <strong className="font-semibold">Delete your account</strong> (Settings › Account) deletes your account,
              your ESPN sign-in and everything personal listed above.
            </li>
            <li>Removing the extension deletes what it kept on your device.</li>
            <li>Deleted data leaves FFL&apos;s backups within 14 days.</li>
          </ul>
        </Section>

        <Section title="Children">
          <p>FFL is not meant for children under 13 and does not knowingly collect their data.</p>
        </Section>

        <Section title="Changes">
          <p>If this policy changes, the date at the top changes with it.</p>
        </Section>

        <Section title="Contact">
          <p>
            Questions, or a request to see or delete your data: ask the operator of this install.
          </p>
        </Section>
      </div>
    </main>
  );
}
