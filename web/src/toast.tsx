import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

type ToastKind = "success" | "error" | "info";
type Toast = { id: number; kind: ToastKind; title: string; message?: string; leaving?: boolean };

const ToastContext = createContext<(kind: ToastKind, title: string, message?: string) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

const ICONS: Record<ToastKind, string> = { success: "✓", error: "!", info: "i" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.map((x) => (x.id === id ? { ...x, leaving: true } : x)));
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 260);
  }, []);

  const push = useCallback(
    (kind: ToastKind, title: string, message?: string) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, kind, title, message }]);
      setTimeout(() => dismiss(id), kind === "error" ? 6000 : 3500);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}${t.leaving ? " toast-leave" : ""}`} onClick={() => dismiss(t.id)}>
            <span className={`toast-icon toast-icon-${t.kind}`}>{ICONS[t.kind]}</span>
            <div>
              <p className="toast-title">{t.title}</p>
              {t.message && <p className="toast-message">{t.message}</p>}
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
