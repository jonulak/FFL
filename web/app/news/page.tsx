import { ErrorCard } from "@/components/error-card";
import { NewsFeed } from "@/components/news-feed";
import { apiGet, type NewsPage } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseId, parseNewsFilter } from "@/lib/news";

export const dynamic = "force-dynamic";

// The news desk's wire: every written story, newest update first, its players tagged with who holds them now.
// `?filter=` narrows it to mine, free agents or other rosters; `?quiet=1` shows the stories that move nobody;
// `?story=` is a push's target.
export default async function Page({ searchParams }: { searchParams: Promise<{ filter?: string | string[]; story?: string | string[]; quiet?: string | string[] }> }) {
  const sp = await searchParams;
  const filter = parseNewsFilter(sp.filter);
  let d: NewsPage;
  try {
    d = await apiGet<NewsPage>(`/news?filter=${filter}`);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <NewsFeed data={d} filter={filter} target={parseId(sp.story)} quiet={sp.quiet === "1"} />;
}
