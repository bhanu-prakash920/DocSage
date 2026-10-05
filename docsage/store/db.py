"""SQLite registry: collections, documents, jobs, chunks, conversations, queries and evaluations."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import NotFound

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    pages INTEGER NOT NULL DEFAULT 0,
    n_text INTEGER NOT NULL DEFAULT 0,
    n_table INTEGER NOT NULL DEFAULT 0,
    n_image INTEGER NOT NULL DEFAULT 0,
    parser TEXT,
    chunking TEXT,
    estimate_json TEXT,
    usage_json TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    duration_s REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection_id);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(collection_id, sha256);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    document_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'queued',
    message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_collection ON jobs(collection_id, created_at);
CREATE TABLE IF NOT EXISTS parents (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    collection_id TEXT NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parents_document ON parents(document_id);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    collection_id TEXT NOT NULL,
    parent_id TEXT,
    modality TEXT NOT NULL,
    page INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    title TEXT,
    text TEXT NOT NULL,
    asset_path TEXT,
    bbox_json TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_collection ON chunks(collection_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE TABLE IF NOT EXISTS entities (
    collection_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    PRIMARY KEY (collection_id, entity, chunk_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queries (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    standalone_question TEXT,
    answer TEXT,
    status TEXT NOT NULL,
    error TEXT,
    mode TEXT NOT NULL,
    options_json TEXT,
    sources_json TEXT,
    trace_json TEXT,
    verdict_json TEXT,
    used_web INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queries_collection ON queries(collection_id, created_at);
CREATE INDEX IF NOT EXISTS idx_queries_conversation ON queries(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS eval_items (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    reference_answer TEXT NOT NULL DEFAULT '',
    expected_document TEXT,
    expected_page INTEGER,
    tag TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    job_id TEXT,
    status TEXT NOT NULL,
    configs_json TEXT NOT NULL,
    results_json TEXT,
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
"""

