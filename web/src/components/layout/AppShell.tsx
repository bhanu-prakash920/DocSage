import { useQuery } from "@tanstack/react-query";
import {
  FlaskConical,
  LayoutDashboard,
  Library,
  Menu,
  MessageSquareText,
  Monitor,
  Moon,
  SlidersHorizontal,
  Sun,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { api } from "../../lib/api";
import { useActiveJobs, useApp } from "../../lib/app";
import { CollectionSwitcher } from "./CollectionSwitcher";

export const NAV = [
  { to: "/", label: "Ask", index: "01", icon: MessageSquareText, end: true },
  { to: "/library", label: "Library", index: "02", icon: Library },
  { to: "/overview", label: "Overview", index: "03", icon: LayoutDashboard },
  { to: "/evaluate", label: "Evaluate", index: "04", icon: FlaskConical },
  { to: "/settings", label: "Settings", index: "05", icon: SlidersHorizontal },
];

function Brand() {
  return (
    <Link to="/" className="brand" aria-label="DocSage home">
      <span className="brand__mark" aria-hidden="true">
        <span />
      </span>
      <span className="brand__word">
        Doc<span>Sage</span>
      </span>
    </Link>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useApp();
  const options = [
    { value: "light" as const, icon: Sun, label: "Light theme" },
    { value: "system" as const, icon: Monitor, label: "Match system theme" },
    { value: "dark" as const, icon: Moon, label: "Dark theme" },
  ];
  return (
    <div className="segmented theme-toggle" role="radiogroup" aria-label="Theme">
      {options.map(({ value, icon: Icon, label }) => (
        <button key={value} type="button" role="radio" aria-checked={theme === value} title={label} onClick={() => setTheme(value)}>
          <Icon aria-hidden="true" />
          <span className="sr-only">{label}</span>
        </button>
      ))}
    </div>
  );
}

function ProviderStatus() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  if (!data) return null;
  return (
    <Link to="/settings" className="provider-pill" title="Model provider. Change it in Settings.">
      <span className={`dot ${data.generative ? "dot--ok" : "dot--warn"}`} aria-hidden="true" />
      <span className="mono">{data.generative ? data.llm : "offline mode"}</span>
      <span className="muted mono">v{data.version}</span>
    </Link>
  );
}

function IndexingIndicator() {
  const { current } = useApp();
  const { data } = useActiveJobs(current?.id);
  const ingest = (data ?? []).filter((j) => j.kind === "ingest");
  const evals = (data ?? []).filter((j) => j.kind === "eval");
  if (!ingest.length && !evals.length) return null;
  return (
    <div className="indexing" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="stack" style={{ gap: 0 }}>
        {ingest.length > 0 && (
          <Link to="/library">
            Indexing {ingest.length} {ingest.length === 1 ? "document" : "documents"}
          </Link>
        )}
        {evals.length > 0 && <Link to="/evaluate">Evaluation running</Link>}
      </span>
    </div>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main">
      <ul className="nav">
        {NAV.map(({ to, label, index, icon: Icon, end }) => (
          <li key={to}>
            <NavLink to={to} end={end} className="nav__link" onClick={onNavigate}>
              <span className="nav__index mono" aria-hidden="true">
                {index}
              </span>
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);
  const current = NAV.find((n) => (n.end ? location.pathname === n.to : location.pathname.startsWith(n.to)));

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar glass" aria-label="Sidebar">
        <Brand />
        <CollectionSwitcher />
        <NavList />
        <span className="spacer" />
        <IndexingIndicator />
        <div className="sidebar__foot">
          <ThemeToggle />
          <ProviderStatus />
        </div>
      </aside>

      <header className="mobile-bar glass">
        <Brand />
        <span className="mobile-bar__page mono">
          {current?.index} {current?.label}
        </span>
        <button
          type="button"
          className="btn btn--secondary btn--icon"
          aria-expanded={menuOpen}
          aria-controls="mobile-nav"
          onClick={() => setMenuOpen((v) => !v)}
        >
          {menuOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
          <span className="sr-only">{menuOpen ? "Close menu" : "Open menu"}</span>
        </button>
      </header>
      {menuOpen && <div className="mobile-backdrop" onClick={() => setMenuOpen(false)} aria-hidden="true" />}
      {menuOpen && (
        <div className="mobile-nav glass glass--strong" id="mobile-nav">
          <CollectionSwitcher compact />
          <NavList onNavigate={() => setMenuOpen(false)} />
          <IndexingIndicator />
          <div className="sidebar__foot">
            <ThemeToggle />
            <ProviderStatus />
          </div>
        </div>
      )}

      <main id="main" className="main" tabIndex={-1}>
        <div className="page" key={location.pathname.split("/")[1]}>
          {children}
        </div>
      </main>
    </div>
  );
}
