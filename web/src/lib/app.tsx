import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Collection } from "./types";

type Theme = "system" | "light" | "dark";
const THEME_KEY = "docsage.theme";
const COLLECTION_KEY = "docsage.collection";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: preference lasts for this page only */
  }
}

interface AppState {
  theme: Theme;
  setTheme: (t: Theme) => void;
  collections: Collection[];
  collectionsLoading: boolean;
  collectionsError: unknown;
  current: Collection | null;
  setCurrent: (id: string) => void;
  refreshCollections: () => void;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => (read(THEME_KEY) as Theme) || "system");
  const [currentId, setCurrentId] = useState<string | null>(() => read(COLLECTION_KEY));
  const qc = useQueryClient();
  const collectionsQuery = useQuery({ queryKey: ["collections"], queryFn: api.collections });
  const collections = collectionsQuery.data ?? [];

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    write(THEME_KEY, theme);
  }, [theme]);

  const current = useMemo(
    () => collections.find((c) => c.id === currentId) ?? collections[0] ?? null,
    [collections, currentId],
  );

  const setCurrent = useCallback((id: string) => {
    setCurrentId(id);
    write(COLLECTION_KEY, id);
  }, []);

  const value: AppState = {
    theme,
    setTheme: setThemeState,
    collections,
    collectionsLoading: collectionsQuery.isLoading,
    collectionsError: collectionsQuery.error,
    current,
    setCurrent,
    refreshCollections: () => qc.invalidateQueries({ queryKey: ["collections"] }),
  };
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside AppProvider");
  return ctx;
}

/** Poll active jobs for a collection; refresh dependent queries when a job finishes. */
export function useActiveJobs(cid: string | undefined) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["jobs", cid, "active"],
    queryFn: () => api.jobs(cid, true),
    enabled: !!cid,
    refetchInterval: (q) => ((q.state.data?.length ?? 0) > 0 ? 1200 : 6000),
  });
  const count = query.data?.length ?? 0;
  const [previous, setPrevious] = useState(0);
  useEffect(() => {
    if (count < previous) {
      qc.invalidateQueries({ queryKey: ["documents", cid] });
      qc.invalidateQueries({ queryKey: ["stats", cid] });
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.invalidateQueries({ queryKey: ["eval-runs", cid] });
    }
    setPrevious(count);
  }, [count, previous, qc, cid]);
  return query;
}
