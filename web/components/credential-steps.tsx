"use client";

import { CircleHelp, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { PHONE_ACTION } from "./claims-submit";

// Every credential a user pastes into FFL (a key, a token, a cookie) gets this: a "How to get…" button beside its field
// that opens the full walkthrough in a dialog, so the card stays short and the steps can be as long as they need.
export function CredentialSteps({ label, title, description, footnote, children }: {
  label: string; title: string; description: string; footnote?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <Dialog>
      <DialogTrigger render={<Button type="button" variant="outline" className={PHONE_ACTION} />}>
        <CircleHelp aria-hidden />{label}
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <ol className="list-decimal space-y-3 pl-5 text-sm leading-relaxed text-muted-foreground marker:text-foreground/50">
          {children}
        </ol>
        {footnote && <p className="text-sm text-muted-foreground">{footnote}</p>}
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

// A link out of FFL (opens a new tab).
export function Out({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-foreground underline underline-offset-2">
      {children}<ExternalLink aria-hidden className="size-3.5" />
    </a>
  );
}

// A button or menu name on the other site's screen.
export function Ui({ children }: { children: React.ReactNode }) {
  return <span className="font-medium text-foreground">{children}</span>;
}

// Something the user types or copies verbatim.
export function Code({ children }: { children: React.ReactNode }) {
  return <code className="font-mono text-[0.8125rem] text-foreground">{children}</code>;
}
