"""Shared MCP layer: one tool set, registered with a program prefix for each adapter."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import __version__
from . import errors, sandbox, winreg_probe
from .adapter import Adapter
from .comthread import ComThread
from .config import Settings
from .errors import AdapterError
from .export import write_rows
from .journal import Journal

MAX_LIMIT = 500

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Changes program state (which model is open / attached) but never model content.
STATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_FILE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def paginate(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    if offset < 0 or limit < 1:
        raise AdapterError(errors.INVALID_INPUT, "offset must be >= 0 and limit >= 1.")
    limit = min(limit, MAX_LIMIT)
    page = items[offset:offset + limit]
    end = offset + len(page)
    return {
        "total": len(items),
        "offset": offset,
        "count": len(page),
        "has_more": end < len(items),
        "next_offset": end if end < len(items) else None,
        "items": page,
    }


class Service:
    """Program-independent behaviour behind the tools; testable without MCP."""

    def __init__(self, adapter: Adapter, settings: Settings, com: ComThread | None = None):
        self.adapter = adapter
        self.settings = settings
        self.com = com or ComThread(f"{adapter.program}-com")
        self.journal = Journal(settings.workdir, adapter.program)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._result_cache: tuple[tuple, dict[str, Any]] | None = None

    # ---- plumbing ---------------------------------------------------------------
    def _call(self, fn: Callable[[], Any], label: str, timeout: float | None = None) -> Any:
        return self.com.call(fn, label, timeout or self.settings.timeout)

    def _revision(self) -> dict[str, Any]:
        """Which saved file state the answers refer to."""
        path = self._call(self.adapter.model_path, "model_path")
        rev = sandbox.file_revision(path) if path else None
        return {
            "model_path": path,
            "revision": rev,
            "is_working_copy": bool(path) and sandbox.is_inside(path, self.settings.workdir / "copies"),
            "note": None if rev else "Model is not saved to disk; no revision id can be given.",
        }

    def _read_roots(self) -> list[Path]:
        return [self.settings.workdir, *self.settings.model_dirs]

    # ---- tools ------------------------------------------------------------------
    def get_capabilities(self) -> dict[str, Any]:
        a = self.adapter
        probe = self._call(a.probe, "probe")
        connected = self._call(a.is_connected, "is_connected")
        info = self._call(a.program_info, "program_info") if connected else None
        return {
            "server": {"name": f"{a.program}_mcp", "version": __version__},
            "program": a.title,
            "connected": connected,
            "program_info": info,
            "access_mode": self.settings.access_mode,
            "folders": {"workdir": str(self.settings.workdir),
                        "readable_model_dirs": [str(p) for p in self._read_roots()]},
            "element_types": a.element_types,
            "result_types": a.result_types,
            "load_case_kinds": list(a.load_case_kinds),
            "operations": {
                "open_working_copy": a.supports_open,
                "run_analysis": a.supports_analysis and self.settings.allow_analysis,
                "run_analysis_note": None if self.settings.allow_analysis else
                "Disabled. Set STRUCT_MCP_ALLOW_ANALYSIS=1 to allow analysis of working copies.",
                "modify_model": False,
            },
            "workstation": {**winreg_probe.environment(), **probe},
        }

    def connect(self, model_path: str | None, start_if_needed: bool) -> dict[str, Any]:
        result = self._call(lambda: self.adapter.connect(model_path, start_if_needed), "connect")
        self._result_cache = None
        return {**result, **self._revision()}

    def open_working_copy(self, source_path: str) -> dict[str, Any]:
        a = self.adapter
        if not a.supports_open:
            raise AdapterError(errors.UNSUPPORTED, f"{a.title}: opening a file through the API is not available.",
                               "Open a copy of the model in the program yourself, then call connect.")
        source = sandbox.require_inside(source_path, self._read_roots(), "reading models")
        if source.suffix.lower() not in a.model_extensions:
            raise AdapterError(errors.INVALID_INPUT, f"'{source.name}' is not a {a.title} model.",
                               f"Expected extension: {', '.join(a.model_extensions)}.")
        source_rev = sandbox.file_revision(source)
        copy = sandbox.make_working_copy(source, self.settings.workdir, a.program, a.companion_extensions)
        if not self._call(a.is_connected, "is_connected"):
            self._call(lambda: a.connect(None, True), "connect")
        self._call(lambda: a.open_model(copy), "open_model")
        self._result_cache = None
        return {"source": str(source), "source_revision": source_rev, "working_copy": str(copy), **self._revision()}

    def get_model_info(self) -> dict[str, Any]:
        info = self._call(self.adapter.model_info, "model_info")
        return {"program": self._call(self.adapter.program_info, "program_info"), **self._revision(), **info}

    def list_elements(self, element_type: str, offset: int, limit: int, ids: list[str] | None) -> dict[str, Any]:
        a = self.adapter
        a.check_choice(element_type, a.element_types, "element_type")
        all_ids = self._call(lambda: a.element_ids(element_type), "element_ids")
        diagnostics: list[str] = []
        if ids:
            known = set(all_ids)
            missing = [i for i in ids if i not in known]
            if missing:
                diagnostics.append(f"Not found in model: {', '.join(missing[:20])}")
            all_ids = [i for i in ids if i in known]
        page = paginate(all_ids, offset, limit)
        page["items"] = self._call(lambda: a.describe_elements(element_type, page["items"]), "describe_elements")
        return {"element_type": element_type, **page, "diagnostics": diagnostics, **self._revision()}

    def list_load_cases(self, kind: str) -> dict[str, Any]:
        a = self.adapter
        a.check_choice(kind, a.load_case_kinds, "kind")
        items = self._call(lambda: a.list_load_cases(kind), "list_load_cases")
        return {"kind": kind, "count": len(items), "items": items, **self._revision()}

    def _results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                 options: dict[str, Any]) -> dict[str, Any]:
        a = self.adapter
        a.check_choice(result_type, a.result_types, "result_type")
        revision = self._revision()
        key = (result_type, tuple(load_cases), tuple(element_ids or ()), tuple(sorted(options.items())),
               (revision["revision"] or {}).get("id"), revision["model_path"])
        if self._result_cache and self._result_cache[0] == key and revision["revision"]:
            return self._result_cache[1]
        rs = self._call(lambda: a.get_results(result_type, load_cases, element_ids, options), "get_results")
        data = {
            "result_type": result_type,
            "load_cases": load_cases,
            "units": rs.units,
            "coordinate_system": rs.coordinate_system,
            "diagnostics": rs.diagnostics,
            "rows": rs.rows,
            **revision,
        }
        self._result_cache = (key, data)
        return data

    def get_results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                    options: dict[str, Any], offset: int, limit: int) -> dict[str, Any]:
        data = dict(self._results(result_type, load_cases, element_ids, options))
        page = paginate(data.pop("rows"), offset, limit)
        page["rows"] = page.pop("items")
        return {**data, **page}

    def export_results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                       options: dict[str, Any], file_format: str, file_name: str | None) -> dict[str, Any]:
        if file_format not in ("csv", "xlsx"):
            raise AdapterError(errors.INVALID_INPUT, f"Unsupported format '{file_format}'.", "Use csv or xlsx.")
        data = self._results(result_type, load_cases, element_ids, options)
        stem = sandbox.safe_name(Path(file_name).stem if file_name else
                                 f"{self.adapter.program}_{result_type}_{time.strftime('%Y%m%d-%H%M%S')}")
        exports = self.settings.workdir / "exports"
        target = sandbox.require_inside(exports / f"{stem}.{file_format}", [exports], "exports")
        if target.exists():
            raise AdapterError(errors.NOT_ALLOWED, f"'{target.name}' already exists; exports never overwrite.",
                               "Choose another file_name.")
        notes = {"program": self.adapter.title, "result_type": result_type, "load_cases": ", ".join(load_cases),
                 "units": data["units"], "coordinate_system": data["coordinate_system"],
                 "model_path": data["model_path"], "revision": data["revision"],
                 "diagnostics": " | ".join(data["diagnostics"])}
        count = write_rows(target, data["rows"], notes)
        return {"path": str(target), "rows_written": count, "units": data["units"],
                "coordinate_system": data["coordinate_system"], "diagnostics": data["diagnostics"],
                "model_path": data["model_path"], "revision": data["revision"]}

    def run_analysis(self, load_cases: list[str] | None) -> dict[str, Any]:
        a = self.adapter
        if not a.supports_analysis:
            raise AdapterError(errors.UNSUPPORTED, f"{a.title}: running analysis is not implemented in this server.")
        if not self.settings.allow_analysis:
            raise AdapterError(errors.NOT_ALLOWED, "Running analysis is disabled (read-only mode).",
                               "Set STRUCT_MCP_ALLOW_ANALYSIS=1 in the server environment.")
        before = self._revision()
        if not before["is_working_copy"]:
            raise AdapterError(errors.NOT_ALLOWED,
                               "Analysis is only run on working copies; the open model is not one.",
                               "Call open_working_copy with the source model first.")
        job_id = uuid.uuid4().hex[:12]
        future = self.com.submit(lambda: a.run_analysis(load_cases), f"run_analysis:{job_id}")
        self.jobs[job_id] = {"future": future, "started": time.time(), "load_cases": load_cases,
                             "revision_before": before}
        self._result_cache = None
        return {"job_id": job_id, "state": "running", **before}

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise AdapterError(errors.INVALID_INPUT, f"Unknown job_id '{job_id}'.",
                               f"Known jobs: {', '.join(self.jobs) or 'none in this server session'}.")
        future: Future = job["future"]
        elapsed = round(time.time() - job["started"], 1)
        out: dict[str, Any] = {"job_id": job_id, "elapsed_seconds": elapsed, "load_cases": job["load_cases"]}
        if not future.done():
            overdue = elapsed > self.settings.analysis_timeout
            out["state"] = "running_overdue" if overdue else "running"
            if overdue:
                out["note"] = ("Exceeded STRUCT_MCP_ANALYSIS_TIMEOUT. The solver was NOT stopped; "
                               "check the program window (it may be waiting on a dialog).")
            return out
        exc = future.exception()
        if exc is not None:
            out.update(state="failed", error=str(exc))
            return out
        out.update(state="finished", outcome=future.result(), **self._revision())
        out["note"] = "Results now refer to the revision above; confirm results state with get_model_info."
        return out


def build_server(adapter: Adapter, settings: Settings | None = None, instructions_extra: str = "") -> MCPServer:
    settings = settings or Settings.from_env()
    svc = Service(adapter, settings)
    p = adapter.program
    t = adapter.title
    mcp = MCPServer(
        f"{p}_mcp",
        version=__version__,
        instructions=(
            f"Read access to structural models open in {t} on this Windows workstation. "
            f"Start with {p}_get_capabilities, then {p}_connect (or {p}_open_working_copy), "
            f"then {p}_get_model_info. Every answer carries units, coordinate system and the "
            "model revision it refers to; quote them with any number you report. Row status "
            "'no_result' means the program has no value, 'error' means the API failed; neither "
            "is a zero. " + instructions_extra
        ),
    )

    async def run(tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        try:
            result = await anyio.to_thread.run_sync(fn)
        except AdapterError as exc:
            svc.journal.record(tool, args, f"error:{exc.code}", message=exc.message)
            raise ToolError(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - surface API failures with context
            svc.journal.record(tool, args, "error:unexpected", message=repr(exc))
            raise ToolError(f"[{errors.API_ERROR}] {t} API call failed in {tool}: {exc!r}. "
                            f"Hint: run {p}_get_capabilities to check the connection and program state.") from None
        extra = {}
        if isinstance(result, dict):
            extra = {"model_path": result.get("model_path"), "revision": (result.get("revision") or {}).get("id")
                     if isinstance(result.get("revision"), dict) else None}
        svc.journal.record(tool, args, "ok", **extra)
        return result

    elem_doc = "; ".join(f"'{k}' — {v}" for k, v in adapter.element_types.items())
    res_doc = "; ".join(f"'{k}' — {v}" for k, v in adapter.result_types.items())

    @mcp.tool(name=f"{p}_get_capabilities", annotations=READ,
              description=f"What this server can do with {t} here: whether the program is attached, its "
                          "version/build, access mode, allowed folders, supported element and result types, "
                          "and a workstation check (COM registrations, bitness, installed API packages). "
                          "Safe to call when the program is not running.")
    async def get_capabilities() -> dict[str, Any]:
        return await run(f"{p}_get_capabilities", {}, svc.get_capabilities)

    @mcp.tool(name=f"{p}_connect", annotations=STATE,
              description=f"Attach to a running {t}. model_path selects the instance holding that file when "
                          "several are open (where the program supports it). start_if_needed=true starts the "
                          "program if it is not running. Does not open or change any model.")
    async def connect(model_path: str | None = None, start_if_needed: bool = False) -> dict[str, Any]:
        return await run(f"{p}_connect", {"model_path": model_path, "start_if_needed": start_if_needed},
                         lambda: svc.connect(model_path, start_if_needed))

    if adapter.supports_open:
        @mcp.tool(name=f"{p}_open_working_copy", annotations=STATE,
                  description=f"Copy a {t} model ({', '.join(adapter.model_extensions)}) from an allowed folder "
                              "into the server's working folder and open the COPY in the program. The source "
                              "file is never opened or modified. Returns the copy's path and revision. Note: "
                              "the program switches to the copy; unsaved work in the model currently open "
                              "there should be saved by the user first.")
        async def open_working_copy(source_path: str) -> dict[str, Any]:
            return await run(f"{p}_open_working_copy", {"source_path": source_path},
                             lambda: svc.open_working_copy(source_path))

    @mcp.tool(name=f"{p}_get_model_info", annotations=READ,
              description="The model currently open: file path, revision (content hash of the saved file), "
                          "units, object counts, and whether analysis results exist and for which cases.")
    async def get_model_info() -> dict[str, Any]:
        return await run(f"{p}_get_model_info", {}, svc.get_model_info)

    @mcp.tool(name=f"{p}_list_elements", annotations=READ,
              description=f"List model objects with their definition. element_type: {elem_doc}. "
                          f"Paginated: offset/limit (limit <= {MAX_LIMIT}); use next_offset while has_more. "
                          "ids restricts the listing to the given identifiers.")
    async def list_elements(element_type: str, offset: int = 0, limit: int = 100,
                            ids: list[str] | None = None) -> dict[str, Any]:
        return await run(f"{p}_list_elements", {"element_type": element_type, "offset": offset, "limit": limit,
                                               "ids": ids},
                         lambda: svc.list_elements(element_type, offset, limit, ids))

    if adapter.load_case_kinds:
        @mcp.tool(name=f"{p}_list_load_cases", annotations=READ,
                  description=f"Load cases and combinations. kind: {', '.join(adapter.load_case_kinds)}. Returns "
                              "id/name, type, combination contents (case + factor) and analysis status where the "
                              "program reports it.")
        async def list_load_cases(kind: str = "all") -> dict[str, Any]:
            return await run(f"{p}_list_load_cases", {"kind": kind}, lambda: svc.list_load_cases(kind))

    if adapter.result_types:
        @mcp.tool(name=f"{p}_get_results", annotations=READ,
                  description=f"Analysis results as typed rows. result_type: {res_doc}. load_cases: names/ids "
                              f"from {p}_list_load_cases (may be empty for modal result types). element_ids: "
                              "restrict to these nodes/members; omit for all. options: program-specific "
                              f"switches listed in {p}_get_capabilities. Paginated with offset/limit. Each "
                              "response states units and coordinate system.")
        async def get_results(result_type: str, load_cases: list[str] | None = None,
                              element_ids: list[str] | None = None, options: dict[str, Any] | None = None,
                              offset: int = 0, limit: int = 100) -> dict[str, Any]:
            args = {"result_type": result_type, "load_cases": load_cases, "element_ids": element_ids,
                    "options": options, "offset": offset, "limit": limit}
            return await run(f"{p}_get_results", args,
                             lambda: svc.get_results(result_type, load_cases or [], element_ids, options or {},
                                                     offset, limit))

        @mcp.tool(name=f"{p}_export_results", annotations=WRITE_FILE,
                  description=f"Write the full result set (same arguments as {p}_get_results) to a CSV "
                              "(';'-separated, UTF-8 with BOM, plus a .meta.txt with units/axes/revision) or "
                              "XLSX file in <workdir>/exports. Never overwrites. Returns the path and row count.")
        async def export_results(result_type: str, load_cases: list[str] | None = None,
                                 element_ids: list[str] | None = None, options: dict[str, Any] | None = None,
                                 file_format: str = "csv", file_name: str | None = None) -> dict[str, Any]:
            args = {"result_type": result_type, "load_cases": load_cases, "element_ids": element_ids,
                    "options": options, "file_format": file_format, "file_name": file_name}
            return await run(f"{p}_export_results", args,
                             lambda: svc.export_results(result_type, load_cases or [], element_ids,
                                                        options or {}, file_format, file_name))

    if adapter.supports_analysis:
        @mcp.tool(name=f"{p}_run_analysis", annotations=STATE,
                  description="Start the analysis of the open model as a background job. Allowed only when "
                              "STRUCT_MCP_ALLOW_ANALYSIS=1 and the open model is a working copy made by "
                              f"{p}_open_working_copy. load_cases limits the run where the program supports "
                              f"it. Returns job_id; poll {p}_get_job_status. Other tools answer 'busy' while "
                              "the solver runs.")
        async def run_analysis(load_cases: list[str] | None = None) -> dict[str, Any]:
            return await run(f"{p}_run_analysis", {"load_cases": load_cases}, lambda: svc.run_analysis(load_cases))

        @mcp.tool(name=f"{p}_get_job_status", annotations=READ,
                  description="State of an analysis job: running, running_overdue (time limit passed, solver "
                              "NOT stopped), finished (with the program's return information) or failed.")
        async def get_job_status(job_id: str) -> dict[str, Any]:
            return await run(f"{p}_get_job_status", {"job_id": job_id}, lambda: svc.get_job_status(job_id))

    for name, (fn, description, annotations) in adapter.extra_tools().items():
        def make(fn: Callable[..., Any] = fn, name: str = name) -> Callable[..., Any]:
            import functools
            import inspect

            @functools.wraps(fn)
            async def tool(**kwargs: Any) -> Any:
                def invoke() -> Any:
                    out = svc._call(lambda: fn(**kwargs), name)
                    return {**out, **svc._revision()} if isinstance(out, dict) else out
                return await run(f"{p}_{name}", kwargs, invoke)

            tool.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
            return tool

        mcp.tool(name=f"{p}_{name}", annotations=annotations or READ, description=description)(make())

    return mcp
