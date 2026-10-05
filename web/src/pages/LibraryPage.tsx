import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  FileImage,
  FileSpreadsheet,
  FileText,
  FolderPlus,
  Globe,
  Pencil,
  Presentation,
  RefreshCw,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CreateCollectionModal } from "../components/layout/CollectionSwitcher";
import { DocumentDrawer } from "../components/library/DocumentDrawer";
import { UploadPanel } from "../components/library/UploadPanel";
import { Button } from "../components/ui/Button";
import { ConfirmDialog, Modal } from "../components/ui/Overlay";
import { Badge, EmptyState, ErrorAlert, Field, PageHeader, Progress, SkeletonRows, StatusBadge, Tooltip } from "../components/ui/primitives";
import { useToast } from "../components/ui/Toast";
import { api, type ApiError } from "../lib/api";
import { useActiveJobs, useApp } from "../lib/app";
import { fmtCost, fmtInt, fmtRelative, pluralize } from "../lib/format";
import { useDocumentTitle, useMediaQuery } from "../lib/hooks";
import type { DocumentRow, Job } from "../lib/types";

function KindIcon({ kind }: { kind: string }) {
  const Icon =
    kind === "pptx" ? Presentation : kind === "image" ? FileImage : kind === "csv" ? FileSpreadsheet : kind === "html" ? Globe : FileText;
  return <Icon aria-hidden="true" className="kind-icon" />;
}

function DocStatusCell({ doc, job, onCancel }: { doc: DocumentRow; job?: Job; onCancel: (id: string) => void }) {
  const active = doc.status === "queued" || doc.status === "processing";
  return (
    <div className="stack" style={{ gap: 6, minWidth: 140 }}>
      <div className="row" style={{ gap: 6 }}>
        {doc.status === "failed" && doc.error ? (
          <Tooltip content={doc.error}>
            <span tabIndex={0}>
              <StatusBadge status={doc.status} />
            </span>
          </Tooltip>
        ) : (
          <StatusBadge status={doc.status} />
        )}
        {active && job && (
          <button type="button" className="link-btn" onClick={() => onCancel(job.id)}>
            Cancel
          </button>
        )}
      </div>
      {active && (
        <>
          <Progress value={job?.progress ?? 0} indeterminate={!job || job.status === "queued"} label={`Ingesting ${doc.filename}`} />
          <span className="mono muted truncate" style={{ fontSize: "var(--fs-2xs)", maxWidth: 220 }}>
            {job?.message ?? "Waiting in queue"}
          </span>
        </>
      )}
      {doc.status === "failed" && doc.error && (
        <span className="field__error" style={{ maxWidth: 260 }}>
          {doc.error}
        </span>
      )}
    </div>
  );
}

