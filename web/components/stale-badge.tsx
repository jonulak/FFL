import { Badge } from "@/components/ui/badge";

export function StaleBadge({ since }: { since: string | null }) {
  return since ? <Badge variant="destructive">stale since {since}</Badge> : null;
}
