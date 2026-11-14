"""LIRA-SAPR / LIRA-FEM adapter (pywin32).

API facts follow the type libraries of LIRA-SAPR 2024 R2 as documented and
exercised by the open-source EvgenVer/lira-sapr-mcp project:

* VISOR: ProgID `LiraSapr.Application[.<year>]` (2024) or
  `LiraSapphire.Application[.<year>]` (2025+); documents, input tables
  (`doc.AllTables.CreateNewItem` -> `ILiraTable.GetContents`), analysis start
  with completion reported through COM events.
* Results: in-process `LiraResAPI.dll`, coclass `LiraResultsAccess` (no ProgID),
  request/response objects bound to the model by NAME (file stem); files are
  read from LIRA's Work folder. Results come in fixed units: m, rad, tf, tf*m.

Things worth knowing when reading the output:
* reading an input table creates a temporary table in the document and deletes
  it again; LIRA then flags the document as modified although nothing changed;
* results are keyed by model name only — a same-named model analysed from
  another folder replaces them. The adapter compares node counts as a check;
* the API has no support-reaction request; reactions of elastic supports are
  in `element_forces` of the special elements (KE 51/56).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..core import errors, winreg_probe
from ..core.adapter import Adapter, ResultSet, row
from ..core.errors import AdapterError

PROGID_PREFIXES = ("lirasapphire.application", "lirasapr.application", "lirafem.application")
RES_TYPELIB_2024 = "{BD6C09E6-B5EE-4DAB-8D61-C009BA755C46}"
RES_CLASS_2024 = "{4F4546AE-AC54-48A2-9414-1B9EF6519A37}"
NO_RESULTS_HRESULT = -2147418113  # 0x8000FFFF: no results (also "no combinations defined")
CHUNK = 5000  # numbers per results request
SCRATCH = "struct-mcp-"
FORBIDDEN_IN_NAME = ".#*?,"

GEOMETRY_UNITS = {-1: None, 0: "m", 1: "cm", 2: "mm", 3: "ft", 4: "in"}
FORCE_UNITS = {0: "g", 1: "kg", 2: "t", 3: "N", 4: "kN", 5: "MN", 6: "lb", 7: "kips"}
RESULT_UNITS = {"force": "tf", "length": "m", "moment": "tf*m", "rotation": "rad",
                "source": "LIRA results API (fixed units, independent of the model's input units)"}
FAMILIES = {0: "undefined", 1: "bar", 2: "plate", 3: "solid", 4: "special", 5: "special_58_59",
            6: "special_310", 7: "special_264"}
FAMILY_PREFIX = {1: "GetBar", 2: "GetPlate", 3: "GetSolid", 4: "GetSpecElement", 5: "GetSpec_58_59_",
                 6: "GetSpec_310_", 7: "GetSpec_264_"}
# LiraForceEnum code -> component name (the getter is FAMILY_PREFIX + name)
FORCES = {
    1: "N", 2: "Mx", 3: "My", 4: "Qz", 5: "Mz", 6: "Qy", 7: "Ry", 8: "Rz", 9: "Bw", 10: "Tw",
    11: "Nx", 12: "Ny", 13: "Nz", 14: "Txy", 15: "Txz", 16: "Mx", 17: "My", 18: "Mxy", 19: "Qx", 20: "Qy",
    21: "Rz", 22: "Nx", 23: "Ny", 24: "Nz", 25: "Txy", 26: "Txz", 27: "Tyz",
    28: "N", 29: "Nx", 30: "Ny", 31: "Nz", 32: "Mx", 33: "My", 34: "Mz", 35: "Qy", 36: "Qz", 37: "Rx",
    38: "Ry", 39: "Rz", 40: "Rux", 41: "Ruy", 42: "Ruz", 43: "Qz", 44: "Ny", 45: "Qx",
    46: "N", 47: "My", 48: "Mx", 49: "Qz", 50: "Mz", 51: "Qy", 52: "N", 53: "Qz", 54: "Qy",
}
DISPLACEMENTS = ("X", "Y", "Z", "UX", "UY", "UZ")
FINISH_CODES = {0: "finished", 1: "failed", 2: "stopped"}


def parse_numbers(text: str | None) -> list[int]:
    """'1-3, 7 9' -> [1, 2, 3, 7, 9] (format of ILiraLongsArray.AddToString)."""
    out: list[int] = []
    for token in re.split(r"[,\s]+", (text or "").strip()):
        if not token:
            continue
        match = re.fullmatch(r"(-?\d+)-(\d+)", token)
        if match:
            out.extend(range(int(match.group(1)), int(match.group(2)) + 1))
        else:
            out.append(int(token))
    return out


def _ints(ids: list[str], what: str) -> list[int]:
    try:
        return [int(i) for i in ids]
    except ValueError:
        raise AdapterError(errors.INVALID_INPUT, f"{what} must be LIRA numbers, got {ids!r}.") from None


def _hresult(exc: BaseException) -> int | None:
    info = getattr(exc, "excepinfo", None)
    if info and len(info) > 5 and info[5]:
        return info[5]
    return getattr(exc, "hresult", None)


class _Com:
    """The pywin32 calls the adapter needs; replaced by a fake in tests."""

    def dispatch_app(self, progid: str) -> tuple[Any, Any]:
        import win32com.client  # type: ignore
        from win32com.client import gencache  # type: ignore

        # Early binding is required: table kinds are typelib constants and the table /
        # request interfaces are reached with CastTo.
        app = gencache.EnsureDispatch(progid)
        return app, win32com.client.constants

    def cast(self, obj: Any, interface: str) -> Any:
        import win32com.client  # type: ignore

        return win32com.client.CastTo(obj, interface)

    def results_access(self, install_root: str | None) -> tuple[Any, Any]:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        from win32com.client import gencache  # type: ignore

        guid, major, minor, clsid = RES_TYPELIB_2024, 1, 0, RES_CLASS_2024
        found = _find_results_typelib(install_root)
        if found:
            guid, major, minor, path = found
            tlib = pythoncom.LoadTypeLib(path)
            for i in range(tlib.GetTypeInfoCount()):
                if (tlib.GetTypeInfoType(i) == pythoncom.TKIND_COCLASS
                        and tlib.GetDocumentation(i)[0] == "LiraResultsAccess"):
                    clsid = str(tlib.GetTypeInfo(i).GetTypeAttr().iid)
        gencache.EnsureModule(guid, 0, major, minor)
        obj = win32com.client.Dispatch(clsid)
        return win32com.client.CastTo(obj, "ILiraResultsAccess"), win32com.client.constants

    def pump(self) -> None:
        import pythoncom  # type: ignore

        pythoncom.PumpWaitingMessages()

    def with_events(self, app: Any, handler: type) -> Any:
        import win32com.client  # type: ignore

        return win32com.client.WithEvents(app, handler)


def _find_results_typelib(install_root: str | None) -> tuple[str, int, int, str] | None:
    """Registered LIRA results type library (name contains 'res'), newest version."""
    try:
        import winreg
    except ImportError:
        return None
    best: tuple[tuple[int, int], tuple[str, int, int, str]] | None = None
    try:
        root = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "TypeLib")
    except OSError:
        return None
    index = 0
    while True:
        try:
            guid = winreg.EnumKey(root, index)
        except OSError:
            break
        index += 1
        try:
            with winreg.OpenKey(root, guid) as gkey:
                v = 0
                while True:
                    try:
                        version = winreg.EnumKey(gkey, v)
                    except OSError:
                        break
                    v += 1
                    name = winreg.QueryValue(gkey, version) or ""
                    low = name.lower()
                    if "res" not in low or not any(k in low for k in ("lira", "sapr", "sapphire")):
                        continue
                    path = None
                    for arch in ("win64", "win32"):
                        try:
                            path = winreg.QueryValue(gkey, rf"{version}\0\{arch}")
                            break
                        except OSError:
                            continue
                    if not path or (install_root and not path.lower().startswith(install_root.lower())):
                        continue
                    major, _, minor = version.partition(".")
                    key = (int(major, 16), int(minor or "0", 16))
                    if best is None or key > best[0]:
                        best = (key, (guid, key[0], key[1], path))
        except (OSError, ValueError):
            continue
    return best[1] if best else None


def _progid_year(progid: str) -> int:
    match = re.search(r"\.(\d{4})", progid)
    return int(match.group(1)) if match else 0


def _work_dir(year: int | None) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    years = [year] if year else []
    years += [y for y in range(2030, 2019, -1) if y != year]
    for y in years:
        for sub in (rf"Software\LIRA SAPR\LIRA SAPR {y}\Directories", rf"Software\LIRA SAPR\LIRA-FEM {y}\Directories",
                    rf"Software\LIRA SAPPHIRE\LIRA SAPPHIRE {y}\Directories"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as key:
                    return winreg.QueryValueEx(key, "WorkDirectory")[0]
            except OSError:
                continue
    return None


class LiraAdapter(Adapter):
    program = "lira"
    title = "LIRA-SAPR / LIRA-FEM"
    model_extensions = (".lir",)
    supports_analysis = True
    element_types = {
        "node": "nodes: X,Y,Z in model geometry units and restraints (X,Y,Z,UX,UY,UZ)",
        "element": "finite elements: FE type number, nodes, stiffness number",
        "stiffness": "stiffness (section) records: type code, comment, parameter string",
    }
    result_types = {
        "node_displacements": "X,Y,Z [m] and UX,UY,UZ [rad] of nodes for load cases; options.shape selects "
                              "a mode shape of a dynamic case (-1 = summary)",
        "element_forces": "internal forces of elements at design sections for load cases; components "
                          "depend on the element family (bars: N,Mx,My,Qz,Mz,Qy,...)",
        "combination_displacements": "node displacements for RSN (DCL) combinations; options.table "
                                     "(default 1)",
        "combination_forces": "element forces for RSN combinations; options.limit_state 1..4 "
                              "(1 ultimate full, 2 ultimate long-term, 3 serviceability full, "
                              "4 serviceability long-term), options.table",
        "modal": "per dynamic load case and mode: period, frequency, modal mass % and accumulated %",
    }

    def __init__(self, com: _Com | None = None) -> None:
        self._com = com or _Com()
        self._app: Any = None
        self._c: Any = None
        self._progid: str | None = None
        self.table_kinds: dict[str, int] = {}

    # ---- connection -------------------------------------------------------------
    def _progids(self) -> list[str]:
        configured = os.environ.get("STRUCT_MCP_LIRA_PROGID")
        found = sorted(winreg_probe.find_progids(PROGID_PREFIXES),
                       key=lambda p: (_progid_year(p), p.lower().startswith("lirasapphire")), reverse=True)
        return list(dict.fromkeys(([configured] if configured else []) + found))

    def probe(self) -> dict[str, Any]:
        progids = self._progids()
        info: dict[str, Any] = {
            "com": [winreg_probe.progid_info(p) for p in progids[:4]],
            "selected_progid": progids[0] if progids else None,
            "results_typelib": _find_results_typelib(None),
            "work_directory": _work_dir(_progid_year(progids[0]) if progids else None),
            "options": {"node_displacements": {"shape": "int"}, "combination_forces": {"limit_state": "1..4",
                                                                                         "table": "int"},
                        "combination_displacements": {"table": "int"}},
            "verified_on": "API usage follows LIRA-SAPR 2024 R2; 2025/2026 expose the same interfaces "
                           "under LiraSapphire.Application but were not verified.",
        }
        try:
            import win32com.client  # type: ignore  # noqa: F401

            info["pywin32"] = True
        except ImportError:
            info["pywin32"] = False
            info["note"] = "Package 'pywin32' is not installed; the adapter cannot talk to LIRA."
        if self.table_kinds:
            info["input_tables"] = sorted(self.table_kinds)
        return info

    def _server_running(self, progid: str) -> bool | None:
        servers = winreg_probe.progid_info(progid).get("servers") or []
        exe = next((Path(s["path"].strip('"').split('" ')[0]).name for s in servers if s["kind"] == "LocalServer32"),
                   None)
        if not exe:
            return None
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True,
                                 text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return exe.lower() in out.lower()

    def connect(self, model_path: str | None = None, start_if_needed: bool = False) -> dict[str, Any]:
        progids = self._progids()
        if not progids:
            raise AdapterError(errors.NOT_CONNECTED, "No LIRA application ProgID is registered on this machine.",
                               "Expected LiraSapr.Application or LiraSapphire.Application. Run LIRA once as "
                               "administrator to register it, or set STRUCT_MCP_LIRA_PROGID.")
        progid = progids[0]
        running = self._server_running(progid)
        if not start_if_needed and running is not True:
            raise AdapterError(errors.NOT_CONNECTED,
                               "LIRA is not running." if running is False else
                               "Could not determine whether LIRA is running.",
                               "Start LIRA (VISOR) and open a model, or call lira_connect with "
                               "start_if_needed=true (attaching would otherwise launch it).")
        try:
            self._app, self._c = self._com.dispatch_app(progid)
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "Package 'pywin32' is required (Windows only).",
                               "Install with: pip install pywin32") from None
        except Exception as exc:  # noqa: BLE001 - pywintypes.com_error
            raise AdapterError(errors.NOT_CONNECTED, f"Could not attach to LIRA ({progid}): {exc}",
                               "Check that LIRA starts normally and the licence key is present.") from None
        self._progid = progid
        self._load_table_kinds()
        out: dict[str, Any] = {"attached": True, "progid": progid, "started_new_instance": running is False}
        if model_path:
            doc = self._find_document(model_path)
            if doc is None:
                out["warning"] = f"'{model_path}' is not open in LIRA; the active document is used."
            else:
                doc.Activate()
        return out

    def _load_table_kinds(self) -> None:
        names: set[str] = set()
        for d in getattr(self._c, "__dicts__", []):
            names.update(d)
        prefix = "kLiraTable_"
        self.table_kinds = {n[len(prefix):]: getattr(self._c, n) for n in names
                            if n.startswith(prefix) and n[len(prefix):] not in ("AllTables", "Group", "Cnt")}

    def is_connected(self) -> bool:
        if self._app is None:
            return False
        try:
            self._app.DocumentCount
            return True
        except Exception:  # noqa: BLE001
            self._app = None
            return False

    def _a(self) -> Any:
        if not self.is_connected():
            raise AdapterError(errors.NOT_CONNECTED, "Not attached to LIRA.", "Call lira_connect first.")
        return self._app

    def program_info(self) -> dict[str, Any]:
        self._a()
        return {"name": self.title, "progid": self._progid, "year": _progid_year(self._progid or "") or None}

    def _find_document(self, path: str) -> Any:
        app = self._a()
        wanted = os.path.normcase(os.path.realpath(path))
        for i in range(app.DocumentCount):
            doc = app.Document(i)
            if os.path.normcase(os.path.realpath(str(doc.PathName))) == wanted:
                return doc
        return None

    def open_model(self, path: Path) -> None:
        if any(ch in path.stem for ch in FORBIDDEN_IN_NAME):
            raise AdapterError(errors.INVALID_INPUT,
                               f"LIRA cannot open a model whose name contains any of '{FORBIDDEN_IN_NAME}' "
                               f"('{path.name}'); it would show a blocking dialog.",
                               "Rename the source file and retry.")
        existing = self._find_document(str(path))
        if existing is not None:
            existing.Activate()
            return
        res = self._a().OpenDocument(str(path), False, True)  # RestoreEnv=False, Silent=True
        doc = res[0] if isinstance(res, tuple) else res
        if doc is None:
            msgs = res[1] if isinstance(res, tuple) and len(res) > 1 else ""
            raise AdapterError(errors.API_ERROR, f"LIRA did not open '{path}'. {msgs}".strip())

    # ---- model ------------------------------------------------------------------
    def _doc(self) -> Any:
        doc = self._a().ActiveDocument
        if doc is None:
            raise AdapterError(errors.NO_MODEL, "LIRA is running but no document is open.",
                               "Open a model in LIRA or call lira_open_working_copy.")
        return doc

    def model_path(self) -> str | None:
        doc = self._a().ActiveDocument
        if doc is None:
            return None
        path = str(doc.PathName)
        return path if path.lower().endswith(".lir") else None  # an unsaved document has no .lir path

    def read_table_rows(self, table: str, numbers: list[int] | None = None) -> list[list[Any]]:
        if table not in self.table_kinds:
            raise AdapterError(errors.UNSUPPORTED, f"Input table '{table}' is not available in this LIRA.",
                               f"Available: {', '.join(sorted(self.table_kinds))}.")
        doc, c = self._doc(), self._c
        part, pars = ((c.kLiraModelPart_FromArray, [int(n) for n in numbers]) if numbers
                      else (c.kLiraModelPart_All, None))
        tables = doc.AllTables
        try:
            item = tables.CreateNewItem(self.table_kinds[table], pars, part,
                                        f"{SCRATCH}{os.getpid()}-{uuid.uuid4().hex[:8]}", -1)
            try:
                data = self._com.cast(item, "ILiraTable").GetContents([])
            except Exception:  # noqa: BLE001 - LIRA raises for an empty table
                data = ()
        finally:
            for i in range(tables.ItemCount - 1, -1, -1):
                if str(tables.Item(i).Name).startswith(SCRATCH):
                    tables.Delete(i)
        return [[None if cell in ("", None) else cell for cell in r] for r in (data or ())]

    def _input_units(self) -> dict[str, Any]:
        try:
            mu = self._a().MeasurementUnits
            return {"geometry": GEOMETRY_UNITS.get(int(mu.Geometry), str(mu.Geometry)),
                    "sections": GEOMETRY_UNITS.get(int(mu.Sections), str(mu.Sections)),
                    "loads_force": FORCE_UNITS.get(int(mu.Loads1), str(mu.Loads1)),
                    "source": "Application.MeasurementUnits (apply to input tables)"}
        except Exception as exc:  # noqa: BLE001
            return {"geometry": None, "source": f"MeasurementUnits not readable: {exc}"}

    def model_info(self) -> dict[str, Any]:
        doc = self._doc()
        path = str(doc.PathName)
        nodes = len(self.read_table_rows("Nodes_Coordinates"))
        elements = len(self.read_table_rows("Elements_TypeAndNodes"))
        info: dict[str, Any] = {
            "title": str(doc.Title), "saved_as_lir": path.lower().endswith(".lir"),
            "modified_flag": bool(doc.IsModified()),
            "modified_flag_note": "Also set by this server's table reads; it does not prove the model changed.",
            "input_units": self._input_units(), "result_units": RESULT_UNITS,
            "counts": {"nodes": nodes, "elements": elements,
                       "load_cases": len(self.read_table_rows("LoadCases"))},
        }
        results: dict[str, Any] = {"detail": "LIRA stores results by model NAME in its Work folder; a "
                                             "same-named model analysed from another folder replaces them."}
        if not info["saved_as_lir"]:
            results.update(available=False, reason="document is not saved as .lir")
        else:
            try:
                main = self._query(path, "kLiraRequest_MainData", "ILiraMainDataRequest", "MainData")
                rn, re_ = int(main.GetNodeCount()), int(main.GetElementCount())
                results.update(available=True, result_nodes=rn, result_elements=re_,
                               matches_model_counts=(rn == nodes and re_ == elements))
                if not results["matches_model_counts"]:
                    results["warning"] = ("Result node/element counts differ from the open model: results are "
                                          "stale or belong to another model with the same name.")
            except AdapterError as exc:
                results.update(available=False, reason=exc.message)
        info["results"] = results
        return info

    def element_ids(self, element_type: str) -> list[str]:
        table = {"node": "Nodes_Coordinates", "element": "Elements_TypeAndNodes", "stiffness": "Stiffnesses"}
        return [str(int(r[0])) for r in self.read_table_rows(table[element_type]) if r and r[0] is not None]

    def describe_elements(self, element_type: str, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        numbers = _ints(ids, "ids")
        if element_type == "node":
            unit = self._input_units()["geometry"]
            restraints = {int(r[0]): r[1:7] for r in self.read_table_rows("Nodes_Restraints", numbers) if r}
            out = []
            for r in self.read_table_rows("Nodes_Coordinates", numbers):
                fixed = restraints.get(int(r[0]))
                out.append({"id": str(int(r[0])), "x": r[1], "y": r[2], "z": r[3], "length_unit": unit,
                            "coordinate_system": "Global",
                            "restraint_X_Y_Z_UX_UY_UZ": [v is not None for v in fixed] if fixed else None})
            return out
        if element_type == "element":
            stiff = {int(r[0]): r[1] for r in self.read_table_rows("Elements_Stiffnesses", numbers) if r}
            return [{"id": str(int(r[0])), "fe_type": r[1],
                     "nodes": [str(n) for n in parse_numbers(str(r[2]))] if len(r) > 2 and r[2] is not None else [],
                     "stiffness": stiff.get(int(r[0]))}
                    for r in self.read_table_rows("Elements_TypeAndNodes", numbers)]
        wanted = set(numbers)  # stiffness rows are filtered here; FromArray addresses nodes/elements
        return [{"id": str(int(r[0])), "section_type_code": r[1], "comment": r[3] if len(r) > 3 else None,
                 "parameters": r[4] if len(r) > 4 else None}
                for r in self.read_table_rows("Stiffnesses") if r and int(r[0]) in wanted]

    def list_load_cases(self, kind: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if kind in ("cases", "all"):
            for r in self.read_table_rows("LoadCases"):
                r = list(r) + [None] * 5
                items.append({"id": str(int(r[0])), "kind": "case", "name": r[1], "subtask": r[2], "type": r[3],
                              "formula": r[4]})
        if kind in ("combinations", "all"):
            path = self.model_path()
            try:
                if not path:
                    raise AdapterError(errors.NO_RESULTS, "document is not saved as .lir")
                resp = self._query(path, "kLiraRequest_LoadCombinationDisplacements",
                                   "ILiraLoadCombinationDisplacementsRequest", "LoadCombinationDisplacements",
                                   lambda req: req.Nodes.AddFromString("1"))
                infos = resp.LoadCombinations
                for i in range(infos.Count):
                    item = infos.Item(i)
                    items.append({"id": str(int(item.Number)), "kind": "combination", "name": str(item.Name),
                                  "formula_number": int(item.FormulaNumber), "is_seismic": bool(item.IsSeismic),
                                  "is_special": bool(item.IsSpecial),
                                  "members": None})
            except AdapterError as exc:
                items.append({"id": None, "kind": "diagnostic",
                              "message": "RSN combinations are listed from analysis results, which are not "
                                         f"available ({exc.message}). Combination factors are not exposed by "
                                         "the API; read the DCL table with lira_read_table."})
        return items

    # ---- results ----------------------------------------------------------------
    def _query(self, model_path: str, kind: str, interface: str, method: str, fill: Any = None) -> Any:
        """One request on a FRESH LiraResultsAccess (reusing one returns garbage in LIRA 2024)."""
        try:
            servers = winreg_probe.progid_info(self._progid or "").get("servers") or []
            root = None
            for s in servers:
                p = Path(s["path"].strip('"'))
                if p.parent.parent.name.lower() == "bin":
                    root = str(p.parent.parent.parent)
            access, constants = self._com.results_access(root)
            req = self._com.cast(access.CreateNewRequest(getattr(constants, kind)), interface)
            req.DocumentName = Path(model_path).stem  # name only: no folder, no extension
            if fill:
                fill(req)
            return getattr(access, method)(req)
        except AdapterError:
            raise
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "Package 'pywin32' is required.") from None
        except Exception as exc:  # noqa: BLE001
            if _hresult(exc) == NO_RESULTS_HRESULT:
                raise AdapterError(errors.NO_RESULTS,
                                   f"LIRA has no such results for model '{Path(model_path).stem}'.",
                                   "Run the analysis (and define RSN for combination results). The same "
                                   "error is returned when no combinations are defined.") from None
            raise AdapterError(errors.API_ERROR, f"LIRA results request {method} failed: {exc}",
                               "The Python bitness must match LiraResAPI.dll (64-bit); see "
                               "lira_get_capabilities.") from None

    @staticmethod
    def _longs(array: Any) -> list[int]:
        # Count/Item, not AddToString: the latter corrupts the heap on large scattered lists.
        return [int(array.Item(i)) for i in range(array.Count)]

    @staticmethod
    def _chunks(numbers: list[int] | None) -> list[str | None]:
        if not numbers:
            return [None]  # empty request list = everything
        return [" ".join(map(str, numbers[i:i + CHUNK])) for i in range(0, len(numbers), CHUNK)]

    def _case_shapes(self, resp: Any) -> dict[int, tuple[list[int], bool]]:
        out: dict[int, tuple[list[int], bool]] = {}
        infos = resp.LoadCases
        for i in range(infos.Count):
            info = infos.Item(i)
            shapes = parse_numbers(info.DeformedShapes.AddToString())
            out[int(info.Number)] = (shapes or [1], bool(shapes) and int(info.SummaryShapeCount) > 0)
        return out

    def _force_rows(self, resp: Any, element: int, cases: list[tuple[int, int, Any]], extra_args: tuple,
                    label: str) -> list[dict[str, Any]]:
        family = int(resp.GetFamily(element))
        prefix = FAMILY_PREFIX.get(family)
        codes = parse_numbers(resp.GetForces(element).AddToString())
        if prefix is None or not codes:
            return [row("no_result", element=str(element), family=FAMILIES.get(family, str(family)),
                        message="no force components reported for this element")]
        rows = []
        for section in range(1, int(resp.GetSectionCount(element)) + 1):
            for case, position, shape in cases:
                values: dict[str, Any] = {}
                for code in codes:
                    name = FORCES.get(code)
                    if name:
                        args = (element, section, case, position, *extra_args)
                        values[name] = float(getattr(resp, prefix + name)(*args))
                fields = {"element": str(element), "family": FAMILIES[family], "section": section, label: str(case)}
                if shape is not None:
                    fields["shape"] = shape
                rows.append(row(**fields, **values))
        return rows

    def get_results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                    options: dict[str, Any]) -> ResultSet:
        path = self.model_path()
        if not path:
            raise AdapterError(errors.NO_RESULTS, "The active document is not saved as .lir; it has no results.")
        rows: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        ids = _ints(element_ids, "element_ids") if element_ids else None
        cases = _ints(load_cases, "load_cases")
        if result_type != "modal" and not cases:
            raise AdapterError(errors.INVALID_INPUT, "load_cases is required for this result type.",
                               "Pass numbers from lira_list_load_cases.")
        case_str = " ".join(map(str, cases))

        if result_type == "modal":
            resp = self._query(path, "kLiraRequest_PeriodsOfVibrations", "ILiraPeriodsOfVibrationsRequest",
                               "PeriodsOfVibrations")
            dynamic = parse_numbers(resp.LoadCaseNumbers.AddToString())
            for case in [c for c in dynamic if not cases or c in cases]:
                def arr(getter: str) -> list[float]:
                    a = getattr(resp, getter)(case)
                    return [float(a.Item(i)) for i in range(a.Count)]
                periods, freqs = arr("GetPeriods"), arr("GetFrequenciesHz")
                masses, accumulated = arr("GetModalMasses"), arr("GetAccumulatedModalMasses")
                for i, period in enumerate(periods):
                    rows.append(row(load_case=str(case), mode=i + 1, period_s=period,
                                    frequency_hz=freqs[i] if i < len(freqs) else None,
                                    modal_mass_percent=masses[i] if i < len(masses) else None,
                                    accumulated_modal_mass_percent=accumulated[i] if i < len(accumulated) else None,
                                    dynamic_type=int(resp.GetDynamicLoadCaseType(case))))
            if not dynamic:
                diagnostics.append("The model has no dynamic load cases.")
            return ResultSet(rows, RESULT_UNITS, "n/a", diagnostics)

        if result_type == "node_displacements":
            for chunk in self._chunks(ids):
                def fill(req: Any, chunk: str | None = chunk) -> None:
                    req.LoadCases.AddFromString(case_str)
                    if chunk:
                        req.Nodes.AddFromString(chunk)
                resp = self._query(path, "kLiraRequest_LoadCaseDisplacement", "ILiraLoadCaseDisplacementsRequest",
                                   "LoadCaseDisplacements", fill)
                shapes = self._case_shapes(resp)
                for node in self._longs(resp.NodeNumbers):
                    for case in cases:
                        wanted = [int(options["shape"])] if "shape" in options else shapes.get(case, ([1], False))[0]
                        for shape in wanted:
                            vals = {d: float(getattr(resp, f"GetNode{d}")(node, case, shape, 0))
                                    for d in DISPLACEMENTS}
                            rows.append(row(node=str(node), load_case=str(case), shape=shape, **vals))
            return ResultSet(rows, RESULT_UNITS, "global X-Y-Z (node local axes where assigned)", diagnostics)

        if result_type == "combination_displacements":
            table = int(options.get("table", 1))
            for chunk in self._chunks(ids):
                def fill(req: Any, chunk: str | None = chunk) -> None:
                    req.LoadCombinationTable = table
                    req.LoadCombinations.AddFromString(case_str)
                    if chunk:
                        req.Nodes.AddFromString(chunk)
                resp = self._query(path, "kLiraRequest_LoadCombinationDisplacements",
                                   "ILiraLoadCombinationDisplacementsRequest", "LoadCombinationDisplacements", fill)
                for node in self._longs(resp.NodeNumbers):
                    for case in cases:
                        vals = {d: float(getattr(resp, f"GetNode{d}")(node, case)) for d in DISPLACEMENTS}
                        rows.append(row(node=str(node), combination=str(case), table=table, **vals))
            return ResultSet(rows, RESULT_UNITS, "global X-Y-Z (node local axes where assigned)", diagnostics)

        elements = ids or [int(i) for i in self.element_ids("element")]
        coord = "element local axes (LIRA sign conventions); sections numbered from 1 along the element"
        if result_type == "element_forces":
            for chunk in self._chunks(elements):
                def fill(req: Any, chunk: str | None = chunk) -> None:
                    req.LoadCases.AddFromString(case_str)
                    req.Elements.AddFromString(chunk)
                resp = self._query(path, "kLiraRequest_LoadCaseForces", "ILiraLoadCaseForcesRequest",
                                   "LoadCaseForces", fill)
                shapes = self._case_shapes(resp)
                triples: list[tuple[int, int, Any]] = []
                for case in cases:
                    modes, dynamic = shapes.get(case, ([1], False))
                    if len(modes) > 1 or dynamic:
                        # forces address a mode by ordinal position, displacements by mode number
                        triples += [(case, pos, mode) for pos, mode in enumerate(modes, start=1)]
                    else:
                        triples.append((case, 1, None))
                for element in parse_numbers(chunk):
                    rows += self._force_rows(resp, element, triples, (0,), "load_case")
            diagnostics.append("Special-element moments are reported in tf, not tf*m (LIRA API).")
            return ResultSet(rows, RESULT_UNITS, coord, diagnostics)

        # combination_forces
        limit_state = int(options.get("limit_state", 1))
        if limit_state not in (1, 2, 3, 4):
            raise AdapterError(errors.INVALID_INPUT, "options.limit_state must be 1, 2, 3 or 4.")
        table = int(options.get("table", 1))
        for chunk in self._chunks(elements):
            def fill(req: Any, chunk: str | None = chunk) -> None:
                req.LoadCombinationTable = table
                req.LoadCombinations.AddFromString(case_str)
                req.LoadCombinationLimitState.AddFromString(str(limit_state))
                req.Elements.AddFromString(chunk)
            resp = self._query(path, "kLiraRequest_LoadCombinationForces", "ILiraLoadCombinationForcesRequest",
                               "LoadCombinationForces", fill)
            for element in parse_numbers(chunk):
                # getter signature: (element, section, combination, limit_state)
                rows += self._force_rows(resp, element, [(c, limit_state, None) for c in cases], (), "combination")
        diagnostics.append(f"Limit state {limit_state}; RSN table {table}.")
        return ResultSet(rows, RESULT_UNITS, coord, diagnostics)

    # ---- analysis ---------------------------------------------------------------
    def protocol(self, tail_lines: int = 40) -> dict[str, Any]:
        path = self.model_path()
        work = _work_dir(_progid_year(self._progid or "") or None)
        if not path or not work:
            raise AdapterError(errors.NO_RESULTS, "No saved model or LIRA Work folder not found in the registry.")
        stem = Path(path).stem
        file = Path(work) / f"{stem}_01.{stem}"
        if not file.is_file():
            raise AdapterError(errors.NO_RESULTS, f"Analysis protocol not found: {file}",
                               "The model has not been analysed on this machine.")
        raw = file.read_bytes()
        for encoding in ("utf-8-sig", "cp1251"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        final = [ln for ln in lines if ln.strip().lower().startswith("расчет")]
        problems = [ln for ln in lines if ln.lstrip().upper().startswith(("ВНИМАНИЕ", "ОШИБКА", "ERROR", "WARNING"))]
        return {"protocol_file": str(file),
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(file.stat().st_mtime)),
                "completed_successfully": bool(final) and "успешно" in final[-1].lower(),
                "problem_lines": problems[:100], "tail": lines[-max(1, min(tail_lines, 400)):]}

    def run_analysis(self, load_cases: list[str] | None) -> dict[str, Any]:
        if load_cases:
            raise AdapterError(errors.UNSUPPORTED, "LIRA analyses the whole model; load cases cannot be selected.",
                               "Call lira_run_analysis without load_cases.")
        app, doc = self._a(), self._doc()
        path = self.model_path()
        if not path:
            raise AdapterError(errors.NOT_ALLOWED, "The document must be saved as .lir before analysis.")
        if doc.IsModified():
            doc.Activate()
            doc.Save(False)  # a working copy; LIRA refuses to analyse a modified document
        state: dict[str, Any] = {"stages": [], "done": None}

        class Events:
            def OnAnalysisStarting(self, Stage: int) -> None:
                state["stages"].append(int(Stage))

            def OnAnalysisFinished(self, FinishCode: int) -> None:
                state["done"] = FINISH_CODES.get(int(FinishCode), str(FinishCode))

            def OnAnalysisStopped(self, StopCode: int) -> None:
                state["done"] = "needs_user_dialog" if int(StopCode) == 1 else "stopped"

        sink = self._com.with_events(app, Events)
        try:
            if not app.StartAnalysis(path):
                raise AdapterError(errors.API_ERROR, "LIRA refused to start the analysis.",
                                   "See PackageAnalysisLog.TXT in LIRA's Work folder.")
            ticks = 0
            while state["done"] is None:  # events arrive only while this thread pumps messages
                self._com.pump()
                time.sleep(0.05)
                ticks += 1
                if ticks % 100 == 0:
                    try:
                        app.DocumentCount  # raises once LIRA has exited
                    except Exception:  # noqa: BLE001
                        raise AdapterError(errors.NOT_CONNECTED,
                                           "LIRA stopped answering during the analysis.") from None
        finally:
            del sink
        outcome: dict[str, Any] = {"state": state["done"], "stages": state["stages"],
                                   "note": "LIRA reports 'finished' even for runs with problems (e.g. geometric "
                                           "instability) and closes the document after analysis; check "
                                           "'protocol' and reopen with lira_open_working_copy / lira_connect."}
        try:
            outcome["protocol"] = self.protocol(15)
        except AdapterError as exc:
            outcome["protocol"] = f"unavailable: {exc.message}"
        return outcome

    # ---- extras -----------------------------------------------------------------
    def read_table(self, table: str, numbers: list[int] | None = None, offset: int = 0,
                   limit: int = 100) -> dict[str, Any]:
        rows = self.read_table_rows(table, numbers)
        limit = max(1, min(limit, 500))
        page = rows[offset:offset + limit]
        end = offset + len(page)
        return {"table": table, "total": len(rows), "offset": offset, "count": len(page),
                "has_more": end < len(rows), "next_offset": end if end < len(rows) else None, "rows": page,
                "note": "Cells are in model input units; the API gives no column headers (DCL and "
                        "CollectionOfMasses carry a header in their first row)."}

    def extra_tools(self) -> dict[str, Any]:
        return {
            "read_table": (self.read_table,
                           "Read any LIRA input table of the active document as raw rows (read-only), e.g. "
                           "Nodes_Coordinates, Elements_TypeAndNodes, Stiffnesses, LoadCases, DCL (RSN), "
                           "Bars_Hinges, AssemblageStages; the list for this LIRA is in lira_get_capabilities "
                           "after connecting. numbers restricts node/element tables to those numbers. "
                           "Paginated with offset/limit.", None),
            "get_analysis_protocol": (self.protocol,
                                      "Tail of the solver protocol of the active model from LIRA's Work "
                                      "folder, with warning/error lines and whether the run completed "
                                      "successfully. Use it to judge an analysis: LIRA signals 'finished' "
                                      "even for runs with problems.", None),
        }
