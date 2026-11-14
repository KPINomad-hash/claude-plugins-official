"""SAPFIR-3D / LIRA-CAD adapter.

What is documented for external automation (tutorial 2017 ch. 10, unchanged in
2022) is small: ProgID `Sapfir.Application.7.0`, `Application.GetActiveDoc()`,
`Document.GetActiveStorey()` and a few model-creation calls; the full object
model lives in SAPFIR.tlb on the workstation. So this adapter offers:

* COM route — attach, report what is registered, list the members the type
  library exposes (`describe_api`) and call parameterless getters
  (`read_property`) so the real API can be established on the installed build
  without changing the model;
* file route — read an IFC export of the model (storeys, elements per storey)
  for `get_analytical_model_info` and for hand-over checks against VISOR.

Nothing here creates or edits model objects.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..core import errors, sandbox, winreg_probe
from ..core.adapter import Adapter, ResultSet
from ..core.config import Settings
from ..core.errors import AdapterError
from .ifc import read_ifc

DOCUMENTED_PROGID = "Sapfir.Application.7.0"
PROGID_PREFIXES = ("sapfir.", "liracad.", "lira-cad.")
_GETTER = re.compile(r"^(Get|Is|Has|Count)\w*$")
_SCALARS = (str, int, float, bool, type(None))


class SapfirAdapter(Adapter):
    program = "sapfir"
    title = "SAPFIR-3D / LIRA-CAD"
    model_extensions = (".spf",)
    supports_open = False
    supports_analysis = False
    load_case_kinds = ()
    result_types = {}
    element_types = {
        "storey": "storeys of the loaded IFC export (name, elevation, element counts)",
        "column": "columns in the IFC export", "beam": "beams", "slab": "slabs", "wall": "walls",
        "member": "generic bar members", "plate": "plates", "footing": "footings", "pile": "piles",
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._app: Any = None
        self._progid: str | None = None
        self._ifc: dict[str, Any] | None = None
        self._ifc_path: Path | None = None

    # ---- connection -------------------------------------------------------------
    def _progids(self) -> list[str]:
        configured = os.environ.get("STRUCT_MCP_SAPFIR_PROGID")
        found = [p for p in winreg_probe.find_progids(PROGID_PREFIXES) if "application" in p.lower()]
        ordered = ([configured] if configured else []) + sorted(found, reverse=True) + [DOCUMENTED_PROGID]
        return list(dict.fromkeys(ordered))

    def probe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "documented_progid": DOCUMENTED_PROGID,
            "com": [winreg_probe.progid_info(p) for p in self._progids()],
            "registered_progids": winreg_probe.find_progids(PROGID_PREFIXES),
            "api_status": "External COM API is only partly documented; use sapfir_describe_api on this "
                          "workstation to list the real members before relying on any call.",
        }
        hints = []
        for base in (os.environ.get("ProgramData"), os.environ.get("PUBLIC")):
            if not base:
                continue
            for pattern in ("Sapfir/*/Service/const_model.jsh", "Documents/SAPFIR/*/SDK Plugin",
                            "LIRA SAPR/*/SDK*", "Documents/LIRA SAPR/*/SDK*"):
                hints += [str(p) for p in Path(base).glob(pattern)]
        info["sdk_and_script_folders_found"] = hints
        try:
            import win32com.client  # type: ignore  # noqa: F401

            info["pywin32"] = True
        except ImportError:
            info["pywin32"] = False
            info["note"] = "Package 'pywin32' is not installed; the COM route is unavailable."
        return info

    def connect(self, model_path: str | None = None, start_if_needed: bool = False) -> dict[str, Any]:
        try:
            import win32com.client  # type: ignore
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "Package 'pywin32' is required (Windows only).",
                               "Install with: pip install pywin32") from None
        tried: list[str] = []
        for progid in self._progids():
            try:
                self._app = win32com.client.GetActiveObject(progid)
                self._progid = progid
                return {"attached": True, "progid": progid, "how": "running instance (GetActiveObject)"}
            except Exception as exc:  # noqa: BLE001 - pywintypes.com_error
                tried.append(f"{progid}: {exc}")
        if start_if_needed:
            for progid in self._progids():
                try:
                    # For an out-of-process server Dispatch starts it (or reuses one that
                    # registers a class factory); which of the two SAPFIR does is not documented.
                    self._app = win32com.client.Dispatch(progid)
                    self._progid = progid
                    return {"attached": True, "progid": progid, "how": "Dispatch (may have started SAPFIR)"}
                except Exception as exc:  # noqa: BLE001
                    tried.append(f"Dispatch {progid}: {exc}")
        raise AdapterError(
            errors.NOT_CONNECTED, "Could not attach to SAPFIR / LIRA-CAD through COM.",
            "SAPFIR may not register itself as a running object; retry with start_if_needed=true, "
            "check sapfir_get_capabilities for registered ProgIDs, or set STRUCT_MCP_SAPFIR_PROGID. "
            "Tried: " + " | ".join(tried[-6:]),
        )

    def is_connected(self) -> bool:
        return self._app is not None

    def _a(self) -> Any:
        if self._app is None:
            raise AdapterError(errors.NOT_CONNECTED, "Not attached to SAPFIR.", "Call sapfir_connect first.")
        return self._app

    def program_info(self) -> dict[str, Any]:
        return {"name": self.title, "progid": self._progid,
                "version": "not reported by the documented API; see ProgID / server path in capabilities"}

    def model_path(self) -> str | None:
        return str(self._ifc_path) if self._ifc_path else None

    # ---- COM exploration (read-only) --------------------------------------------
    def _walk(self, path: list[str]) -> Any:
        obj = self._a()
        for step in path:
            if not _GETTER.match(step):
                raise AdapterError(errors.NOT_ALLOWED, f"'{step}' is not a getter.",
                                   "Only parameterless members named Get*/Is*/Has*/Count* may be called; "
                                   "this server does not create or edit SAPFIR objects.")
            try:
                value = getattr(obj, step)
                obj = value() if callable(value) else value
            except AdapterError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AdapterError(errors.API_ERROR, f"Calling '{step}' failed: {exc}.",
                                   "List the available members with sapfir_describe_api.") from None
            if obj is None:
                raise AdapterError(errors.NO_MODEL, f"'{step}' returned nothing (no active document/storey?).")
        return obj

    def describe_api(self, path: list[str] | None = None) -> dict[str, Any]:
        obj = self._walk(path or [])
        members = winreg_probe.com_members(obj)
        return {"object": "Application" + "".join(f".{s}()" for s in path or []), "member_count": len(members),
                "members": members,
                "note": None if members else "The object exposes no type information to late binding; "
                        "generate wrappers from SAPFIR.tlb (python -m win32com.client.makepy) instead."}

    def read_property(self, path: list[str]) -> dict[str, Any]:
        if not path:
            raise AdapterError(errors.INVALID_INPUT, "path must contain at least one member name.",
                               "Example: ['GetActiveDoc', 'GetActiveStorey'].")
        value = self._walk(path)
        if isinstance(value, _SCALARS):
            return {"path": path, "kind": "value", "value": value}
        if isinstance(value, (list, tuple)) and all(isinstance(v, _SCALARS) for v in value):
            return {"path": path, "kind": "list", "value": list(value)[:1000]}
        return {"path": path, "kind": "object", "members": [m["name"] for m in winreg_probe.com_members(value)]}

    # ---- file route -------------------------------------------------------------
    def load_ifc_export(self, ifc_path: str) -> dict[str, Any]:
        roots = [self.settings.workdir, *self.settings.model_dirs]
        path = sandbox.require_inside(ifc_path, roots, "reading models")
        if path.suffix.lower() != ".ifc" or not path.is_file():
            raise AdapterError(errors.INVALID_INPUT, f"'{path}' is not an existing .ifc file.",
                               "Export the model from SAPFIR as IFC into an allowed folder first.")
        self._ifc, self._ifc_path = read_ifc(path), path
        return {"loaded": str(path), **self.get_analytical_model_info()}

    def _need_ifc(self) -> dict[str, Any]:
        if self._ifc is None:
            raise AdapterError(errors.NO_MODEL, "No IFC export is loaded.",
                               "In SAPFIR export the model to IFC, then call sapfir_load_ifc_export.")
        return self._ifc

    def get_analytical_model_info(self) -> dict[str, Any]:
        ifc = self._need_ifc()
        diagnostics = []
        if ifc["not_assigned_to_storey"]:
            diagnostics.append(f"{ifc['not_assigned_to_storey']} element(s) are not assigned to any storey.")
        if not ifc["storeys"]:
            diagnostics.append("The file contains no IfcBuildingStorey.")
        return {
            "source": "IFC export (file route)", "ifc_schema": ifc["schema"],
            "storeys": [{k: s[k] for k in ("name", "elevation", "elements")} for s in ifc["storeys"]],
            "element_totals": ifc["totals"],
            "analytical_model_state": "not readable: the IFC export describes the physical model; the "
                                      "analytical/FE model state is only visible in SAPFIR or after transfer "
                                      "to VISOR (compare with lira_get_model_info).",
            "diagnostics": diagnostics,
        }

    def model_info(self) -> dict[str, Any]:
        com: dict[str, Any] = {"attached": self._app is not None}
        if self._app is not None:
            for label, path in (("active_document", ["GetActiveDoc"]),
                                ("active_storey", ["GetActiveDoc", "GetActiveStorey"])):
                try:
                    self._walk(path)
                    com[label] = "present"
                except AdapterError as exc:
                    com[label] = f"unavailable: {exc.message}"
        info: dict[str, Any] = {"com": com, "units": {"length": "as written in the IFC file (SAPFIR works in m)"}}
        info["ifc"] = self.get_analytical_model_info() if self._ifc else "no IFC export loaded"
        return info

    def element_ids(self, element_type: str) -> list[str]:
        ifc = self._need_ifc()
        if element_type == "storey":
            return [s["id"] for s in ifc["storeys"]]
        return [e["id"] for e in ifc["elements"] if e["type"] == element_type]

    def describe_elements(self, element_type: str, ids: list[str]) -> list[dict[str, Any]]:
        ifc = self._need_ifc()
        pool = ifc["storeys"] if element_type == "storey" else ifc["elements"]
        by_id = {item["id"]: item for item in pool}
        return [by_id[i] for i in ids]

    def list_load_cases(self, kind: str) -> list[dict[str, Any]]:
        raise AdapterError(errors.UNSUPPORTED, "SAPFIR load cases are not readable through this server.")

    def get_results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                    options: dict[str, Any]) -> ResultSet:
        raise AdapterError(errors.UNSUPPORTED, "SAPFIR holds no analysis results; read them from LIRA-SAPR.")

    def extra_tools(self) -> dict[str, Any]:
        return {
            "describe_api": (self.describe_api,
                             "List the methods/properties the installed SAPFIR exposes on the Application "
                             "object or on an object reached through getters, e.g. path=['GetActiveDoc'] or "
                             "['GetActiveDoc','GetActiveStorey']. Read-only; use it to establish the real "
                             "API of this build.", None),
            "read_property": (self.read_property,
                              "Call a chain of parameterless getters (names starting Get/Is/Has/Count) from "
                              "the Application object and return the value, or the member names when the "
                              "result is an object. Anything else is refused.", None),
            "load_ifc_export": (self.load_ifc_export,
                                "Read an IFC file exported from SAPFIR (inside an allowed folder): storeys, "
                                "elevations, element counts per storey. Required before sapfir_list_elements "
                                "and sapfir_get_analytical_model_info.", None),
            "get_analytical_model_info": (self.get_analytical_model_info,
                                          "Storeys, element groups and hand-over diagnostics of the loaded IFC "
                                          "export. Compare the totals with lira_get_model_info after the model "
                                          "is transferred to VISOR.", None),
        }
