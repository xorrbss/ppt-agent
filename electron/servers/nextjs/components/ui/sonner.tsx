"use client"

import type React from "react"
import { BadgeCheck, Loader2, ShieldAlert } from "lucide-react"
import { Toaster as Sonner } from "sonner"

type ToasterProps = React.ComponentProps<typeof Sonner>

const Toaster = ({ icons, ...props }: ToasterProps) => {
  const defaultIcons: NonNullable<ToasterProps["icons"]> = {
    success: <BadgeCheck aria-hidden="true" />,
    error: <ShieldAlert aria-hidden="true" />,
    info: <ShieldAlert aria-hidden="true" />,
    warning: <ShieldAlert aria-hidden="true" />,
    loading: <Loader2 aria-hidden="true" className="animate-spin" />,
    close: <span aria-hidden="true">Got it!</span>,
  }

  return (
    <>
      <style jsx global>{`
        /* Constrain toast width similar to the design mock */
       
        /* Neutral "card" toast container */
        [data-sonner-toast][data-styled="true"] {
          border-radius: 10px !important;
         
          border: 1px solid #E1E1E5 !important;
          box-shadow: 0 4px 8px 0 rgba(0, 0, 0, 0.06) !important;
          padding: 8px !important;
          gap: 10px !important;
          backdrop-filter: blur(6px) !important;
          background: white !important;
        }

        /* Typography */
        [data-sonner-toast][data-styled="true"] [data-title] {
          font-family: var(--font-syne), ui-sans-serif, system-ui, -apple-system,
            BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial,
            "Noto Sans", sans-serif !important;
          font-size: 12px !important;
          font-weight: 500 !important;
          line-height: 14px !important;
          letter-spacing: 0.04em !important;
          color: rgb(15 23 42) !important; /* slate-900 */
          text-transform: none !important;
        }

        [data-sonner-toast][data-styled="true"] [data-description] {
          font-family: var(--font-syne), ui-sans-serif, system-ui, -apple-system,
            BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial,
            "Noto Sans", sans-serif !important;
          font-size: 10px !important;
          font-weight: 400 !important;
          line-height: 10px !important; /* 100% */
          letter-spacing: 0.03em !important;
          color: rgb(100 116 139) !important; /* slate-500 */
        }

        [data-sonner-toast][data-styled="true"] [data-content] {
          gap: 2px !important;
          flex: 1 1 auto !important;
          min-width: 0 !important;
        }

        /* Left icon badge */
        [data-sonner-toast][data-styled="true"] [data-icon] {
          width: 20px !important;
          height: 20px !important;
          display: flex !important;
          align-items: center !important;
          justify-content: center !important;
          margin: 0 !important;
          color: rgb(51 65 85) !important; /* slate-700 */
        }

        [data-sonner-toast][data-styled="true"] [data-icon] svg {
          width: 20px !important;
          height: 20px !important;
        }

        /* Per-type icon colors */
        [data-sonner-toast][data-type="success"] [data-icon] {
          color: rgb(22, 163, 74) !important;
        }

        [data-sonner-toast][data-type="error"] [data-icon] {
          color: rgb(220, 38, 38) !important;
        }

        [data-sonner-toast][data-type="info"] [data-icon] {
          color: rgb(37, 99, 235) !important;
        }

        [data-sonner-toast][data-type="warning"] [data-icon] {
          color: rgb(217, 119, 6) !important;
        }

        [data-sonner-toast][data-type="loading"] [data-icon] {
          color: rgb(124, 58, 237) !important;
        }

        /* Outline buttons like the mock ("Got it!") */
        [data-sonner-toast][data-styled="true"] [data-button] {
          height: auto !important;
          padding: 4px 8px !important;
          border-radius: 6px !important;
          font-family: var(--font-syne), ui-sans-serif, system-ui, -apple-system,
            BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial,
            "Noto Sans", sans-serif !important;
          font-size: 10px !important;
          font-weight: 400 !important;
          background: rgb(255 255 255) !important;
          color: #3F3F3F !important;
          border: 1px solid #EDEEEF !important;
          box-shadow: none !important;
        }

        /* Always-present "Got it!" button (styled close button) */
        [data-sonner-toast][data-styled="true"] [data-close-button] {
          position: static !important;
          inset: auto !important;
          transform: none !important;
          order: 9999 !important;
          flex: 0 0 auto !important;
          flex-shrink: 0 !important;
          white-space: nowrap !important;
          width: auto !important;
          height: auto !important;
          padding: 4px 8px !important;
          border-radius: 6px !important;
          margin-left: auto !important;
          margin-right: 0 !important;
          align-self: center !important;
          background: rgb(255 255 255) !important;
          color: #3F3F3F !important;
          border: 1px solid #EDEEEF !important;
          box-shadow: none !important;
          font-family: var(--font-syne), ui-sans-serif, system-ui, -apple-system,
            BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial,
            "Noto Sans", sans-serif !important;
          font-size: 10px !important;
          font-weight: 400 !important;
          line-height: 14px !important;
          letter-spacing: 0.02em !important;
        }

        [data-sonner-toast][data-styled="true"] [data-close-button]:hover {
          background: rgb(248 250 252) !important; /* slate-50 */
        }

        [data-sonner-toast][data-styled="true"] [data-button]:hover {
          background: rgb(248 250 252) !important; /* slate-50 */
        }

        /* Dark mode */
        .dark [data-sonner-toast][data-styled="true"] {
          background: rgb(2 6 23) !important; /* slate-950 */
          border: 1px solid rgba(148, 163, 184, 0.22) !important; /* slate-400 @ 22% */
          box-shadow: 0 10px 26px rgba(0, 0, 0, 0.45) !important;
        }

        .dark [data-sonner-toast][data-styled="true"] [data-title] {
          color: rgb(248 250 252) !important; /* slate-50 */
        }

        .dark [data-sonner-toast][data-styled="true"] [data-description] {
          color: rgb(148 163 184) !important; /* slate-400 */
        }

        .dark [data-sonner-toast][data-styled="true"] [data-button] {
          background: rgb(2 6 23) !important;
          color: rgb(248 250 252) !important;
          border: 1px solid rgba(148, 163, 184, 0.26) !important;
        }

        .dark [data-sonner-toast][data-styled="true"] [data-close-button] {
          background: rgb(2 6 23) !important;
          color: rgb(248 250 252) !important;
          border: 1px solid rgba(148, 163, 184, 0.26) !important;
        }

        .dark [data-sonner-toast][data-styled="true"] [data-button]:hover {
          background: rgb(15 23 42) !important; /* slate-900 */
        }

        .dark [data-sonner-toast][data-styled="true"] [data-close-button]:hover {
          background: rgb(15 23 42) !important; /* slate-900 */
        }
      `}</style>
      <Sonner
        style={{
          zIndex: 999999999,
          background: 'red',
        }}
        className="toaster group z-50 bg-white"
        icons={{ ...defaultIcons, ...(icons ?? {}) }}
        toastOptions={{
          closeButtonAriaLabel: "Dismiss notification",
          classNames: {
            toast: "group toast",
            description: "group-[.toast]:text-muted-foreground",
            actionButton:
              "group-[.toast]:rounded-2xl group-[.toast]:border group-[.toast]:border-slate-200 group-[.toast]:bg-white group-[.toast]:text-slate-900 hover:group-[.toast]:bg-slate-50",
            cancelButton:
              "group-[.toast]:rounded-2xl group-[.toast]:border group-[.toast]:border-slate-200 group-[.toast]:bg-white group-[.toast]:text-slate-700 hover:group-[.toast]:bg-slate-50",
          },
        }}
        {...props}
      />
    </>
  )
}

export { Toaster }
