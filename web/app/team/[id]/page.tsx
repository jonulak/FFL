import { notFound } from "next/navigation";
import { ErrorCard } from "@/components/error-card";
import { TeamRosterView } from "@/components/team-roster";
import { apiGet, type TeamRoster } from "@/lib/api";
import { errorFields } from "@/lib/errors";
import { parseTeamParam } from "@/lib/team-link";

export const dynamic = "force-dynamic";

// Any fantasy team's roster as a plain list: `/team/<team id>`, reached by tapping a team's name.
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const team = parseTeamParam(id);
  if (team == null) notFound();
  const path = `/team/${team}`;
  let data: TeamRoster;
  try {
    data = await apiGet<TeamRoster>(path);
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <TeamRosterView initial={data} path={path} />;
}
