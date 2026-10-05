import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, ServerCrash } from "lucide-react";
import { Component, lazy, Suspense, useId, useState, type ErrorInfo, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { Button, ButtonLink } from "./components/ui/Button";
import { Modal } from "./components/ui/Overlay";
import { EmptyState, Field, SkeletonRows, Spinner } from "./components/ui/primitives";
import { ToastProvider } from "./components/ui/Toast";
import { ApiError, api, getToken, setToken } from "./lib/api";
import { AppProvider, useApp } from "./lib/app";
import { AskPage } from "./pages/AskPage";

const LibraryPage = lazy(() => import("./pages/LibraryPage").then((m) => ({ default: m.LibraryPage })));
const OverviewPage = lazy(() => import("./pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const EvaluatePage = lazy(() => import("./pages/EvaluatePage").then((m) => ({ default: m.EvaluatePage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI error", error, info.componentStack);
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page" style={{ padding: "var(--s-6) var(--s-4)" }}>
        <EmptyState
          icon={<ServerCrash />}
          title="This view crashed"
          actions={
            <Button variant="primary" onClick={() => this.setState({ error: null })}>
              Reload view
            </Button>
          }
        >
          {this.state.error.message}. Your data is safe on the server; reloading usually fixes this.
        </EmptyState>
      </div>
    );
  }
}

function TokenPrompt({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState(getToken() ?? "");
  const id = useId();
  return (
    <Modal
      open
      onClose={() => undefined}
      title="Access token required"
      description="This DocSage server is protected. Paste the value of DOCSAGE_API_TOKEN."
      footer={
        <Button
          variant="primary"
          icon={<KeyRound aria-hidden="true" />}
          disabled={!value.trim()}
          onClick={() => {
            setToken(value.trim());
            onSaved();
          }}
        >
          Unlock
        </Button>
      }
    >
      <Field label="API token" htmlFor={id} hint="Stored in this browser only.">
        <input id={id} className="input input--mono" type="password" value={value} onChange={(e) => setValue(e.target.value)} data-autofocus />
      </Field>
    </Modal>
  );
}

function Gate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: 1 });
  const { collectionsError } = useApp();
  if (health.isLoading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "100dvh" }}>
        <Spinner large label="Connecting to DocSage" />
      </div>
    );
  }
  if (health.error) {
    return (
      <div className="page" style={{ padding: "var(--s-8) var(--s-4)", maxWidth: 720 }}>
        <EmptyState
          icon={<ServerCrash />}
          title="Cannot reach the DocSage server"
          actions={
            <Button variant="primary" onClick={() => health.refetch()} loading={health.isFetching}>
              Retry
            </Button>
          }
        >
          Start the backend with <code>docsage serve</code> (or <code>docker compose up</code>) and try again.
        </EmptyState>
      </div>
    );
  }
  const unauthorized = collectionsError instanceof ApiError && collectionsError.status === 401;
  return (
    <>
      {children}
      {unauthorized && <TokenPrompt onSaved={() => qc.invalidateQueries()} />}
    </>
  );
}

function NotFound() {
  return (
    <EmptyState icon={<ServerCrash />} title="Page not found" actions={<ButtonLink to="/" variant="primary">Go to Ask</ButtonLink>}>
      The address does not match any DocSage page.
    </EmptyState>
  );
}

export function App() {
  return (
    <ToastProvider>
      <AppProvider>
        <Gate>
          <AppShell>
            <ErrorBoundary>
              <Suspense fallback={<SkeletonRows rows={5} height={64} />}>
              <Routes>
                <Route path="/" element={<AskPage />} />
                <Route path="/library" element={<LibraryPage />} />
                <Route path="/library/:docId" element={<LibraryPage />} />
                <Route path="/overview" element={<OverviewPage />} />
                <Route path="/evaluate" element={<EvaluatePage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
              </Suspense>
            </ErrorBoundary>
          </AppShell>
        </Gate>
      </AppProvider>
    </ToastProvider>
  );
}
