import Link from "next/link";
import { notFound } from "next/navigation";
import { ErrorCard } from "@/components/error-card";
import { PlayerNewsView } from "@/components/news-feed";
import { apiGet, type PlayerNews } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseId } from "@/lib/news";

export const dynamic = "force-dynamic";

const DAYS = 30;

// One player's news: every story that named him in the last 30 days, his line in each.
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const id = parseId((await params).id);
  if (id == null) notFound();
  let d: PlayerNews;
  try {
    d = await apiGet<PlayerNews>(`/news/player/${id}?days=${DAYS}`);
  } catch (e) {
    return (
      <div className="space-y-3">
        <ErrorCard {...errorFields(e)} />
        <Link href="/news" className="block text-sm text-muted-foreground underline hover:text-foreground">All news</Link>
      </div>
    );
  }
  return <PlayerNewsView data={d} days={DAYS} />;
}
