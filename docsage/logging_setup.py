from __future__ import annotations

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    root = logging.getLogger()
    if getattr(root, "_docsage_configured", False):
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if as_json
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    )
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in (
        "httpx",
        "httpx2",
        "uvicorn.access",
        "chromadb",
        "urllib3",
        "sentence_transformers",
        "google_genai",
        "anthropic",
        "openai",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._docsage_configured = True  # type: ignore[attr-defined]
