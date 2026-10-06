"use client";

import { LoaderCircle, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { waiverHref } from "@/lib/waiver-search";

const DEBOUNCE_MS = 250;

// The name search over every position's pool. The query is server state in `?q=`, like the
// position: typing settles for a beat, then replaces the URL (no history entry per key) and the
// page re-renders from it. `pos` rides along so clearing the search returns to the tab it left.
// `/` focuses the field from anywhere on the page that is not itself a field; Esc clears it.
export function WaiverSearch({ q, pos }: { q: string; pos: string }) {
  const router = useRouter();
  const [value, setValue] = useState(q);
  const [pending, startTransition] = useTransition();
  const input = useRef<HTMLInputElement>(null);

  // a tab tapped while searching drops `q` from the URL: the field empties with it. Only a clear
  // is followed — our own replace lands a beat after the keys, and copying it back into the
  // field would eat what was typed since. (Set in render, React's pattern for state off a prop.)
  const [seen, setSeen] = useState(q);
  if (q !== seen) {
    setSeen(q);
    if (!q) setValue("");
  }

  useEffect(() => {
    if (value.trim() === q.trim()) return;
    const t = setTimeout(() => startTransition(() => router.replace(waiverHref(value, pos), { scroll: false })), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [value, q, pos, router]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el?.closest("input, textarea, select, [contenteditable=true]")) return;
      e.preventDefault();
      input.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const Icon = pending ? LoaderCircle : Search;
  return (
    <InputGroup className="h-10 rounded-md border-foreground/15 bg-card sm:h-8 sm:w-64 dark:bg-card">
      <InputGroupAddon>
        <Icon aria-hidden className={`size-4 ${pending ? "animate-spin" : ""}`} />
      </InputGroupAddon>
      <InputGroupInput
        ref={input}
        type="search"
        inputMode="search"
        enterKeyHint="search"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        aria-label="Search free agents at every position by name or team"
        placeholder="Name or team"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setValue("");
            e.currentTarget.blur();
          }
        }}
        className="[&::-webkit-search-cancel-button]:hidden"
      />
      <InputGroupAddon align="inline-end">
        {value ? (
          <InputGroupButton size="icon-xs" aria-label="Clear the search" onClick={() => { setValue(""); input.current?.focus(); }}>
            <X aria-hidden />
          </InputGroupButton>
        ) : (
          <kbd aria-hidden className="hidden rounded border border-foreground/15 px-1.5 font-mono text-[0.6875rem] leading-5 text-muted-foreground md:inline">/</kbd>
        )}
      </InputGroupAddon>
    </InputGroup>
  );
}
