"""STAAD.Pro adapter over Bentley's `openstaadpy` (OpenSTAAD, comtypes).

Facts taken from the openstaadpy source and Bentley's openstaad-mcp skills:
* getters return values in the model's BASE unit system — "Metric" = metre + kN,
  "English" = inch + kip — regardless of the input/display units shown in the UI;
* GetMemberEndForces(member, end, loadCase, localOrGlobal): end 0 = start (A),
  1 = end (B); localOrGlobal 0 = local, 1 = global; components FX,FY,FZ,MX,MY,MZ;
* an instance is addressed by the path of its open .std file (ROT moniker).

Only getters are called. SetInputUnits / SetLoadActive and the like are never used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import errors, winreg_probe
from ..core.adapter import Adapter, ResultSet, row
from ..core.errors import AdapterError

PROGID = "StaadPro.OpenSTAAD"
BASE_UNITS = {"Metric": ("kN", "m"), "English": ("kip", "in")}
SUPPORT_TYPES = {
    0: "none", 1: "pinned", 2: "fixed", 3: "fixed_but", 4: "enforced", 5: "enforced_but", 6: "inclined",
    7: "footing", 8: "elastic_mat", 9: "plate_mat", 10: "multilinear_spring", 11: "generated_pinned",
    12: "generated_fixed", 13: "generated_fixed_but",
}
LOAD_TYPES = ["Dead", "Live", "Roof Live", "Wind", "Seismic-H", "Seismic-V", "Snow", "Fluids", "Soil", "Rain",
              "Ponding", "Dust", "Traffic", "Temperature", "Imperfection", "Accidental", "Flood", "Ice",
              "Wind Ice", "Crane Hook", "Mass", "Gravity", "Push", "None"]
ANALYSIS_STATUS = {-1: "terminated", 0: "general_error", 1: "in_progress", 2: "completed",
                   3: "completed_with_warnings", 4: "completed_with_errors", 5: "not_performed"}
SIX = ("FX", "FY", "FZ", "MX", "MY", "MZ")


def _ints(ids: list[str], what: str) -> list[int]:
    try:
        return [int(i) for i in ids]
    except ValueError:
        raise AdapterError(errors.INVALID_INPUT, f"{what} must be STAAD numbers, got {ids!r}.") from None


class StaadAdapter(Adapter):
    program = "staad"
    title = "STAAD.Pro"
    model_extensions = (".std",)
    supports_analysis = True
    element_types = {
        "node": "nodes: coordinates (global, base length unit) and support type",
        "member": "beam/column members: end nodes, length, section, material, beta angle",
        "plate": "plate elements: corner nodes (3 or 4)",
    }
    result_types = {
        "node_displacements": "X,Y,Z translations and rX,rY,rZ rotations of nodes (global axes)",
        "support_reactions": "FX,FY,FZ,MX,MY,MZ at supported nodes (global axes)",
        "member_end_forces": "FX,FY,FZ,MX,MY,MZ at both member ends; options.axes = 'local' (default) "
                             "or 'global'",
        "modal": "per extracted mode: frequency, period and mass participation in X,Y,Z (percent)",
    }

    def __init__(self) -> None:
        self._staad: Any = None

    # ---- connection -------------------------------------------------------------
    def probe(self) -> dict[str, Any]:
        info: dict[str, Any] = {"com": [winreg_probe.progid_info(PROGID)]}
        try:
            import openstaadpy  # type: ignore  # noqa: F401

            info["openstaadpy"] = True
        except ImportError:
            info["openstaadpy"] = False
            info["note"] = ("Package 'openstaadpy' is not installed: "
                            "pip install git+https://github.com/BentleySystems/openstaadpy.git")
        info["options"] = {"member_end_forces": {"axes": ["local", "global"]}}
        info["requirements"] = "STAAD.Pro 2025 (25.0.1) or newer, running with a model open."
        return info

    def list_instances(self) -> dict[str, Any]:
        """Open .std models registered in the Windows Running Object Table."""
        try:
            import pythoncom  # type: ignore
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "Package 'pywin32' is required to list instances.",
                               "Install with: pip install pywin32") from None
        found: list[str] = []
        rot = pythoncom.GetRunningObjectTable()
        ctx = pythoncom.CreateBindCtx(0)
        for moniker in rot.EnumRunning():
            try:
                name = moniker.GetDisplayName(ctx, None)
            except Exception:  # noqa: BLE001 - some monikers cannot be named
                continue
            if name.lower().endswith(".std"):
                found.append(name)
        return {"count": len(found), "open_models": found,
                "note": "Pass one of these paths as model_path to staad_connect. An instance with no "
                        "model open is not listed."}

    def connect(self, model_path: str | None = None, start_if_needed: bool = False) -> dict[str, Any]:
        try:
            import comtypes  # type: ignore

            comtypes.CoInitialize()  # openstaadpy initialises COM only on the thread that imports it
            from openstaadpy import os_analytical  # type: ignore
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "Package 'openstaadpy' is required (Windows only).",
                               "pip install git+https://github.com/BentleySystems/openstaadpy.git") from None
        if start_if_needed:
            raise AdapterError(errors.UNSUPPORTED, "This server does not start STAAD.Pro.",
                               "Start STAAD.Pro and open a model, then call staad_connect.")
        try:
            self._staad = os_analytical.connect(model_path) if model_path else os_analytical.connect()
        except Exception as exc:  # noqa: BLE001 - OsErrorBase
            raise AdapterError(errors.NOT_CONNECTED, f"Could not attach to STAAD.Pro: {exc}",
                               "STAAD.Pro must be running with a model open (and licensed for OpenSTAAD). "
                               "With several instances, pass model_path from staad_list_instances.") from None
        return {"attached": True, **self.program_info()}

    def is_connected(self) -> bool:
        if self._staad is None:
            return False
        try:
            self._staad.GetApplicationVersion()
            return True
        except Exception:  # noqa: BLE001
            self._staad = None
            return False

    def _s(self) -> Any:
        if not self.is_connected():
            raise AdapterError(errors.NOT_CONNECTED, "Not attached to STAAD.Pro.", "Call staad_connect first.")
        return self._staad

    def program_info(self) -> dict[str, Any]:
        return {"name": "STAAD.Pro", "version": self._s().GetApplicationVersion()}

    def open_model(self, path: Path) -> None:
        self._s().OpenSTAADFile(str(path))

    # ---- model ------------------------------------------------------------------
    def model_path(self) -> str | None:
        try:
            return self._s().GetSTAADFile(True) or None
        except AdapterError:
            raise
        except Exception:  # noqa: BLE001 - no model open
            return None

    def _units(self) -> dict[str, str]:
        base = self._s().GetBaseUnit()
        force, length = BASE_UNITS.get(base, ("?", "?"))
        return {"force": force, "length": length, "moment": f"{force}*{length}", "rotation": "rad",
                "source": f"STAAD base unit system '{base}' (API getters ignore input/display units)"}

    def model_info(self) -> dict[str, Any]:
        s = self._s()
        if not self.model_path():
            raise AdapterError(errors.NO_MODEL, "STAAD.Pro is running but no model is open.")
        info: dict[str, Any] = {
            "units": self._units(),
            "vertical_axis": "Z" if s.Geometry.IsZUp() else "Y",
            "counts": {"nodes": s.Geometry.GetNodeCount(), "members": s.Geometry.GetMemberCount(),
                       "plates": s.Geometry.GetPlateCount(), "supports": s.Support.GetSupportCount(),
                       "primary_load_cases": s.Load.GetPrimaryLoadCaseCount(),
                       "combinations": s.Load.GetLoadCombinationCaseCount()},
        }
        available = bool(s.Output.AreResultsAvailable())
        results: dict[str, Any] = {"available": available, "analyzing_now": bool(s.IsAnalyzing())}
        try:
            status = s.GetAnalysisStatus()
            results["status"] = ANALYSIS_STATUS.get(status.get("ReturnValue"), str(status.get("ReturnValue")))
            results["warnings"], results["errors"] = status.get("NoOfWarnings"), status.get("NoOfErrors")
        except Exception as exc:  # noqa: BLE001 - raises on negative status / older builds
            results["status"] = f"unavailable: {exc}"
        results["detail"] = ("Whether results match unsaved edits is not reported by OpenSTAAD; results "
                             "belong to the last analysed, saved file.")
        info["results"] = results
        return info

    def element_ids(self, element_type: str) -> list[str]:
        g = self._s().Geometry
        getter = {"node": g.GetNodeList, "member": g.GetBeamList, "plate": g.GetPlateList}[element_type]
        return [str(i) for i in list(getter())]

    def describe_elements(self, element_type: str, ids: list[str]) -> list[dict[str, Any]]:
        s = self._s()
        length = self._units()["length"]
        out: list[dict[str, Any]] = []

        def attempt(fn: Any, *args: Any) -> Any:
            try:
                return fn(*args)
            except Exception:  # noqa: BLE001 - e.g. no section/material assigned
                return None

        for n in _ints(ids, "ids"):
            if element_type == "node":
                x, y, z = s.Geometry.GetNodeCoordinates(n)
                code = attempt(s.Support.GetSupportType, n)
                out.append({"id": str(n), "x": x, "y": y, "z": z, "length_unit": length,
                            "coordinate_system": "Global",
                            "support": SUPPORT_TYPES.get(code, str(code)) if code is not None else "unknown"})
            elif element_type == "member":
                a, b = s.Geometry.GetMemberIncidence(n)
                out.append({"id": str(n), "node_a": str(a), "node_b": str(b),
                            "length": attempt(s.Geometry.GetBeamLength, n), "length_unit": length,
                            "section": attempt(s.Property.GetBeamSectionName, n),
                            "material": attempt(s.Property.GetBeamMaterialName, n),
                            "beta_angle_deg": attempt(s.Property.GetBetaAngle, n)})
            else:
                nodes = [str(i) for i in list(s.Geometry.GetPlateIncidence(n)) if i]
                out.append({"id": str(n), "nodes": nodes,
                            "material": attempt(s.Property.GetPlateMaterialName, n)})
        return out

    def list_load_cases(self, kind: str) -> list[dict[str, Any]]:
        load = self._s().Load
        items: list[dict[str, Any]] = []
        if kind in ("cases", "all"):
            for n in list(load.GetPrimaryLoadCaseNumbers()):
                try:
                    code = load.GetLoadType(n)
                    load_type = LOAD_TYPES[code] if 0 <= code < len(LOAD_TYPES) else str(code)
                except Exception:  # noqa: BLE001
                    load_type = "unknown"
                items.append({"id": str(n), "kind": "case", "name": load.GetLoadCaseTitle(n), "type": load_type})
        if kind in ("combinations", "all"):
            for n in list(load.GetLoadCombinationCaseNumbers()):
                count = load.GetNoOfLoadAndFactorPairsForCombination(n)
                ids, factors = load.GetLoadAndFactorForCombination(n)
                # the factor array carries one trailing element (overall SRSS factor / padding)
                members = [{"name": str(i), "factor": f}
                           for i, f in zip(list(ids)[:count], list(factors)[:count])]
                items.append({"id": str(n), "kind": "combination", "name": load.GetLoadCaseTitle(n),
                              "members": members})
        return items

    # ---- results ----------------------------------------------------------------
    def get_results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                    options: dict[str, Any]) -> ResultSet:
        s = self._s()
        out = s.Output
        if not out.AreResultsAvailable():
            raise AdapterError(errors.NO_RESULTS, "STAAD.Pro reports no analysis results for this model.",
                               "Run the analysis in STAAD.Pro (or staad_run_analysis on a working copy).")
        units = self._units()
        rows: list[dict[str, Any]] = []
        diagnostics: list[str] = []

        if result_type == "modal":
            modes = out.GetNoOfModesExtracted()
            for mode in range(1, modes + 1):
                try:
                    freq = out.GetModeFrequency(mode)
                    px, py, pz = out.GetModalMassParticipationFactors(mode)
                    rows.append(row(mode=mode, frequency_hz=freq, period_s=(1.0 / freq) if freq else None,
                                    participation_x_percent=px, participation_y_percent=py, participation_z_percent=pz))
                except Exception as exc:  # noqa: BLE001
                    rows.append(row("error", mode=mode, message=str(exc)))
            if not modes:
                diagnostics.append("No modes extracted (no modal analysis in this model).")
            diagnostics.append("Mass participation is in percent of total mass per global direction "
                               "(as labelled in Bentley's OpenSTAAD dynamic-output sample).")
            return ResultSet(rows, units, "global X-Y-Z", diagnostics)

        if not load_cases:
            raise AdapterError(errors.INVALID_INPUT, "load_cases is required for this result type.",
                               "Pass load case / combination numbers from staad_list_load_cases.")
        cases = _ints(load_cases, "load_cases")

        if result_type in ("node_displacements", "support_reactions"):
            if element_ids:
                nodes = _ints(element_ids, "element_ids")
            elif result_type == "support_reactions":
                nodes = list(s.Support.GetSupportNodes())
            else:
                nodes = list(s.Geometry.GetNodeList())
            fn, keys = ((out.GetNodeDisplacements, ("X", "Y", "Z", "rX", "rY", "rZ"))
                        if result_type == "node_displacements" else (out.GetSupportReactions, SIX))
            for node in nodes:
                for case in cases:
                    try:
                        rows.append(row(node=str(node), load_case=str(case), **dict(zip(keys, list(fn(node, case))))))
                    except Exception as exc:  # noqa: BLE001 - API failure for this item, not a zero
                        rows.append(row("error", node=str(node), load_case=str(case), message=str(exc)))
            return ResultSet(rows, units, "global X-Y-Z", diagnostics)

        axes = options.get("axes", "local")
        if axes not in ("local", "global"):
            raise AdapterError(errors.INVALID_INPUT, f"options.axes must be 'local' or 'global', got {axes!r}.")
        flag = 0 if axes == "local" else 1
        members = _ints(element_ids, "element_ids") if element_ids else list(s.Geometry.GetBeamList())
        for member in members:
            for case in cases:
                for end, label in ((0, "A"), (1, "B")):
                    try:
                        vals = list(out.GetMemberEndForces(member, end, case, flag))
                        rows.append(row(member=str(member), end=label, load_case=str(case), **dict(zip(SIX, vals))))
                    except Exception as exc:  # noqa: BLE001
                        rows.append(row("error", member=str(member), end=label, load_case=str(case),
                                        message=str(exc)))
        diagnostics.append("Member end forces act on the member ends; ends A and B use opposite sign "
                           "conventions (STAAD end-force convention), so an equal axial force appears with "
                           "opposite signs at A and B.")
        coord = "member local x-y-z" if axes == "local" else "global X-Y-Z"
        return ResultSet(rows, units, coord, diagnostics)

    # ---- analysis ---------------------------------------------------------------
    def run_analysis(self, load_cases: list[str] | None) -> dict[str, Any]:
        if load_cases:
            raise AdapterError(errors.UNSUPPORTED, "STAAD.Pro analyses the whole input file; a subset of "
                               "load cases cannot be selected.", "Call staad_run_analysis without load_cases.")
        s = self._s()
        s.SetSilentMode(True)  # otherwise modal dialogs block the COM call
        try:
            status = s.AnalyzeEx(1, 0, 1)  # silent, visible, wait until complete
        finally:
            s.SetSilentMode(False)
        outcome: dict[str, Any] = {"status_code": status, "status": ANALYSIS_STATUS.get(status, str(status)),
                                   "results_available": bool(s.Output.AreResultsAvailable())}
        try:
            outcome["errors"] = list(s.GetAnalysisErrorMessages())
            outcome["warnings"] = list(s.GetAnalysisWarningMessages())
        except Exception:  # noqa: BLE001 - STAAD.Pro v26+ only
            outcome["messages_note"] = "Error/warning texts need STAAD.Pro v26+; read the .anl file."
        return outcome

    def extra_tools(self) -> dict[str, Any]:
        return {"list_instances": (self.list_instances,
                                   "List .std models currently open in running STAAD.Pro instances (from the "
                                   "Windows Running Object Table). Use a returned path as model_path in "
                                   "staad_connect to pick an instance.", None)}
