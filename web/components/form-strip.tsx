import type { Form } from "@/lib/api";
import { formItems } from "@/lib/format";

// His last three games as one line of label/value pairs (STRATEGY.md "Recent form"): the
// Roster card's register for a row of facts, and the Waiver row's caption on a phone. Each
// pair stays on one line; the line wraps between pairs. Nothing here is a verdict: `pace` is
// the one number that looks like one, and it is only the window's pace held against ROS.
export function FormStrip({ form, className = "" }: { form: Form | null | undefined; className?: string }) {
  if (!form) return null;
  return (
    <p className={`flex flex-wrap gap-x-2 gap-y-0.5 text-xs tabular-nums text-muted-foreground ${className}`}>
      {formItems(form).map((i, n) => (
        <span key={i.label} className="whitespace-nowrap" title={i.title}>
          {n > 0 && <span aria-hidden className="mr-2">·</span>}
          <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em]">{i.label}</span>
          {" "}
          <span className="font-medium text-foreground/85">{i.value}</span>
        </span>
      ))}
    </p>
  );
}
