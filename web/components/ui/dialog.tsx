"use client"

import * as React from "react"
// Base UI's Drawer is its Dialog plus swipe-to-dismiss: every dialog here swipes down to close on a
// touch screen. A mouse drag inside the panel stays a text selection (the children sit in
// Drawer.Content); add data-base-ui-swipe-ignore to anything that drags on its own.
import { Drawer as DialogPrimitive } from "@base-ui/react/drawer"
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
        "fixed inset-0 isolate z-50 bg-black/25 opacity-[calc(1-var(--drawer-swipe-progress,0))] transition-opacity duration-300 ease-(--ease-arrive) data-swiping:duration-0 data-starting-style:opacity-0 data-ending-style:opacity-0 data-ending-style:duration-150 data-ending-style:ease-(--ease-leave) dark:bg-black/50",
        className
      )}
      {...props}
    />
  )
}

// The visible area, for centring a dialog in it. On iOS, at the foot of a page with the toolbar
// shown, the visual viewport is shorter than the layout viewport fixed elements are placed in and
// sits lower in it (traced on the phone: 718px tall, 94px down an 812px layout viewport), so a
// dialog centred on `top: 50%` opened with its top above the screen. While any DialogContent is
// mounted, <html> carries the visual viewport as --vv-top and --vv-h; the popup centres on them and the
// tall dialogs cap their height by --vv-h.
let vvOpen = 0
let vvStop: (() => void) | null = null
function useVisualViewportVars() {
  React.useLayoutEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    if (vvOpen++ === 0) {
      const html = document.documentElement
      const set = () => {
        html.style.setProperty("--vv-top", `${vv.offsetTop}px`)
        html.style.setProperty("--vv-h", `${vv.height}px`)
      }
      set()
      vv.addEventListener("resize", set)
      vv.addEventListener("scroll", set)
      vvStop = () => {
        vv.removeEventListener("resize", set)
        vv.removeEventListener("scroll", set)
        html.style.removeProperty("--vv-top")
        html.style.removeProperty("--vv-h")
      }
    }
    return () => {
      if (--vvOpen === 0) { vvStop?.(); vvStop = null }
    }
  }, [])
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  initialFocus,
  ref,
  ...props
}: DialogPrimitive.Popup.Props & {
  showCloseButton?: boolean
}) {
  // Focus lands on the panel itself unless a dialog says otherwise. Our dialogs open from app
  // state (after the log's hold), not a Base UI trigger, so Base UI cannot tell a tap opened them
  // and focuses the first button inside without preventScroll. On iOS, once the page is scrolled
  // far enough to collapse the toolbar, the scroll lock does not hold and that focus pans the
  // viewport, carrying the fixed dialog off screen. The panel is focused with preventScroll.
  const panel = React.useRef<HTMLDivElement | null>(null)
  const setRef = React.useCallback((el: HTMLDivElement | null) => {
    panel.current = el
    if (typeof ref === "function") ref(el)
    else if (ref) ref.current = el
  }, [ref])
  useVisualViewportVars()
  return (
    <DialogPortal>
      <DialogOverlay />
      {/* the viewport is where the swipe is read; the popup keeps its own fixed centring */}
      <DialogPrimitive.Viewport className="fixed inset-0 z-50">
      <DialogPrimitive.Popup
        ref={setRef}
        initialFocus={initialFocus ?? panel}
        data-slot="dialog-content"
        className={cn(
          // Transitions on Base UI's starting/ending styles rather than keyframes, so a tap that
        // closes the dialog mid-open reverses it from where it is instead of jumping.
        "fixed top-[calc(var(--vv-top,0px)+var(--vv-h,100%)/2)] left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 gap-4 rounded-xl bg-popover p-4 text-sm text-popover-foreground ring-1 ring-foreground/10 outline-none sm:max-w-sm",
        "transition-[opacity,scale,translate,transform] duration-300 ease-(--ease-arrive) data-starting-style:translate-y-[calc(-50%+0.75rem)] data-starting-style:scale-[0.97] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0 data-ending-style:duration-150 data-ending-style:ease-(--ease-leave) motion-reduce:data-starting-style:-translate-y-1/2 motion-reduce:data-starting-style:scale-100",
        // The swipe: the panel follows the finger (Base UI writes the transform while it moves), a short
        // pull springs back, and a dismissing one carries on down off the screen at the finger's speed
        // rather than fading where it was let go.
        "[transform:translateY(var(--drawer-swipe-movement-y,0px))] data-swiping:select-none data-swipe-dismiss:data-ending-style:[transform:translateY(calc(var(--vv-h,100dvh)/2+50%))] data-swipe-dismiss:data-ending-style:scale-100 data-swipe-dismiss:data-ending-style:opacity-100 data-swipe-dismiss:data-ending-style:duration-[calc(var(--drawer-swipe-strength,1)*320ms)] data-swipe-dismiss:data-ending-style:ease-out",
          className
        )}
        {...props}
      >
        <DialogPrimitive.Content className="contents">{children}</DialogPrimitive.Content>
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
      </DialogPrimitive.Viewport>
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
