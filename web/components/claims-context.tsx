"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ClientError, clientGet, clientPost } from "@/app/api-client";
import type { Claim, ClaimGroup, ClaimPlayer, Claims, PoolStatus, SubmitResult, FreeAgentRow } from "@/lib/api";
import { submitFailure, type SubmitFailure } from "@/lib/claims";

export type { SubmitFailure };

// The tab's rows as the panel needs them, so a target added from this tab has a name before the save returns.
export type TabPlayer = Pick<FreeAgentRow, "player_id" | "name" | "pos" | "team" | "add" | "ros_adj" | "status" | "waiver_date">;

type Ctx = {
  data: Claims;
  groups: ClaimGroup[]; // the local plan: ahead of `data.groups` while a save is in flight
  dirty: boolean; // local edits the server has not answered yet (its errors and counts are for the old plan)
  pos: Map<number, string>;
  status: Record<string, PoolStatus>;
  player: (id: number) => ClaimPlayer | undefined;
  setGroups: (g: ClaimGroup[]) => void;
  saving: boolean;
  error: string | null;
  submitting: boolean;
  // expectNow: the "Add now" list the dialog showed; the server refuses (409) when its fresh read differs
  submit: (replaceOutside: boolean, expectNow: Claim[]) => Promise<SubmitResult>;
  // fresh: statuses re-read from ESPN (the Submit dialog), not the cached pool's
  refresh: (fresh?: boolean) => Promise<void>;
};

const Context = createContext<Ctx | null>(null);

const DEBOUNCE_MS = 400;
const RETRY_MS = 2_000;
// a submit cancels and reposts ESPN's whole list, then reads it back: give it a minute
const SUBMIT_MS = 60_000;

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

const failure = (e: unknown): SubmitFailure =>
  e instanceof ClientError ? submitFailure(e.status, e.body, e.message) : submitFailure(null, null, message(e));

export class SubmitError extends Error {
  constructor(public failure: SubmitFailure) {
    super(failure.kind === "invalid" ? failure.errors.map((x) => x.reason).join("; ") : failure.kind === "running" ? "a submit is already running" : failure.error);
  }
}

// The week's claim plan, shared by the table's row control, the docked panel and the Submit dialog.
// An edit lands locally at once and is saved 400 ms after the last one; the server's answer is the
// truth for the expansion, the errors and the submit state. A save refused because a submit is
// running (409) keeps the local plan and goes again once the submit is over.
export function ClaimsProvider({ initial, players, children }: { initial: Claims; players: TabPlayer[]; children: ReactNode }) {
  const [data, setData] = useState(initial);
  const [groups, setLocal] = useState(initial.groups);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const seq = useRef(0); // the newest save; an older answer is not applied over newer edits
  const latest = useRef(initial.groups);
  const submittingRef = useRef(false);
  const deferred = useRef(false); // a save waiting on a submit
  const retry = useRef<() => void>(() => {});

  // Every player id the panel can name: the tab's rows first, the server's lookup over them
  // (it has the roster and every target saved in any tab).
  const lookup = useMemo(() => {
    const m = new Map<number, ClaimPlayer>();
    for (const r of players)
      m.set(r.player_id, { name: r.name, pos: r.pos, team: r.team, add: r.add, ros_adj: r.ros_adj, slot: null, status: r.status, waiver_date: r.waiver_date, droppable: null, locked: null });
    for (const [k, v] of Object.entries(data.players)) m.set(Number(k), v);
    return m;
  }, [players, data.players]);
  const pos = useMemo(() => new Map([...lookup].map(([k, v]) => [k, v.pos])), [lookup]);
  const status = useMemo(() => {
    const s: Record<string, PoolStatus> = {};
    for (const r of players) if (r.status) s[String(r.player_id)] = r.status;
    return { ...s, ...data.status };
  }, [players, data.status]);
  const player = useCallback((id: number) => lookup.get(id), [lookup]);

  const save = useCallback(async (g: ClaimGroup[]): Promise<boolean> => {
    const mine = ++seq.current;
    setSaving(true);
    try {
      const next = await clientPost<Claims>("/claims", { groups: g });
      if (mine !== seq.current) return false;
      deferred.current = false;
      setData(next);
      setLocal(next.groups);
      latest.current = next.groups;
      setDirty(false);
      setError(null);
      return true;
    } catch (e) {
      if (mine !== seq.current) return false;
      if (e instanceof ClientError && e.status === 409) {
        // a submit holds the plan file: keep the edits, go again after it
        deferred.current = true;
        setError("saving after the submit…");
        if (!submittingRef.current) timer.current = setTimeout(() => retry.current(), RETRY_MS);
        return false;
      }
      setError(`not saved: ${message(e)}`);
      return false;
    } finally {
      if (mine === seq.current) setSaving(false);
    }
  }, []);

  // the 409 retry calls save through a ref (a callback cannot name itself in its own body)
  useEffect(() => { retry.current = () => void save(latest.current); }, [save]);

  const setGroups = useCallback((g: ClaimGroup[]) => {
    if (submittingRef.current) return; // editing is off while Send runs
    setLocal(g);
    latest.current = g;
    setDirty(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(g), DEBOUNCE_MS);
  }, [save]);

  const refresh = useCallback(async (fresh = false) => {
    try {
      const next = await clientGet<Claims>(fresh ? "/claims?fresh=1" : "/claims");
      setData(next);
      if (!deferred.current) {
        setLocal(next.groups);
        latest.current = next.groups;
        setDirty(false);
      }
    } catch (e) {
      setError(`couldn't reload the plan: ${message(e)}`);
    }
  }, []);

  const submit = useCallback(async (replaceOutside: boolean, expectNow: Claim[]): Promise<SubmitResult> => {
    // the submit reads the saved plan: save the latest edits first
    clearTimeout(timer.current);
    if ((dirty || deferred.current) && !(await save(latest.current)))
      throw new SubmitError(deferred.current ? { kind: "running" } : { kind: "other", error: "the latest edits aren't saved, so nothing was sent" });
    submittingRef.current = true;
    setSubmitting(true);
    try {
      const expect_now = expectNow.map(({ group, add, drop }) => ({ group, add, drop }));
      return await clientPost<SubmitResult>("/claims/submit", { replace_outside: replaceOutside, expect_now }, SUBMIT_MS);
    } catch (e) {
      throw new SubmitError(failure(e));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      if (deferred.current) await save(latest.current);
      else await refresh();
    }
  }, [dirty, save, refresh]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const value = { data, groups, dirty, pos, status, player, setGroups, saving, error, submitting, submit, refresh };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useClaims(): Ctx {
  const c = useContext(Context);
  if (!c) throw new Error("useClaims outside ClaimsProvider");
  return c;
}

// The row control renders on pages without the provider too (a /claims failure): it then shows nothing.
export function useClaimsMaybe(): Ctx | null {
  return useContext(Context);
}
