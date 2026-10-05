import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileUp, Play, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { api, type ApiError } from "../../lib/api";
import { fmtBytes, fmtCost, fmtInt } from "../../lib/format";
import type { DocumentRow } from "../../lib/types";
import { Button } from "../ui/Button";
import { Alert, Badge, Panel, PanelHead } from "../ui/primitives";
import { useToast } from "../ui/Toast";

const ACCEPT = ".pdf,.epub,.xps,.docx,.pptx,.html,.htm,.md,.markdown,.txt,.csv,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff";

export function UploadPanel({ cid, staged }: { cid: string; staged: DocumentRow[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [errors, setErrors] = useState<{ filename: string; error: string; hint: string | null }[]>([]);
  const [duplicates, setDuplicates] = useState<string[]>([]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["documents", cid] });
    qc.invalidateQueries({ queryKey: ["jobs", cid, "active"] });
    qc.invalidateQueries({ queryKey: ["collections"] });
  };

  const upload = useMutation({
    mutationFn: (files: File[]) => api.upload(cid, files),
    onSuccess: (res) => {
      setErrors(res.errors);
      setDuplicates(res.documents.filter((d) => d.duplicate).map((d) => d.filename));
      refresh();
      const fresh = res.documents.filter((d) => !d.duplicate).length;
      if (fresh) toast("ok", `${fresh} ${fresh === 1 ? "file" : "files"} ready to review`, "Check the estimate, then start ingestion.");
    },
    onError: (e: ApiError) => toast("err", "Upload failed", e.message),
  });
  const ingest = useMutation({
    mutationFn: () => api.ingest(cid),
    onSuccess: (jobs) => {
      refresh();
      toast("ok", `Indexing ${jobs.length} ${jobs.length === 1 ? "document" : "documents"}`, "You can keep working; progress shows in the table.");
    },
    onError: (e: ApiError) => toast("err", "Could not start ingestion", e.message),
  });
  const discard = useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: refresh,
    onError: (e: ApiError) => toast("err", "Could not discard the file", e.message),
  });

  const onFiles = (list: FileList | null) => {
    const files = [...(list ?? [])];
    if (files.length) upload.mutate(files);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    onFiles(e.dataTransfer.files);
  };

  const total = staged.reduce(
    (acc, d) => ({
      low: acc.low + (d.estimate?.cost_low_usd ?? 0),
      high: acc.high + (d.estimate?.cost_high_usd ?? 0),
      calls: acc.calls + (d.estimate?.llm_calls ?? 0),
      over: acc.over || !!d.estimate?.over_budget,
    }),
    { low: 0, high: 0, calls: 0, over: false },
  );

  return (
    <Panel variant="shadow" aria-labelledby="upload-title">
      <PanelHead id="upload-title" title="Add documents" eyebrow="Upload · Review · Ingest" />
      <div className="panel__body stack">
        <div
          className="dropzone"
          data-dragging={dragging || undefined}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <UploadCloud aria-hidden="true" className="dropzone__icon" />
          <div className="stack" style={{ gap: 4 }}>
            <strong className="dropzone__title">Drop files here</strong>
            <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>
              PDF, Word, PowerPoint, HTML, Markdown, text, CSV and images. Up to 100 MB each.
            </span>
          </div>
          <Button variant="secondary" icon={<FileUp aria-hidden="true" />} loading={upload.isPending} onClick={() => input.current?.click()}>
            Choose files
          </Button>
          <input
            ref={input}
            type="file"
            multiple
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => {
              onFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {errors.length > 0 && (
          <Alert tone="err" title={`${errors.length} ${errors.length === 1 ? "file was" : "files were"} not added`} onDismiss={() => setErrors([])}>
            <ul className="plain-list">
              {errors.map((e) => (
                <li key={e.filename}>
                  <strong>{e.filename}</strong>: {e.error}
                </li>
              ))}
            </ul>
          </Alert>
        )}
        {duplicates.length > 0 && (
          <Alert tone="info" title="Already in this collection" onDismiss={() => setDuplicates([])}>
            {duplicates.join(", ")} {duplicates.length === 1 ? "is" : "are"} unchanged, so {duplicates.length === 1 ? "it was" : "they were"} skipped.
          </Alert>
        )}

        {staged.length > 0 && (
          <div className="staged">
            <div className="staged__head">
              <span className="eyebrow">Review before ingesting</span>
              <span className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>
                {staged[0].estimate?.chunking} chunking
              </span>
            </div>
            <ul className="staged__list">
              {staged.map((d) => (
                <li key={d.id} className="staged__item">
                  <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                    <span className="truncate" style={{ fontWeight: 600 }}>
                      {d.filename}
                    </span>
                    <span className="mono muted staged__facts">
                      {fmtBytes(d.size_bytes)} · {fmtInt(d.estimate?.pages ?? 0)} pages · {fmtInt(d.estimate?.images ?? 0)} figures ·{" "}
                      {fmtInt(d.estimate?.tables ?? 0)} tables · {fmtInt(d.estimate?.llm_calls ?? 0)} model calls
                    </span>
                  </div>
                  <span className="mono num staged__cost" title="Estimated ingestion cost range">
                    {d.estimate?.cost_high_usd ? `${fmtCost(d.estimate.cost_low_usd)}–${fmtCost(d.estimate.cost_high_usd)}` : "$0"}
                  </span>
                  <Button size="sm" variant="ghost" iconOnly icon={<Trash2 aria-hidden="true" />} onClick={() => discard.mutate(d.id)}>
                    Discard {d.filename}
                  </Button>
                </li>
              ))}
            </ul>
            {total.over && (
              <Alert tone="warn" title="This may exceed your ingestion budget">
                Ingestion stops if a document costs more than the limit set in Settings.
              </Alert>
            )}
            <div className="staged__foot">
              <div className="stack" style={{ gap: 0 }}>
                <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                  Estimated total
                </span>
                <strong className="mono">
                  {total.high ? `${fmtCost(total.low)} – ${fmtCost(total.high)}` : "$0"}
                  <span className="muted" style={{ fontWeight: 400 }}> · {fmtInt(total.calls)} model calls</span>
                </strong>
              </div>
              <span className="spacer" />
              <Badge>{staged.length} staged</Badge>
              <Button variant="primary" icon={<Play aria-hidden="true" />} loading={ingest.isPending} onClick={() => ingest.mutate()}>
                Ingest {staged.length} {staged.length === 1 ? "file" : "files"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}
