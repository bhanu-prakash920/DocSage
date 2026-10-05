export type Modality = "text" | "table" | "image" | "web";
export type DocStatus = "staged" | "queued" | "processing" | "ready" | "failed" | "cancelled";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Collection {
  id: string;
  name: string;
  description: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_dim: number;
  version: number;
  created_at: string;
  updated_at: string;
  documents?: number;
  ready_documents?: number;
  chunks?: number;
}

export interface Estimate {
  pages: number;
  chars: number;
  images: number;
  tables: number;
  llm_calls: number;
  llm_model: string;
  embedding_model: string;
  cost_usd: number;
  cost_low_usd: number;
  cost_high_usd: number;
  priced: boolean;
  chunking: string;
  over_budget: boolean;
  kind: string;
}

export interface DocUsage {
  calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  warnings?: string[];
  title?: string;
  unit?: string;
  llm?: string;
  embedding?: string;
}

export interface DocumentRow {
  id: string;
  collection_id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  kind: string;
  status: DocStatus;
  error: string | null;
  pages: number;
  n_text: number;
  n_table: number;
  n_image: number;
  parser: string | null;
  chunking: string | null;
  estimate: Estimate | null;
  usage: DocUsage | null;
  cost_usd: number;
  duration_s: number | null;
  created_at: string;
  updated_at: string;
  duplicate?: boolean;
}

export interface Chunk {
  id: string;
  document_id: string;
  parent_id: string | null;
  modality: Modality;
  page: number;
  ordinal: number;
  title: string | null;
  text: string;
  asset_path: string | null;
  meta: Record<string, unknown> | null;
}

export interface DocumentDetail extends DocumentRow {
  chunks: Chunk[];
}

export interface Job {
  id: string;
  collection_id: string;
  document_id: string | null;
  kind: "ingest" | "eval";
  status: JobStatus;
  progress: number;
  stage: string;
  message: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  filename?: string | null;
}

export interface Source {
  n: number;
  chunk_id: string | null;
  document_id: string | null;
  filename: string;
  page: number | null;
  modality: Modality;
  text: string;
  score: number;
  title: string | null;
  asset_path: string | null;
  url: string | null;
  signals: Record<string, unknown>;
  cited: boolean;
  truncated?: boolean;
}

export interface TraceStep {
  step: "rewrite" | "retrieve" | "grade" | "web" | "generate";
  label: string;
  ms?: number;
  status: "running" | "done" | "error";
  detail?: Record<string, unknown>;
}

export interface Usage {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface Verdict {
  relevant: boolean;
  sufficient: boolean;
  missing: string;
}

export type QueryEvent =
  | { type: "start"; query_id: string; conversation_id: string | null; mode: string }
  | ({ type: "step" } & TraceStep)
  | { type: "sources"; sources: Source[] }
  | ({ type: "verdict" } & Verdict)
  | { type: "token"; text: string }
  | {
      type: "done";
      query_id: string;
      conversation_id: string | null;
      answer: string;
      sources: Source[];
      standalone_question: string | null;
      verdict: (Verdict & { useful_sources?: number[] }) | null;
      used_web: boolean;
      latency_ms: number;
      usage: Usage;
      trace: TraceStep[];
    }
  | { type: "error"; message: string; hint: string | null; query_id: string };

export interface QueryOptions {
  mode?: "simple" | "agentic";
  top_k?: number;
  hybrid?: boolean;
  rerank?: "none" | "llm" | "cross-encoder";
  parent_expansion?: boolean;
  web_search?: boolean;
  graph?: boolean;
  modalities?: ("text" | "table" | "image")[];
  document_ids?: string[];
}

export interface Conversation {
  id: string;
  collection_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turns?: number;
}

export interface QueryRecord {
  id: string;
  conversation_id: string | null;
  question: string;
  standalone_question: string | null;
  answer: string | null;
  status: "done" | "failed";
  error: string | null;
  mode: string;
  sources: Source[] | null;
  trace: TraceStep[] | null;
  verdict: Verdict | null;
  used_web: number;
  latency_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  created_at: string;
}

export interface Stats {
  documents: { total: number; ready: number; failed: number; processing: number; staged: number; pages: number; ingest_cost: number };
  chunks: { text: number; table: number; image: number; total: number };
  queries: {
    total: number;
    cost: number;
    avg_latency_ms: number | null;
    p50_latency_ms: number | null;
    p95_latency_ms: number | null;
    web: number;
    failed: number;
  };
  daily: { day: string; queries: number; cost: number; avg_latency: number | null }[];
  recent_queries?: Pick<QueryRecord, "id" | "conversation_id" | "question" | "status" | "mode" | "used_web" | "latency_ms" | "cost_usd" | "created_at">[];
  recent_jobs?: Job[];
}

export interface ProviderStatus {
  llm_provider: string;
  llm_model: string | null;
  llm_fast_model: string | null;
  generative: boolean;
  embedding: { provider: string; model: string; dim: number };
  keys: Record<string, boolean>;
  web_search_available: boolean;
  local_embeddings_available: boolean;
  error: string | null;
}

export type SettingsValues = {
  llm_provider: string;
  llm_model: string | null;
  llm_fast_model: string | null;
  embedding_provider: string;
  embedding_model: string | null;
  embedding_dim: number | null;
  parser: string;
  chunking: string;
  chunk_size: number;
  chunk_overlap: number;
  summarize_images: boolean;
  summarize_tables: boolean;
  ocr_tables: boolean;
  graph_rag: boolean;
  query_mode: "simple" | "agentic";
  top_k: number;
  hybrid: boolean;
  rerank: string;
  parent_expansion: boolean;
  web_search: boolean;
  agent_max_retries: number;
  anthropic_effort: string;
  max_ingest_cost_usd: number;
  max_query_cost_usd: number;
};

export interface SettingsResponse {
  values: SettingsValues;
  status: ProviderStatus;
  editable: string[];
  options: Record<string, string[]>;
  data_dir: string;
}

export interface Health {
  status: string;
  version: string;
  llm: string;
  generative: boolean;
  auth_required: boolean;
}

export interface EvalItem {
  id: string;
  collection_id: string;
  question: string;
  reference_answer: string;
  expected_document: string | null;
  expected_page: number | null;
  tag: string | null;
  source: string;
  created_at: string;
}

export interface EvalSummaryRow {
  config: string;
  hit_rate: number | null;
  mrr: number | null;
  faithfulness: number | null;
  relevance: number | null;
  correctness: number | null;
  latency_ms: number | null;
  cost_usd: number;
  errors: number;
}

export interface EvalItemResult {
  config: string;
  item_id: string;
  question: string;
  answer: string;
  error: string | null;
  hit: boolean | null;
  rr: number | null;
  latency_ms: number;
  cost_usd: number;
  faithfulness: number | null;
  relevance: number | null;
  correctness: number | null;
  notes: string;
  method: string;
}

export interface EvalRun {
  id: string;
  collection_id: string;
  job_id: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  configs: { name: string; preset: string; chunking: string | null; mode: string }[];
  results: { summary: EvalSummaryRow[]; items: EvalItemResult[]; judge: string; judge_cost_usd: number; n_items: number } | null;
  error: string | null;
  cost_usd: number;
  created_at: string;
  finished_at: string | null;
}
