import { Skeleton } from "@/components/ui/skeleton";

// Shown by Next while a page's server component waits on the API. With the API serving stale
// and rebuilding behind the request that wait is milliseconds; this frame is for the remaining
// cold hits (an API restart, the first build of a new week) so the nav and a page shape stay on
// screen instead of a blank body.
export default function Loading() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading">
      <div className="flex items-end gap-3">
        <Skeleton className="h-9 w-40" />
        <Skeleton className="h-6 w-20" />
      </div>
      <div className="space-y-2">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    </div>
  );
}
