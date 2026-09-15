import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'

/**
 * A panel that slides in from the right over the page behind it.
 *
 * Chosen over a full-page detail view for incident triage: the list keeps its scroll position and
 * its selection, so closing one incident puts you back exactly where you were in the queue rather
 * than at the top of a re-rendered page. Wide, because incident detail carries an analysis, cited
 * evidence and a chat transcript — a narrow drawer would just move the old cramped column.
 */
export function Drawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title?: ReactNode
  onClose: () => void
  children: ReactNode
}) {
  // Escape closes it: a drawer covers the list, and reaching for the mouse to get back to a queue
  // you are working through is the kind of friction that makes people stop using the queue.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        aria-label="Close"
        className="flex-1 cursor-default bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="animate-in flex h-full w-full max-w-3xl flex-col border-l border-hair bg-plane shadow-2xl">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-hair px-5 py-3">
          <div className="min-w-0 flex-1">{title}</div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-md p-1 text-muted transition hover:bg-surface-2 hover:text-ink"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  )
}
