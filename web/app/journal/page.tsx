import { DecisionForm } from "@/components/decision-form";
import { ErrorCard } from "@/components/error-card";
import { Markdown } from "@/components/markdown";
import { apiGet, type Journal } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// The decisions log. The league's transactions have their own page (/transactions).
export default async function Page() {
  let d: Journal;
  try {
    d = await apiGet<Journal>("/journal");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Journal</h1>
        <DecisionForm />
      </div>
      <Markdown text={d.decisions_md} />
    </div>
  );
}
