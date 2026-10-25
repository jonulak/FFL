"use client";

import { Settings } from "lucide-react";
import Link from "@/components/league-link";
import { usePathname } from "next/navigation";
import { buttonVariants } from "@/components/ui/button";
import { Hint } from "./hint";

// The gear at the nav's right edge: Settings is not a page tab. Full ink on its own page.
export function SettingsLink() {
  const active = usePathname().startsWith("/settings");
  return (
    <Hint text="Settings">
      <Link href="/settings" aria-label="Settings" aria-current={active ? "page" : undefined}
            className={buttonVariants({ variant: "ghost", size: "icon-sm", className: "max-sm:size-11 text-foreground/55 aria-[current=page]:text-foreground" })}>
        <Settings />
      </Link>
    </Hint>
  );
}
