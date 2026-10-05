from __future__ import annotations

from docsage.cli import main
from docsage.samples import write_samples


def test_cli_ingest_dry_run_ask_and_eval(env, tmp_path, capsys):
    folder = tmp_path / "docs"
    write_samples(folder)
    data = str(env.data_dir)
    assert main(["--data-dir", data, "ingest", str(folder), "--dry-run"]) == 0
    assert "Nothing was ingested" in capsys.readouterr().out
    assert main(["--data-dir", data, "ingest", str(folder), "-c", "cli", "--chunking", "recursive"]) == 0
    assert main(["--data-dir", data, "ingest", str(folder), "-c", "cli"]) == 0
    assert "Unchanged, skipped" in capsys.readouterr().out
    assert main(["--data-dir", data, "ask", "-c", "cli", "-q", "What is the rollback command?"]) == 0
    assert "deployctl" in capsys.readouterr().out
    assert main(["--data-dir", data, "collections"]) == 0
    assert main(["--data-dir", data, "status"]) == 0
    golden = tmp_path / "golden.json"
    golden.write_text(
        '[{"question": "What is the SEV1 response time?", "reference_answer": "5 minutes", '
        '"expected_document": "incident-runbook.md"}]'
    )
    out = tmp_path / "results"
    assert (
        main(
            [
                "--data-dir",
                data,
                "eval",
                "-c",
                "cli",
                "--presets",
                "dense,hybrid",
                "--golden",
                str(golden),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert list(out.glob("*.md")) and list(out.glob("*.json"))
