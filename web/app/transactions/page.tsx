import { ErrorCard } from "@/components/error-card";
import { TransactionsView } from "@/components/transactions";
import { apiGet, type Transactions } from "@/lib/api";
import { errorFields } from "@/lib/errors";

export const dynamic = "force-dynamic";

// The league's adds, drops and trades, newest first: an add beside the drop it made room with.
export default async function Page() {
  let d: Transactions;
  try {
    d = await apiGet<Transactions>("/transactions");
  } catch (e) {
    return <ErrorCard {...errorFields(e)} />;
  }
  return <TransactionsView data={d} />;
}
