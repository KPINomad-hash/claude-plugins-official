"""Append-only call journal (JSON lines) kept in <workdir>/journal/<program>.jsonl."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, workdir: Path, program: str):
        self.path = workdir / "journal" / f"{program}.jsonl"
        self._lock = threading.Lock()

    def record(self, tool: str, args: dict[str, Any], outcome: str, **extra: Any) -> None:
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "args": args,
            "outcome": outcome,
            **{k: v for k, v in extra.items() if v is not None},
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass  # journalling must never break a tool call
