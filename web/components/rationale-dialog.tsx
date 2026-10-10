"use client";

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { Decision, Presets } from "@/lib/api";
import { PHONE_INSET } from "./claims-submit";
import { RationaleForm } from "./rationale-form";

// RationaleForm in a Dialog, open while `decision` is set. Closing by overlay or Escape is Later:
// the row stays open on the Decisions page for a reason to be given there. One shape serves the
// page's Edit (the full row) and a move's own prompt (only what the answer carried).
export function RationaleDialog({ decision, presets, withOutcome = false, onClose, onSaved }: {
  decision: (Pick<Decision, "id" | "kind" | "title"> & Partial<Pick<Decision, "rationale" | "rationale_note" | "outcome">>) | null;
  presets: Presets | null;
  withOutcome?: boolean;
  onClose: () => void;
  onSaved?: (d: Decision) => void;
}) {
  return (
    <Dialog open={decision != null && presets != null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className={`sm:max-w-md ${PHONE_INSET}`}>
        {decision && presets && (
          <>
            <DialogHeader>
              <DialogTitle>Why?</DialogTitle>
              <DialogDescription>{decision.title}</DialogDescription>
            </DialogHeader>
            <RationaleForm key={decision.id} decision={decision} presets={presets} withOutcome={withOutcome}
              onSaved={(d) => { onSaved?.(d); onClose(); }} onLater={onClose} />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
