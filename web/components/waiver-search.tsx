"use client";

import { Search, X } from "lucide-react";
import { Spinner as Spin } from "@/components/ui/spinner";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { Kbd } from "@/components/ui/kbd";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { usePlayerView } from "./player-view-context";

const DEBOUNCE_MS = 250;

// The name search over every player, rostered or free, at every position. The query is server state in `?q=`, like the
// position: typing settles for a beat, then replaces the URL (no history entry per key) and the
// page re-renders from it. The view rides along so clearing the search returns to the tab it left.
// `/` focuses the field from anywhere on the page that is not itself a field; Esc clears it.
export function WaiverSearch({ q }: { q: string }) {
  const router = useRouter();
  const { presetId, view, preset, playersHref } = usePlayerView();
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
    const t = setTimeout(() => startTransition(() => router.replace(playersHref(value, presetId, view, preset), { scroll: false })), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [value, q, presetId, view, preset, router, playersHref]);

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

  return (
    <InputGroup className="h-10 rounded-md border-foreground/15 bg-card sm:h-8 sm:w-64 dark:bg-card">
      <InputGroupAddon>
        {/* the glass turns into the spinner while the page is fetched: both in one cell, crossfading */}
        <span aria-hidden className="grid *:col-start-1 *:row-start-1 *:transition-[opacity,scale] *:duration-200 *:ease-(--ease-arrive)">
          <Search className={`size-4 ${pending ? "scale-75 opacity-0" : ""}`} />
          <Spin aria-hidden role="presentation" className={`size-4 ${pending ? "" : "scale-75 opacity-0 animate-none"}`} />
        </span>
      </InputGroupAddon>
      <InputGroupInput
        ref={input}
        type="search"
        inputMode="search"
        enterKeyHint="search"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        aria-label="Search every player by name or team"
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
          <InputGroupButton key="clear" size="icon-xs" aria-label="Clear the search" className="fx-in" onClick={() => { setValue(""); input.current?.focus(); }}>
            <X aria-hidden />
          </InputGroupButton>
        ) : (
          <Kbd key="hint" aria-hidden className="hidden border border-foreground/15 bg-transparent font-mono text-[0.6875rem] md:inline-flex">/</Kbd>
        )}
      </InputGroupAddon>
    </InputGroup>
  );
}
