import { X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useDialogBehaviour(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const first = node?.querySelector<HTMLElement>("[data-autofocus]") ?? node?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
      if (e.key === "Tab" && node) {
        const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const [a, b] = [items[0], items[items.length - 1]];
        if (e.shiftKey && document.activeElement === a) {
          e.preventDefault();
          b.focus();
        } else if (!e.shiftKey && document.activeElement === b) {
          e.preventDefault();
          a.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, [open, onClose]);
  return ref;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useDialogBehaviour(open, onClose);
  const titleId = useId();
  if (!open) return null;
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        className={`modal glass glass--strong ${wide ? "modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="modal__head">
          <div className="stack" style={{ gap: 4, minWidth: 0, flex: 1 }}>
            <h2 className="modal__title" id={titleId}>
              {title}
            </h2>
            {description && <p className="secondary" style={{ fontSize: "var(--fs-sm)" }}>{description}</p>}
          </div>
          <Button variant="ghost" size="sm" iconOnly icon={<X aria-hidden="true" />} onClick={onClose}>
            Close
          </Button>
        </header>
        <div className="modal__body">{children}</div>
        {footer && <footer className="modal__foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onClose,
  title,
  eyebrow,
  children,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  eyebrow?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  const ref = useDialogBehaviour(open, onClose);
  const titleId = useId();
  if (!open) return null;
  return createPortal(
    <div className="overlay drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className="drawer glass glass--strong" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="modal__head">
          <div className="stack" style={{ gap: 2, minWidth: 0, flex: 1 }}>
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            <h2 className="modal__title break" id={titleId}>
              {title}
            </h2>
          </div>
          {actions}
          <Button variant="ghost" size="sm" iconOnly icon={<X aria-hidden="true" />} onClick={onClose}>
            Close
          </Button>
        </header>
        <div className="modal__body" style={{ flex: 1 }}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmLabel,
  requireText,
  loading,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  requireText?: string;
  loading?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const id = useId();
  useEffect(() => setTyped(""), [open]);
  const blocked = requireText != null && typed.trim() !== requireText;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button onClick={onClose}>Keep it</Button>
          <Button variant="danger" onClick={onConfirm} disabled={blocked} loading={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="secondary">{children}</div>
        {requireText != null && (
          <div className="field">
            <label className="field__label" htmlFor={id}>
              Type <span className="mono">{requireText}</span> to confirm
            </label>
            <input id={id} className="input input--mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </div>
        )}
      </div>
    </Modal>
  );
}

/** Anchored popover that closes on outside click and Escape. */
export function Popover({
  open,
  onClose,
  children,
  align = "left",
  width,
  label,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  align?: "left" | "right";
  width?: number;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const parent = ref.current?.parentElement;
      if (parent && !parent.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      ref={ref}
      className="popover glass glass--strong"
      role="dialog"
      aria-label={label}
      style={{ [align]: 0, top: "calc(100% + 6px)", width }}
    >
      {children}
    </div>
  );
}
