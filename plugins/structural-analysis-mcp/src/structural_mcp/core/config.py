"""Settings read from environment variables.

STRUCT_MCP_WORKDIR         Folder the servers may write to (working copies, exports,
                           journal). Default: ~/StructuralMCP
STRUCT_MCP_MODEL_DIRS      Extra folders models may be READ from (copied out of),
                           separated by the OS path separator (";" on Windows).
STRUCT_MCP_ALLOW_ANALYSIS  "1" enables run_analysis (working copies only). Default off.
STRUCT_MCP_TIMEOUT         Seconds to wait for an ordinary API call. Default 120.
STRUCT_MCP_ANALYSIS_TIMEOUT Seconds an analysis job may run before it is reported
                           as overdue (it is never killed). Default 3600.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    workdir: Path
    model_dirs: list[Path] = field(default_factory=list)
    allow_analysis: bool = False
    timeout: float = 120.0
    analysis_timeout: float = 3600.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        workdir = Path(env.get("STRUCT_MCP_WORKDIR") or Path.home() / "StructuralMCP")
        dirs = [Path(p) for p in (env.get("STRUCT_MCP_MODEL_DIRS") or "").split(os.pathsep) if p.strip()]
        return cls(
            workdir=workdir,
            model_dirs=dirs,
            allow_analysis=_truthy(env.get("STRUCT_MCP_ALLOW_ANALYSIS")),
            timeout=float(env.get("STRUCT_MCP_TIMEOUT") or 120),
            analysis_timeout=float(env.get("STRUCT_MCP_ANALYSIS_TIMEOUT") or 3600),
        )

    @property
    def access_mode(self) -> str:
        return "read+analysis-on-working-copies" if self.allow_analysis else "read-only"
