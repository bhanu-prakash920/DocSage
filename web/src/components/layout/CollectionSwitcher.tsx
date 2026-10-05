import { useMutation } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Database, Plus } from "lucide-react";
import { useId, useState } from "react";
import { api } from "../../lib/api";
import { useApp } from "../../lib/app";
import { fmtInt } from "../../lib/format";
import { Button } from "../ui/Button";
import { Modal, Popover } from "../ui/Overlay";
import { ErrorAlert, Field } from "../ui/primitives";
import { useToast } from "../ui/Toast";

export function CollectionSwitcher({ compact }: { compact?: boolean }) {
  const { collections, current, setCurrent } = useApp();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="switcher"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Database aria-hidden="true" />
        <span className="stack" style={{ gap: 0, minWidth: 0, flex: 1, textAlign: "left" }}>
          <span className="eyebrow">Collection</span>
          <span className="switcher__name truncate">{current?.name ?? "No collection"}</span>
        </span>
        {!compact && current && (
          <span className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>
            {fmtInt(current.ready_documents ?? 0)} docs
          </span>
        )}
        <ChevronsUpDown aria-hidden="true" />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} label="Switch collection" width={300}>
        <div className="stack" style={{ gap: 2 }}>
          {collections.map((c) => (
            <button
              key={c.id}
              type="button"
              className="menu-item"
              aria-current={c.id === current?.id}
              onClick={() => {
                setCurrent(c.id);
                setOpen(false);
              }}
            >
              {c.id === current?.id ? <Check aria-hidden="true" /> : <span style={{ width: 16 }} />}
              <span className="truncate" style={{ flex: 1 }}>
                {c.name}
              </span>
              <span className="mono muted" style={{ fontSize: "var(--fs-2xs)" }}>
                {fmtInt(c.ready_documents ?? 0)}/{fmtInt(c.documents ?? 0)}
              </span>
            </button>
          ))}
          {collections.length > 0 && <div className="menu-sep" />}
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              setOpen(false);
              setCreating(true);
            }}
          >
            <Plus aria-hidden="true" />
            New collection
          </button>
        </div>
      </Popover>
      <CreateCollectionModal open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

export function CreateCollectionModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { setCurrent, refreshCollections } = useApp();
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const nameId = useId();
  const descId = useId();
  const create = useMutation({
    mutationFn: () => api.createCollection(name.trim(), description.trim()),
    onSuccess: (c) => {
      refreshCollections();
      setCurrent(c.id);
      toast("ok", "Collection created", `“${c.name}” is ready for documents.`);
      setName("");
      setDescription("");
      onClose();
    },
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New collection"
      description="A collection is a separate knowledge base with its own index, conversations and evaluation set."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>
            Create collection
          </Button>
        </>
      }
    >
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        {create.error && <ErrorAlert error={create.error} title="Could not create the collection" />}
        <Field label="Name" htmlFor={nameId} hint="For example: Q3 board pack, Product specs, Research papers.">
          <input id={nameId} className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} data-autofocus required />
        </Field>
        <Field label="Description" htmlFor={descId} hint="Optional. Shown in the library header.">
          <textarea id={descId} className="textarea" rows={3} maxLength={400} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