JSON_FIELDS = {
    "estimate_json": "estimate",
    "usage_json": "usage",
    "result_json": "result",
    "bbox_json": "bbox",
    "meta_json": "meta",
    "options_json": "options",
    "sources_json": "sources",
    "trace_json": "trace",
    "verdict_json": "verdict",
    "configs_json": "configs",
    "results_json": "results",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40] or "collection"


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():  # noqa: SIM118 - sqlite3.Row iterates values, not keys
        value = row[key]
        if key in JSON_FIELDS:
            out[JSON_FIELDS[key]] = json.loads(value) if value else None
        else:
            out[key] = value
    return out


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self.tx() as conn:
            conn.executescript(SCHEMA)

    # -- connection management -------------------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self.conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        return _row(self.conn().execute(sql, tuple(params)).fetchone())

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [r for r in (_row(x) for x in self.conn().execute(sql, tuple(params)).fetchall()) if r]

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.conn().execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    # -- collections -----------------------------------------------------------------------------
    def create_collection(
        self, name: str, description: str, embedding: dict[str, Any], hidden: bool = False
    ) -> dict[str, Any]:
        base = slugify(name)
        cid = base
        i = 2
        while self.scalar("SELECT 1 FROM collections WHERE id=?", (cid,)):
            cid = f"{base}-{i}"
            i += 1
        ts = now()
        with self.tx() as c:
            c.execute(
                "INSERT INTO collections (id,name,description,embedding_provider,embedding_model,embedding_dim,"
                "hidden,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    name.strip(),
                    description.strip(),
                    embedding["provider"],
                    embedding["model"],
                    int(embedding["dim"]),
                    int(hidden),
                    ts,
                    ts,
                ),
            )
        return self.get_collection(cid)

    def get_collection(self, cid: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM collections WHERE id=?", (cid,))
        if not row:
            raise NotFound(f"Collection '{cid}' does not exist.")
        return row

    def list_collections(self) -> list[dict[str, Any]]:
        return self.all(
            """SELECT c.*,
                (SELECT COUNT(*) FROM documents d WHERE d.collection_id=c.id) AS documents,
                (SELECT COUNT(*) FROM documents d WHERE d.collection_id=c.id AND d.status='ready') AS ready_documents,
                (SELECT COUNT(*) FROM chunks k WHERE k.collection_id=c.id) AS chunks
               FROM collections c WHERE c.hidden=0 ORDER BY c.created_at"""
        )

    def update_collection(self, cid: str, **fields: Any) -> dict[str, Any]:
        allowed = {k: v for k, v in fields.items() if k in ("name", "description") and v is not None}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            with self.tx() as c:
                c.execute(
                    f"UPDATE collections SET {sets}, updated_at=? WHERE id=?", (*allowed.values(), now(), cid)
                )
        return self.get_collection(cid)

    def bump_version(self, cid: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE collections SET version=version+1, updated_at=? WHERE id=?", (now(), cid))

    def delete_collection(self, cid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM jobs WHERE collection_id=?", (cid,))
            c.execute("DELETE FROM queries WHERE collection_id=?", (cid,))
            c.execute("DELETE FROM entities WHERE collection_id=?", (cid,))
            c.execute("DELETE FROM collections WHERE id=?", (cid,))

    # -- documents -------------------------------------------------------------------------------
    def create_document(self, **fields: Any) -> dict[str, Any]:
        doc_id = new_id("doc")
        ts = now()
        with self.tx() as c:
            c.execute(
                "INSERT INTO documents (id,collection_id,filename,path,sha256,size_bytes,kind,status,estimate_json,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    fields["collection_id"],
                    fields["filename"],
                    fields["path"],
                    fields["sha256"],
                    fields["size_bytes"],
                    fields["kind"],
                    fields.get("status", "staged"),
                    _dump(fields.get("estimate")),
                    ts,
                    ts,
                ),
            )
        return self.get_document(doc_id)

    def get_document(self, doc_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not row:
            raise NotFound(f"Document '{doc_id}' does not exist.")
        return row

    def find_document_by_hash(self, cid: str, sha256: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM documents WHERE collection_id=? AND sha256=? ORDER BY created_at LIMIT 1",
            (cid, sha256),
        )

    def list_documents(self, cid: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM documents WHERE collection_id=? ORDER BY created_at DESC", (cid,))

    def update_document(self, doc_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, values = [], []
        for key, value in fields.items():
            col = next((k for k, v in JSON_FIELDS.items() if v == key), key)
            cols.append(f"{col}=?")
            values.append(_dump(value) if col in JSON_FIELDS else value)
        with self.tx() as c:
            c.execute(
                f"UPDATE documents SET {', '.join(cols)}, updated_at=? WHERE id=?", (*values, now(), doc_id)
            )

    def delete_document(self, doc_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    # -- chunks / parents --------------------------------------------------------------------------
    def replace_document_content(
        self,
        doc_id: str,
        cid: str,
        parents: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
        entities: list[tuple[str, str]],
    ) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM parents WHERE document_id=?", (doc_id,))
            c.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
            c.executemany(
                "INSERT INTO parents (id,document_id,collection_id,page,text) VALUES (?,?,?,?,?)",
                [(p["id"], doc_id, cid, p["page"], p["text"]) for p in parents],
            )
            c.executemany(
                "INSERT INTO chunks (id,document_id,collection_id,parent_id,modality,page,ordinal,title,text,"
                "asset_path,bbox_json,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        k["id"],
                        doc_id,
                        cid,
                        k.get("parent_id"),
                        k["modality"],
                        k["page"],
                        k["ordinal"],
                        k.get("title"),
                        k["text"],
                        k.get("asset_path"),
                        _dump(k.get("bbox")),
                        _dump(k.get("meta")),
                    )
                    for k in chunks
                ],
            )
            c.executemany(
                "INSERT OR IGNORE INTO entities (collection_id,entity,chunk_id) VALUES (?,?,?)",
                [(cid, e, chunk_id) for e, chunk_id in entities],
            )

    def clear_document_content(self, doc_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM parents WHERE document_id=?", (doc_id,))
            c.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))

    def collection_chunks(self, cid: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT k.id,k.document_id,k.parent_id,k.modality,k.page,k.text,k.title,k.asset_path "
            "FROM chunks k JOIN documents d ON d.id=k.document_id WHERE k.collection_id=? AND d.status='ready'",
            (cid,),
        )

    def chunks_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.all(
            f"SELECT k.*, d.filename FROM chunks k JOIN documents d ON d.id=k.document_id WHERE k.id IN ({marks})",
            ids,
        )
        return {r["id"]: r for r in rows}

    def document_chunks(self, doc_id: str, limit: int = 500) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM chunks WHERE document_id=? ORDER BY page, ordinal LIMIT ?", (doc_id, limit)
        )

    def parents_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {r["id"]: r for r in self.all(f"SELECT * FROM parents WHERE id IN ({marks})", ids)}

    def chunks_for_entities(self, cid: str, entities: list[str], limit: int = 30) -> list[str]:
        if not entities:
            return []
        marks = ",".join("?" * len(entities))
        rows = (
            self.conn()
            .execute(
                f"SELECT chunk_id, COUNT(*) AS n FROM entities WHERE collection_id=? AND entity IN ({marks}) "
                f"GROUP BY chunk_id ORDER BY n DESC LIMIT ?",
                (cid, *entities, limit),
            )
            .fetchall()
        )
        return [r[0] for r in rows]

    def collection_entities(self, cid: str) -> list[str]:
        return [
            r[0]
            for r in self.conn()
            .execute("SELECT DISTINCT entity FROM entities WHERE collection_id=?", (cid,))
            .fetchall()
        ]

    # -- jobs --------------------------------------------------------------------------------------
    def create_job(self, cid: str, kind: str, document_id: str | None = None) -> dict[str, Any]:
        job_id = new_id("job")
        with self.tx() as c:
            c.execute(
                "INSERT INTO jobs (id,collection_id,document_id,kind,status,created_at) VALUES (?,?,?,?,?,?)",
                (job_id, cid, document_id, kind, "queued", now()),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise NotFound(f"Job '{job_id}' does not exist.")
        return row

    def update_job(self, job_id: str, **fields: Any) -> None:
        cols, values = [], []
        for key, value in fields.items():
            col = "result_json" if key == "result" else key
            cols.append(f"{col}=?")
            values.append(_dump(value) if col == "result_json" else value)
        with self.tx() as c:
            c.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", (*values, job_id))

    def list_jobs(
        self, cid: str | None = None, active: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = "SELECT j.*, d.filename FROM jobs j LEFT JOIN documents d ON d.id=j.document_id WHERE 1=1"
        params: list[Any] = []
        if cid:
            sql += " AND j.collection_id=?"
            params.append(cid)
        if active:
            sql += " AND j.status IN ('queued','running')"
        sql += " ORDER BY j.created_at DESC LIMIT ?"
        params.append(limit)
        return self.all(sql, params)

    def fail_interrupted_jobs(self) -> int:
        with self.tx() as c:
            cur = c.execute(
                "UPDATE jobs SET status='failed', message='Interrupted by a server restart.', "
                "finished_at=? WHERE status IN ('queued','running')",
                (now(),),
            )
            c.execute(
                "UPDATE documents SET status='failed', error='Interrupted by a server restart.' "
                "WHERE status IN ('queued','processing')"
            )
            c.execute(
                "UPDATE eval_runs SET status='failed', error='Interrupted by a server restart.' "
                "WHERE status IN ('queued','running')"
            )
            return cur.rowcount

    # -- conversations & queries -------------------------------------------------------------------
    def create_conversation(self, cid: str, title: str) -> dict[str, Any]:
        conv_id = new_id("conv")
        ts = now()
        with self.tx() as c:
            c.execute(
                "INSERT INTO conversations (id,collection_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                (conv_id, cid, title[:120], ts, ts),
            )
        return self.get_conversation(conv_id)

    def get_conversation(self, conv_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM conversations WHERE id=?", (conv_id,))
        if not row:
            raise NotFound(f"Conversation '{conv_id}' does not exist.")
        return row

    def touch_conversation(self, conv_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))

    def list_conversations(self, cid: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.all(
            "SELECT c.*, (SELECT COUNT(*) FROM queries q WHERE q.conversation_id=c.id) AS turns "
            "FROM conversations c WHERE c.collection_id=? ORDER BY c.updated_at DESC LIMIT ?",
            (cid, limit),
        )

    def delete_conversation(self, conv_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    def conversation_turns(self, conv_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM queries WHERE conversation_id=? ORDER BY created_at", (conv_id,))

    def save_query(self, record: dict[str, Any]) -> None:
        cols = [
            "id",
            "collection_id",
            "conversation_id",
            "question",
            "standalone_question",
            "answer",
            "status",
            "error",
            "mode",
            "options_json",
            "sources_json",
            "trace_json",
            "verdict_json",
            "used_web",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "created_at",
        ]
        values = []
        for col in cols:
            key = JSON_FIELDS.get(col, col)
            value = record.get(key)
            values.append(_dump(value) if col in JSON_FIELDS else value)
        with self.tx() as c:
            c.execute(f"INSERT INTO queries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", values)

    def list_queries(self, cid: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.all(
            "SELECT id,conversation_id,question,status,mode,used_web,latency_ms,cost_usd,created_at "
            "FROM queries WHERE collection_id=? ORDER BY created_at DESC LIMIT ?",
            (cid, limit),
        )

    # -- evaluation --------------------------------------------------------------------------------
    def add_eval_item(self, cid: str, **f: Any) -> dict[str, Any]:
        item_id = new_id("evi")
        with self.tx() as c:
            c.execute(
                "INSERT INTO eval_items (id,collection_id,question,reference_answer,expected_document,"
                "expected_page,tag,source,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    cid,
                    f["question"].strip(),
                    (f.get("reference_answer") or "").strip(),
                    f.get("expected_document"),
                    f.get("expected_page"),
                    f.get("tag"),
                    f.get("source", "manual"),
                    now(),
                ),
            )
        return self.one("SELECT * FROM eval_items WHERE id=?", (item_id,)) or {}

    def list_eval_items(self, cid: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM eval_items WHERE collection_id=? ORDER BY created_at", (cid,))

    def delete_eval_item(self, item_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM eval_items WHERE id=?", (item_id,))

    def create_eval_run(self, cid: str, configs: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
        run_id = new_id("run")
        with self.tx() as c:
            c.execute(
                "INSERT INTO eval_runs (id,collection_id,job_id,status,configs_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, cid, job_id, "queued", _dump(configs), now()),
            )
        return self.get_eval_run(run_id)

    def get_eval_run(self, run_id: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM eval_runs WHERE id=?", (run_id,))
        if not row:
            raise NotFound(f"Evaluation run '{run_id}' does not exist.")
        return row

    def update_eval_run(self, run_id: str, **fields: Any) -> None:
        cols, values = [], []
        for key, value in fields.items():
            col = "results_json" if key == "results" else key
            cols.append(f"{col}=?")
            values.append(_dump(value) if col == "results_json" else value)
        with self.tx() as c:
            c.execute(f"UPDATE eval_runs SET {', '.join(cols)} WHERE id=?", (*values, run_id))

    def list_eval_runs(self, cid: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM eval_runs WHERE collection_id=? ORDER BY created_at DESC LIMIT ?", (cid, limit)
        )

    # -- stats -------------------------------------------------------------------------------------
    def stats(self, cid: str | None = None) -> dict[str, Any]:
        where = "WHERE collection_id=?" if cid else ""
        p: tuple[Any, ...] = (cid,) if cid else ()
        docs = (
            self.one(
                f"SELECT COUNT(*) AS total, SUM(status='ready') AS ready, SUM(status='failed') AS failed, "
                f"SUM(status IN ('queued','processing')) AS processing, SUM(status='staged') AS staged, "
                f"COALESCE(SUM(pages),0) AS pages, COALESCE(SUM(cost_usd),0) AS ingest_cost FROM documents {where}",
                p,
            )
            or {}
        )
        modality = {
            r["modality"]: r["n"]
            for r in self.all(f"SELECT modality, COUNT(*) AS n FROM chunks {where} GROUP BY modality", p)
        }
        q = (
            self.one(
                f"SELECT COUNT(*) AS total, COALESCE(SUM(cost_usd),0) AS cost, AVG(latency_ms) AS avg_latency, "
                f"SUM(used_web) AS web, SUM(status='failed') AS failed FROM queries {where}",
                p,
            )
            or {}
        )
        daily = self.all(
            f"SELECT substr(created_at,1,10) AS day, COUNT(*) AS queries, COALESCE(SUM(cost_usd),0) AS cost, "
            f"AVG(latency_ms) AS avg_latency FROM queries {where} GROUP BY day ORDER BY day DESC LIMIT 14",
            p,
        )
        latencies = [
            r[0]
            for r in self.conn()
            .execute(
                f"SELECT latency_ms FROM queries {where} {'AND' if where else 'WHERE'} latency_ms IS NOT NULL "
                f"ORDER BY created_at DESC LIMIT 200",
                p,
            )
            .fetchall()
        ]
        return {
            "documents": {k: int(v or 0) if k != "ingest_cost" else float(v or 0) for k, v in docs.items()},
            "chunks": {
                "text": modality.get("text", 0),
                "table": modality.get("table", 0),
                "image": modality.get("image", 0),
                "total": sum(modality.values()),
            },
            "queries": {
                "total": int(q.get("total") or 0),
                "cost": float(q.get("cost") or 0),
                "avg_latency_ms": float(q["avg_latency"]) if q.get("avg_latency") else None,
                "web": int(q.get("web") or 0),
                "failed": int(q.get("failed") or 0),
                "p50_latency_ms": _percentile(latencies, 50),
                "p95_latency_ms": _percentile(latencies, 95),
            },
            "daily": list(reversed(daily)),
        }


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return float(ordered[idx])


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    from ..config import get_settings

    global _db
    with _db_lock:
        path = get_settings().data_dir / "docsage.sqlite3"
        if _db is None or _db.path != path:
            _db = Database(path)
        return _db


def reset_db() -> None:
    global _db
    with _db_lock:
        _db = None