export function LibraryPage() {
  const { current, collections, setCurrent, refreshCollections, collectionsLoading } = useApp();
  const cid = current?.id;
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { docId } = useParams();
  const desktop = useMediaQuery("(min-width: 860px)");
  useDocumentTitle("Library");

  const jobs = useActiveJobs(cid);
  const docs = useQuery({
    queryKey: ["documents", cid],
    queryFn: () => api.documents(cid!),
    enabled: !!cid,
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === "queued" || d.status === "processing") ? 1500 : false),
  });
  const jobByDoc = useMemo(() => new Map((jobs.data ?? []).map((j) => [j.document_id, j])), [jobs.data]);
  const all = docs.data ?? [];
  const staged = all.filter((d) => d.status === "staged");
  const listed = all.filter((d) => d.status !== "staged");

  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [deletingCollection, setDeletingCollection] = useState(false);
  const [deleteDoc, setDeleteDoc] = useState<DocumentRow | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["documents", cid] });
    qc.invalidateQueries({ queryKey: ["jobs", cid, "active"] });
    qc.invalidateQueries({ queryKey: ["collections"] });
    qc.invalidateQueries({ queryKey: ["stats", cid] });
  };
  const reingest = useMutation({
    mutationFn: (id: string) => api.reingest(id),
    onSuccess: () => {
      invalidate();
      toast("ok", "Re-indexing started");
    },
    onError: (e: ApiError) => toast("err", "Could not re-index", e.message),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelJob(id),
    onSuccess: () => {
      invalidate();
      toast("info", "Cancelling", "The job stops at its next checkpoint.");
    },
  });
  const removeDoc = useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: () => {
      invalidate();
      toast("ok", "Document deleted", "Its chunks, figures and file were removed from the index.");
      setDeleteDoc(null);
    },
    onError: (e: ApiError) => toast("err", "Could not delete the document", e.message),
  });
  const rename = useMutation({
    mutationFn: () => api.updateCollection(cid!, name.trim(), description.trim()),
    onSuccess: () => {
      refreshCollections();
      setRenaming(false);
      toast("ok", "Collection updated");
    },
  });
  const removeCollection = useMutation({
    mutationFn: () => api.deleteCollection(cid!),
    onSuccess: () => {
      const next = collections.find((c) => c.id !== cid);
      if (next) setCurrent(next.id);
      refreshCollections();
      setDeletingCollection(false);
      toast("ok", "Collection deleted");
    },
    onError: (e: ApiError) => toast("err", "Could not delete the collection", e.message),
  });
  const samples = useMutation({
    mutationFn: api.loadSamples,
    onSuccess: (c) => {
      refreshCollections();
      setCurrent(c.id);
      toast("ok", "Sample collection created", "Four documents are being indexed.");
    },
    onError: (e: ApiError) => toast("err", "Could not create the sample collection", e.message),
  });

  if (collectionsLoading) return <SkeletonRows rows={5} height={64} />;
  if (!current) {
    return (
      <>
        <PageHeader index="02" title="Library" />
        <EmptyState
          icon={<FolderPlus />}
          title="No collections yet"
          actions={
            <>
              <Button variant="primary" icon={<FolderPlus aria-hidden="true" />} onClick={() => setCreating(true)}>
                New collection
              </Button>
              <Button icon={<Sparkles aria-hidden="true" />} loading={samples.isPending} onClick={() => samples.mutate()}>
                Load sample collection
              </Button>
            </>
          }
        >
          Collections keep separate indexes, conversations and evaluation sets. Create one for each body of documents.
        </EmptyState>
        <CreateCollectionModal open={creating} onClose={() => setCreating(false)} />
      </>
    );
  }

  const ready = all.filter((d) => d.status === "ready").length;
  const actions = (doc: DocumentRow) => {
    const job = jobByDoc.get(doc.id);
    const active = doc.status === "queued" || doc.status === "processing";
    return (
      <div className="row" style={{ gap: 2, justifyContent: "flex-end" }}>
        {active && job ? (
          <Button size="sm" variant="ghost" iconOnly icon={<Ban aria-hidden="true" />} onClick={() => cancel.mutate(job.id)}>
            Cancel ingestion of {doc.filename}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            iconOnly
            icon={<RefreshCw aria-hidden="true" />}
            onClick={() => reingest.mutate(doc.id)}
            title="Re-index with the current settings"
          >
            Re-index {doc.filename}
          </Button>
        )}
        <Button size="sm" variant="ghost" iconOnly icon={<Trash2 aria-hidden="true" />} disabled={active} onClick={() => setDeleteDoc(doc)}>
          Delete {doc.filename}
        </Button>
      </div>
    );
  };

  return (
    <>
      <PageHeader
        index="02"
        title="Library"
        meta={
          <>
            <strong style={{ color: "var(--ink)" }}>{current.name}</strong>
            <Badge>{fmtInt(ready)} ready</Badge>
            <Badge>{fmtInt(current.chunks ?? 0)} chunks</Badge>
            <span className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>
              {current.embedding_provider}:{current.embedding_model}
            </span>
          </>
        }
        actions={
          <>
            <Button
              icon={<Pencil aria-hidden="true" />}
              onClick={() => {
                setName(current.name);
                setDescription(current.description);
                setRenaming(true);
              }}
            >
              Edit
            </Button>
            <Button icon={<FolderPlus aria-hidden="true" />} onClick={() => setCreating(true)}>
              New collection
            </Button>
            <Button variant="danger" icon={<Trash2 aria-hidden="true" />} onClick={() => setDeletingCollection(true)}>
              Delete
            </Button>
          </>
        }
      />
      {current.description && <p className="secondary" style={{ marginTop: "calc(-1 * var(--s-3))", marginBottom: "var(--s-5)", maxWidth: "70ch" }}>{current.description}</p>}

      <div className="library">
        <div className="library__upload">
          <UploadPanel cid={current.id} staged={staged} />
        </div>
        <section className="library__docs" aria-labelledby="docs-title">
          <h2 id="docs-title" className="section-title" style={{ marginTop: 0 }}>
            Documents <span className="eyebrow">{fmtInt(listed.length)} total</span>
          </h2>
          {docs.isLoading ? (
            <SkeletonRows rows={5} height={56} />
          ) : docs.error ? (
            <ErrorAlert error={docs.error} title="Could not load documents" retry={() => docs.refetch()} />
          ) : listed.length === 0 ? (
            <EmptyState
              icon={<Upload />}
              title="Nothing indexed yet"
              actions={
                <Button icon={<Sparkles aria-hidden="true" />} loading={samples.isPending} onClick={() => samples.mutate()}>
                  Load sample collection
                </Button>
              }
            >
              Upload files on the left. Each one is parsed into text, tables and figures, then embedded so you can ask about it.
            </EmptyState>
          ) : desktop ? (
            <div className="panel table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Status</th>
                    <th className="num" title="Text, table and figure chunks">Chunks T · Tb · F</th>
                    <th className="num">Cost</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {listed.map((d) => (
                    <tr key={d.id} data-selected={d.id === docId || undefined}>
                      <td style={{ maxWidth: 340 }}>
                        <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                          <button type="button" className="doc-link" onClick={() => navigate(`/library/${d.id}`)}>
                            <KindIcon kind={d.kind} />
                            <span className="truncate">{d.filename}</span>
                          </button>
                          <span className="mono muted doc-sub">
                            {d.status === "ready" ? `${pluralize(d.pages, d.usage?.unit ?? "page")} · ` : ""}
                            added {fmtRelative(d.created_at)}
                          </span>
                        </div>
                      </td>
                      <td>
                        <DocStatusCell doc={d} job={jobByDoc.get(d.id)} onCancel={(id) => cancel.mutate(id)} />
                      </td>
                      <td className="num">{d.status === "ready" ? `${d.n_text} · ${d.n_table} · ${d.n_image}` : "–"}</td>
                      <td className="num">{fmtCost(d.cost_usd)}</td>
                      <td>{actions(d)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ul className="doc-cards">
              {listed.map((d) => (
                <li key={d.id} className="panel doc-card">
                  <button type="button" className="doc-link" onClick={() => navigate(`/library/${d.id}`)}>
                    <KindIcon kind={d.kind} />
                    <span className="truncate">{d.filename}</span>
                  </button>
                  <DocStatusCell doc={d} job={jobByDoc.get(d.id)} onCancel={(id) => cancel.mutate(id)} />
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>
                      {d.status === "ready" ? `${pluralize(d.pages, d.usage?.unit ?? "page")} · ${d.n_text}/${d.n_table}/${d.n_image} chunks · ` : ""}
                      {fmtCost(d.cost_usd)} · {fmtRelative(d.created_at)}
                    </span>
                    {actions(d)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <DocumentDrawer docId={docId ?? null} onClose={() => navigate("/library")} />
      <CreateCollectionModal open={creating} onClose={() => setCreating(false)} />
      <Modal
        open={renaming}
        onClose={() => setRenaming(false)}
        title="Edit collection"
        footer={
          <>
            <Button onClick={() => setRenaming(false)}>Cancel</Button>
            <Button variant="primary" disabled={!name.trim()} loading={rename.isPending} onClick={() => rename.mutate()}>
              Save
            </Button>
          </>
        }
      >
        <div className="stack">
          {rename.error && <ErrorAlert error={rename.error} />}
          <Field label="Name" htmlFor="col-name">
            <input id="col-name" className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} data-autofocus />
          </Field>
          <Field label="Description" htmlFor="col-desc">
            <textarea id="col-desc" className="textarea" rows={3} maxLength={400} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </Modal>
      <ConfirmDialog
        open={!!deleteDoc}
        onClose={() => setDeleteDoc(null)}
        onConfirm={() => deleteDoc && removeDoc.mutate(deleteDoc.id)}
        loading={removeDoc.isPending}
        title="Delete this document?"
        confirmLabel="Delete document"
      >
        <strong>{deleteDoc?.filename}</strong> and all of its chunks, figures and the stored file will be removed. Past answers keep
        their text but its sources will no longer open.
      </ConfirmDialog>
      <ConfirmDialog
        open={deletingCollection}
        onClose={() => setDeletingCollection(false)}
        onConfirm={() => removeCollection.mutate()}
        loading={removeCollection.isPending}
        title="Delete the whole collection?"
        confirmLabel="Delete collection"
        requireText={current.name}
      >
        This permanently removes {fmtInt(all.length)} documents, their index, every conversation and the evaluation set in “
        {current.name}”.
      </ConfirmDialog>
    </>
  );
}
