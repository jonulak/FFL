"use client"

import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { cn } from "cn"

import { Button } from "@/components/ui/button"
import { XIcon } from "lucide-react"

function Dialog({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger({ ...props }: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal({ ...props }: DialogPrimitive.Portal.Props) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />
}

function DialogClose({ ...props }: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: DialogPrimitive.Backdrop.Props) {
  return (
    <DialogPrimitive.Backdrop
      data-slot="dialog-overlay"
      className={cn(
        // A plain scrim, not a backdrop blur: blurring the whole page every frame of the fade is
        // what made opening a dialog stutter on the phone.
        "fixed inset-0 isolate z-50 bg-black/25 transition-opacity duration-300 ease-(--ease-arrive) data-starting-style:opacity-0 data-ending-style:opacity-0 data-ending-style:duration-150 data-ending-style:ease-(--ease-leave) dark:bg-black/50",
        className
      )}
      {...props}
    />
  )
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  anchor = "center",
  ...props
}: DialogPrimitive.Popup.Props & {
  showCloseButton?: boolean
  // "top" for a dialog whose content arrives after it opens (a fetched log, an outlook being
  // written): centred, every change of height moves its top edge, and the dialog reads as
  // jumping into place; pinned at 7.5dvh it only grows downward. 7.5dvh is where a centred
  // dialog at the 85dvh cap stands, so a full one sits where it always did.
  anchor?: "center" | "top"
}) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        className={cn(
          // Transitions on Base UI's starting/ending styles rather than keyframes, so a tap that
        // closes the dialog mid-open reverses it from where it is instead of jumping.
        "fixed left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 gap-4 rounded-xl bg-popover p-4 text-sm text-popover-foreground ring-1 ring-foreground/10 outline-none sm:max-w-sm",
        "transition-[opacity,scale,translate] duration-300 ease-(--ease-arrive) data-starting-style:scale-[0.97] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0 data-ending-style:duration-150 data-ending-style:ease-(--ease-leave) motion-reduce:data-starting-style:scale-100",
        anchor === "top"
          ? "top-[7.5dvh] origin-top data-starting-style:translate-y-3 motion-reduce:data-starting-style:translate-y-0"
          : "top-1/2 -translate-y-1/2 data-starting-style:translate-y-[calc(-50%+0.75rem)] motion-reduce:data-starting-style:-translate-y-1/2",
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            render={
              <Button
                variant="ghost"
                className="absolute top-2 right-2"
                size="icon-sm"
              />
            }
          >
            <XIcon
            />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Popup>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  )
}

function DialogFooter({
  className,
  showCloseButton = false,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean
}) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "-mx-4 -mb-4 flex flex-col-reverse gap-2 rounded-b-xl border-t bg-muted/50 p-4 sm:flex-row sm:justify-end",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton && (
        <DialogPrimitive.Close render={<Button variant="outline" />}>
          Close
        </DialogPrimitive.Close>
      )}
    </div>
  )
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(
        "font-heading text-base leading-none font-medium",
        className
      )}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "text-sm text-muted-foreground *:[a]:underline *:[a]:underline-offset-3 *:[a]:hover:text-foreground",
        className
      )}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}
