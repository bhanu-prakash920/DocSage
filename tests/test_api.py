from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from docsage.samples import write_samples


@pytest.fixture()
def client(env):
    from docsage.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def sse(client, url, body):
    events = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200, resp.read()
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_full_workflow(client, tmp_path):
    r = client.post("/api/collections", json={"name": "Reports Q3", "description": "test"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert cid == "reports-q3"

    files = write_samples(tmp_path / "f")
    up = client.post(
        f"/api/collections/{cid}/uploads",
        files=[("files", (p.name, p.read_bytes())) for p in files] + [("files", ("x.exe", b"MZ"))],
    ).json()
    assert len(up["documents"]) == 4 and up["errors"][0]["filename"] == "x.exe"
    assert all(d["status"] == "staged" and d["estimate"]["pages"] >= 1 for d in up["documents"])

    again = client.post(
        f"/api/collections/{cid}/uploads", files=[("files", (files[0].name, files[0].read_bytes()))]
    )
    assert again.json()["documents"][0]["duplicate"] is True

    jobs = client.post(f"/api/collections/{cid}/ingest", json={}).json()
    assert len(jobs) == 4
    docs = client.get(f"/api/collections/{cid}/documents").json()
    assert all(d["status"] == "ready" for d in docs) and "path" not in docs[0]

    events = sse(
        client, f"/api/collections/{cid}/query", {"question": "Who owns the certification delay risk?"}
    )
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and "sources" in kinds and "token" in kinds
    done = events[-1]
    assert "Luis Brandt" in done["answer"]
    conv = done["conversation_id"]

    follow = sse(
        client,
        f"/api/collections/{cid}/query",
        {"question": "And who owns chip supply?", "conversation_id": conv, "modalities": ["table"]},
    )
    assert follow[-1]["type"] == "done"
    assert all(s["modality"] == "table" for s in follow[-1]["sources"])
    turns = client.get(f"/api/conversations/{conv}").json()["turns"]
    assert len(turns) == 2

    doc = next(d for d in docs if d["kind"] == "pdf")
    detail = client.get(f"/api/documents/{doc['id']}").json()
    asset = next(c["asset_path"] for c in detail["chunks"] if c["asset_path"])
    assert client.get(f"/api/files/{asset}").status_code == 200
    assert client.get("/api/files/docsage.sqlite3").status_code == 404

    stats = client.get(f"/api/collections/{cid}/stats").json()
    assert stats["queries"]["total"] == 2 and stats["chunks"]["image"] >= 1

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert len(client.get(f"/api/collections/{cid}/documents").json()) == 3
    assert client.delete(f"/api/collections/{cid}").status_code == 204
    assert client.get(f"/api/collections/{cid}").status_code == 404


def test_query_on_empty_collection_is_a_clear_error(client):
    cid = client.post("/api/collections", json={"name": "Empty"}).json()["id"]
    r = client.post(f"/api/collections/{cid}/query", json={"question": "hi"})
    assert r.status_code == 400 and "no indexed documents" in r.json()["error"]


def test_settings_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["status"]["llm_provider"] == "offline" and "agentic" in s["options"]["chunking"]
    assert client.patch("/api/settings", json={"top_k": 99}).status_code == 400
    assert client.patch("/api/settings", json={"google_api_key": "x"}).status_code == 400
    ok = client.patch("/api/settings", json={"top_k": 8, "rerank": "llm"})
    assert ok.status_code == 200 and ok.json()["values"]["top_k"] == 8


def test_samples_and_eval(client):
    c = client.post("/api/samples").json()
    items = client.get(f"/api/collections/{c['id']}/eval/items").json()
    assert len(items) >= 10
    gen = client.post(f"/api/collections/{c['id']}/eval/items/generate", json={"count": 3})
    assert gen.status_code == 201 and gen.json()
    run = client.post(f"/api/collections/{c['id']}/eval/runs", json={"presets": ["dense", "hybrid"]}).json()
    result = client.get(f"/api/eval/runs/{run['id']}").json()
    assert result["status"] == "succeeded", result
    summary = {s["config"]: s for s in result["results"]["summary"]}
    assert set(summary) == {"dense", "hybrid"}
    assert summary["hybrid"]["hit_rate"] is not None and summary["hybrid"]["hit_rate"] > 0.5


def test_eval_chunking_matrix_uses_shadow_collections(client):
    c = client.post("/api/samples").json()
    run = client.post(
        f"/api/collections/{c['id']}/eval/runs",
        json={"presets": ["hybrid"], "chunking": ["recursive", "semantic"]},
    ).json()
    result = client.get(f"/api/eval/runs/{run['id']}").json()
    assert result["status"] == "succeeded", result
    assert [s["config"] for s in result["results"]["summary"]] == ["recursive / hybrid", "semantic / hybrid"]
    assert [x["id"] for x in client.get("/api/collections").json()] == [c["id"]]


def test_api_token(env):
    from docsage.api.app import create_app
    from docsage.config import configure

    configure(
        data_dir=str(env.data_dir), llm_provider="offline", embedding_provider="hash", api_token="s3cret"
    )
    with TestClient(create_app()) as c:
        assert c.get("/api/collections").status_code == 401
        assert c.get("/api/collections", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/health").json()["auth_required"] is True


def test_cancelled_job(env):
    from docsage import library
    from docsage.jobs import JobRunner, set_runner, submit_ingest
    from docsage.store import get_db

    c = library.create_collection("Cancel me")
    path = write_samples(env.data_dir / "tmp")[0]
    doc = library.stage_file(c["id"], path.name, path.read_bytes())
    runner = JobRunner(synchronous=True)
    set_runner(runner)
    runner._cancelled.add("pending")
    original = runner.is_cancelled
    runner.is_cancelled = lambda job_id: True  # cancel at the first checkpoint
    job = submit_ingest(doc["id"])
    runner.is_cancelled = original
    assert get_db().get_job(job["id"])["status"] == "cancelled"
    assert get_db().get_document(doc["id"])["status"] == "cancelled"
