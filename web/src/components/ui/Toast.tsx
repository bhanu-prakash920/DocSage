import { AlertTriangle, CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type Tone = "ok" | "err" | "warn" | "info";
interface ToastItem {
  id: number;
  tone: Tone;
  title: string;
  body?: string;
}

const ToastContext = createContext<(tone: Tone, title: string, body?: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (tone: Tone, title: string, body?: string) => {
      const id = next.current++;
      setItems((xs) => [...xs.slice(-3), { id, tone, title, body }]);
      window.setTimeout(() => dismiss(id), tone === "err" ? 9000 : 4500);
    },
    [dismiss],
  );
  const value = useMemo(() => push, [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="false">
        {items.map((t) => {
          const Icon = t.tone === "ok" ? CheckCircle2 : t.tone === "err" ? CircleAlert : t.tone === "warn" ? AlertTriangle : Info;
          return (
            <div key={t.id} className={`toast glass glass--strong toast--${t.tone}`} role={t.tone === "err" ? "alert" : "status"}>
              <Icon aria-hidden="true" />
              <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                <strong style={{ fontFamily: "var(--font-display)" }}>{t.title}</strong>
                {t.body && <span className="secondary break">{t.body}</span>}
              </div>
              <button className="btn btn--ghost btn--sm btn--icon" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
                <X aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
