import { ErrorCard } from "@/components/error-card";
import { NewsFeed } from "@/components/news-feed";
import { apiGet, type NewsPage, type Story } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseId, parseNewsFilter } from "@/lib/news";

export const dynamic = "force-dynamic";

// The news desk's wire: every written story that moves somebody, newest update first, its players tagged with who
// holds them now. `?filter=` narrows it to mine, free agents or other rosters; `?story=` is a push's target, read on
// its own so it shows whatever the feed leaves out.
export default async function Page({ searchParams }: { searchParams: Promise<{ filter?: string | string[]; story?: string | string[] }> }) {
  const sp = await searchParams;
  const filter = parseNewsFilter(sp.filter);
  const target = parseId(sp.story);
  let d: NewsPage;
  let pinned: Story | null = null;
  try {
    // a story gone (404) or unreadable is no pin: the page says the story is no longer in the feed
    [d, pinned] = await Promise.all([
      apiGet<NewsPage>(`/news?filter=${filter}`),
      target == null ? null : apiGet<Story>(`/news/story/${target}`).catch(() => null),
    ]);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <NewsFeed data={d} filter={filter} target={target} pinned={pinned} />;
}
