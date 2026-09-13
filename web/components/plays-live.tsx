"use client";

import type { Plays } from "@/lib/api";
import { PlayFeed } from "./play-feed";
import { useLive } from "./use-live";

export function PlaysLive({ initial }: { initial: Plays }) {
  const { data } = useLive(initial, "/plays");
  return <PlayFeed plays={data.plays} full />;
}
