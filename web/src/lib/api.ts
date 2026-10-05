import type {
  Collection,
  Conversation,
  DocumentDetail,
  DocumentRow,
  EvalItem,
  EvalRun,
  Health,
  Job,
  QueryEvent,
  QueryOptions,
  QueryRecord,
  SettingsResponse,
  SettingsValues,
  Stats,
  ProviderStatus,
} from "./types";

const TOKEN_KEY = "docsage.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token lives for this page only */
  }
}

export class ApiError extends Error {
  status: number;
  hint: string | null;
  constructor(status: number, message: string, hint: string | null = null) {
    super(message);
    this.status = status;
    this.hint = hint;
  }
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function parseError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let hint: string | null = null;
  try {
    const body = await res.json();
    if (body?.error) message = body.error;
    else if (body?.detail) message = typeof body.detail === "string" ? body.detail : "The request was not valid.";
    hint = body?.hint ?? null;
    if (Array.isArray(body?.detail)) {
      message = body.detail.map((d: { msg: string; loc?: unknown[] }) => `${(d.loc ?? []).slice(-1)}: ${d.msg}`).join("; ");
    }
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 0 || res.status >= 502) hint = hint ?? "Is the DocSage server running? Start it with `docsage serve`.";
  return new ApiError(res.status, message, hint);
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      signal,
      headers: headers(body !== undefined && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the DocSage server.", "Check that `docsage serve` is running.");
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("GET", "/health"),
  settings: () => request<SettingsResponse>("GET", "/settings"),
  saveSettings: (changes: Partial<SettingsValues>) => request<SettingsResponse>("PATCH", "/settings", changes),
  overview: () =>
    request<Stats & { collections: Collection[]; recent_jobs: Job[]; status: ProviderStatus }>("GET", "/overview"),

  collections: () => request<Collection[]>("GET", "/collections"),
  createCollection: (name: string, description: string) =>
    request<Collection>("POST", "/collections", { name, description }),
  updateCollection: (id: string, name: string, description: string) =>
    request<Collection>("PATCH", `/collections/${id}`, { name, description }),
  deleteCollection: (id: string) => request<void>("DELETE", `/collections/${id}`),
  stats: (id: string) => request<Stats>("GET", `/collections/${id}/stats`),
  loadSamples: () => request<Collection>("POST", "/samples"),

  documents: (cid: string) => request<DocumentRow[]>("GET", `/collections/${cid}/documents`),
  document: (id: string) => request<DocumentDetail>("GET", `/documents/${id}`),
  upload: (cid: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    return request<{ documents: DocumentRow[]; errors: { filename: string; error: string; hint: string | null }[] }>(
      "POST",
      `/collections/${cid}/uploads`,
      form,
    );
  },
  ingest: (cid: string, documentIds?: string[]) =>
    request<Job[]>("POST", `/collections/${cid}/ingest`, { document_ids: documentIds ?? null }),
  reingest: (docId: string) => request<Job>("POST", `/documents/${docId}/ingest`),
  deleteDocument: (docId: string) => request<void>("DELETE", `/documents/${docId}`),

  jobs: (cid?: string, active = false) =>
    request<Job[]>("GET", `/jobs?${new URLSearchParams({ ...(cid ? { collection_id: cid } : {}), active: String(active) })}`),
  cancelJob: (id: string) => request<Job>("POST", `/jobs/${id}/cancel`),

  conversations: (cid: string) => request<Conversation[]>("GET", `/collections/${cid}/conversations`),
  conversation: (id: string) => request<Conversation & { turns: QueryRecord[] }>("GET", `/conversations/${id}`),
  deleteConversation: (id: string) => request<void>("DELETE", `/conversations/${id}`),

  evalItems: (cid: string) => request<EvalItem[]>("GET", `/collections/${cid}/eval/items`),
  addEvalItem: (cid: string, item: Partial<EvalItem>) => request<EvalItem>("POST", `/collections/${cid}/eval/items`, item),
  importEvalItems: (cid: string, items: Partial<EvalItem>[]) =>
    request<EvalItem[]>("POST", `/collections/${cid}/eval/items/import`, { items }),
  generateEvalItems: (cid: string, count: number) =>
    request<EvalItem[]>("POST", `/collections/${cid}/eval/items/generate`, { count }),
  deleteEvalItem: (id: string) => request<void>("DELETE", `/eval/items/${id}`),
  evalRuns: (cid: string) => request<EvalRun[]>("GET", `/collections/${cid}/eval/runs`),
  evalRun: (id: string) => request<EvalRun>("GET", `/eval/runs/${id}`),
  startEval: (cid: string, presets: string[], chunking: string[] | null) =>
    request<EvalRun>("POST", `/collections/${cid}/eval/runs`, { presets, chunking }),
};

export function fileUrl(assetPath: string): string {
  const token = getToken();
  return `/api/files/${assetPath}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}

/** POST a question and invoke `onEvent` for each server-sent event. */
export async function streamQuery(
  cid: string,
  body: { question: string; conversation_id?: string | null } & QueryOptions,
  onEvent: (event: QueryEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/collections/${cid}/query`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the DocSage server.", "Check that `docsage serve` is running.");
  }
  if (!res.ok || !res.body) throw await parseError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index: number;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const line = raw.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as QueryEvent);
    }
  }
}
