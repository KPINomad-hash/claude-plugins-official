"""SAP2000 adapter over the CSI OAPI (local COM, comtypes).

Return convention: called through comtypes, an OAPI method returns its ByRef
outputs in declaration order followed by the integer return code, e.g.
`[NumberResults, Obj, Elm, ..., ret]`; a method without outputs returns `ret`
alone. `_out()` checks the code and the expected output count, so a build with
a different convention fails loudly instead of yielding shifted columns.

The adapter never calls SetPresentUnits: values are reported in the model's
present units, which are stated with every result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import errors, winreg_probe
from ..core.adapter import Adapter, ResultSet, row
from ..core.errors import AdapterError

PROGID = "CSI.SAP2000.API.SapObject"
HELPER_PROGID = "SAP2000v1.Helper"

# eUnits enumeration of the OAPI
UNITS = {
    1: ("lb", "in", "F"), 2: ("lb", "ft", "F"), 3: ("kip", "in", "F"), 4: ("kip", "ft", "F"),
    5: ("kN", "mm", "C"), 6: ("kN", "m", "C"), 7: ("kgf", "mm", "C"), 8: ("kgf", "m", "C"),
    9: ("N", "mm", "C"), 10: ("N", "m", "C"), 11: ("tonf", "mm", "C"), 12: ("tonf", "m", "C"),
    13: ("kN", "cm", "C"), 14: ("kgf", "cm", "C"), 15: ("N", "cm", "C"), 16: ("tonf", "cm", "C"),
}
CASE_STATUS = {1: "not_run", 2: "could_not_start", 3: "not_finished", 4: "finished"}
CASE_TYPES = {
    1: "LinearStatic", 2: "NonlinearStatic", 3: "Modal", 4: "ResponseSpectrum", 5: "LinearHistory",
    6: "NonlinearHistory", 7: "LinearDynamic", 8: "NonlinearDynamic", 9: "MovingLoad", 10: "Buckling",
    11: "SteadyState", 12: "PowerSpectralDensity", 13: "LinearStaticMultiStep", 14: "HyperStatic",
}
COMBO_TYPES = {0: "LinearAdd", 1: "Envelope", 2: "AbsoluteAdd", 3: "SRSS", 4: "RangeAdd"}
ITEM_OBJECT, ITEM_GROUP = 0, 2


def _out(result: Any, n_out: int, what: str) -> list[Any]:
    """Split an OAPI call result into outputs, raising on a non-zero return code."""
    if n_out == 0:
        ret, outs = (result[-1], []) if isinstance(result, (list, tuple)) else (result, [])
    else:
        if not isinstance(result, (list, tuple)) or len(result) != n_out + 1:
            raise AdapterError(
                errors.API_ERROR,
                f"{what}: expected {n_out} outputs + return code, got {result!r:.200}.",
                "The OAPI signature or the COM return convention differs on this SAP2000 build; "
                "check the method in CSI_OAPI_Documentation.chm.",
            )
        ret, outs = result[-1], list(result[:-1])
    if not isinstance(ret, int) or isinstance(ret, bool):
        raise AdapterError(errors.API_ERROR, f"{what}: return code is not an integer ({ret!r}).",
                           "COM return convention differs from the one this adapter expects (code last).")
    if ret != 0:
        raise AdapterError(errors.API_ERROR, f"{what} failed with OAPI return code {ret}.")
    return outs


def _seq(value: Any) -> list[Any]:
    return [] if value is None else list(value)


class Sap2000Adapter(Adapter):
    program = "sap2000"
    title = "SAP2000"
    model_extensions = (".sdb",)
    supports_analysis = True
    element_types = {
        "joint": "point objects: coordinates (global XYZ) and restraints",
        "frame": "frame objects: end joints, section, material, local axis angle",
        "area": "area objects: corner joints and section property",
    }
    result_types = {
        "joint_displacements": "U1,U2,U3,R1,R2,R3 of joints (joint local axes; = global unless redefined)",
        "support_reactions": "F1,F2,F3,M1,M2,M3 at restrained joints (joint local axes)",
        "frame_forces": "P,V2,V3,T,M2,M3 along frame objects at output stations (frame local axes)",
        "modal_periods": "period, frequency, circular frequency, eigenvalue per mode",
        "modal_mass_ratios": "modal participating mass ratios UX..RZ with running sums",
    }

    def __init__(self) -> None:
        self._sap: Any = None
        self._model: Any = None

    # ---- connection -------------------------------------------------------------
    def probe(self) -> dict[str, Any]:
        info: dict[str, Any] = {"com": [winreg_probe.progid_info(PROGID), winreg_probe.progid_info(HELPER_PROGID)]}
        try:
            import comtypes  # type: ignore

            info["comtypes"] = comtypes.__version__
        except ImportError:
            info["comtypes"] = None
            info["note"] = "Package 'comtypes' is not installed; the adapter cannot talk to SAP2000."
        info["options"] = {
            "frame_forces / joint_displacements / support_reactions": "element_ids omitted = whole model (group ALL)",
        }
        return info

    def _client(self) -> Any:
        try:
            import comtypes.client  # type: ignore
        except ImportError:
            raise AdapterError(errors.MISSING_DEPENDENCY, "Package 'comtypes' is required (Windows only).",
                               "Install with: pip install comtypes") from None
        return comtypes.client

    def connect(self, model_path: str | None = None, start_if_needed: bool = False) -> dict[str, Any]:
        client = self._client()
        started = False
        try:
            sap = client.GetActiveObject(PROGID)
        except OSError:
            if not start_if_needed:
                raise AdapterError(errors.NOT_CONNECTED, "No running SAP2000 instance found.",
                                   "Start SAP2000 and open a model, or call connect with "
                                   "start_if_needed=true.") from None
            try:
                sap = client.CreateObject(PROGID)
                _out(sap.ApplicationStart(), 0, "ApplicationStart")
                started = True
            except OSError as exc:
                raise AdapterError(errors.NOT_CONNECTED, f"SAP2000 could not be started: {exc}.",
                                   "Check the installation, the COM registration (run SAP2000 once as "
                                   "administrator) and the licence.") from None
        self._sap, self._model = sap, sap.SapModel
        out = {"attached": True, "started_new_instance": started, **self.program_info()}
        current = self.model_path()
        if model_path and current and Path(current).resolve() != Path(model_path).resolve():
            out["warning"] = (f"The attached instance holds '{current}', not '{model_path}'. SAP2000 exposes "
                              "one instance through COM; close the other instances to attach to the wanted one.")
        return out

    def is_connected(self) -> bool:
        if self._model is None:
            return False
        try:
            self._model.GetModelFilename(True)
            return True
        except Exception:  # noqa: BLE001 - a dead COM proxy raises COMError/OSError
            self._sap = self._model = None
            return False

    def _m(self) -> Any:
        if not self.is_connected():
            raise AdapterError(errors.NOT_CONNECTED, "Not attached to SAP2000.", "Call sap2000_connect first.")
        return self._model

    def program_info(self) -> dict[str, Any]:
        version, number = _out(self._m().GetVersion(), 2, "GetVersion")
        return {"name": "SAP2000", "version": version, "version_number": number}

    def open_model(self, path: Path) -> None:
        _out(self._m().File.OpenFile(str(path)), 0, f"File.OpenFile('{path}')")

    # ---- model ------------------------------------------------------------------
    def model_path(self) -> str | None:
        name = self._m().GetModelFilename(True)
        return name or None

    def _units(self) -> dict[str, str]:
        code = self._m().GetPresentUnits()
        force, length, temp = UNITS.get(code, ("?", "?", "?"))
        return {"force": force, "length": length, "temperature": temp, "moment": f"{force}*{length}",
                "rotation": "rad", "source": f"present units (eUnits={code})"}

    def _case_status(self) -> dict[str, str]:
        _n, names, status = _out(self._m().Analyze.GetCaseStatus(), 3, "Analyze.GetCaseStatus")
        return {n: CASE_STATUS.get(s, str(s)) for n, s in zip(_seq(names), _seq(status))}

    def model_info(self) -> dict[str, Any]:
        m = self._m()
        if not self.model_path() and m.PointObj.Count() == 0:
            raise AdapterError(errors.NO_MODEL, "SAP2000 is running but no model is open.",
                               "Open a model in SAP2000 or call sap2000_open_working_copy.")
        status = self._case_status()
        finished = sorted(n for n, s in status.items() if s == "finished")
        db = UNITS.get(m.GetDatabaseUnits(), ("?", "?", "?"))
        return {
            "units": self._units(),
            "database_units": {"force": db[0], "length": db[1], "temperature": db[2]},
            "counts": {"joints": m.PointObj.Count(), "frames": m.FrameObj.Count(), "areas": m.AreaObj.Count(),
                       "load_cases": len(status)},
            "model_locked": bool(m.GetModelIsLocked()),
            "results": {
                "available": bool(finished),
                "finished_cases": finished,
                "not_finished_cases": sorted(n for n, s in status.items() if s != "finished"),
                "detail": "Per-case status from Analyze.GetCaseStatus. SAP2000 deletes results when an "
                          "unlocked model is changed, so finished cases match the current model state.",
            },
        }

    def _names(self, obj: Any, what: str) -> list[str]:
        _n, names = _out(obj.GetNameList(), 2, f"{what}.GetNameList")
        return [str(x) for x in _seq(names)]

    def element_ids(self, element_type: str) -> list[str]:
        m = self._m()
        obj = {"joint": m.PointObj, "frame": m.FrameObj, "area": m.AreaObj}[element_type]
        return self._names(obj, element_type)

    def describe_elements(self, element_type: str, ids: list[str]) -> list[dict[str, Any]]:
        m = self._m()
        length = self._units()["length"]
        out: list[dict[str, Any]] = []
        for name in ids:
            if element_type == "joint":
                x, y, z = _out(m.PointObj.GetCoordCartesian(name), 3, f"PointObj.GetCoordCartesian({name})")
                (restraint,) = _out(m.PointObj.GetRestraint(name), 1, f"PointObj.GetRestraint({name})")
                out.append({"id": name, "x": x, "y": y, "z": z, "length_unit": length,
                            "coordinate_system": "Global",
                            "restraint_U1_U2_U3_R1_R2_R3": [bool(v) for v in _seq(restraint)]})
            elif element_type == "frame":
                p1, p2 = _out(m.FrameObj.GetPoints(name), 2, f"FrameObj.GetPoints({name})")
                section, _auto = _out(m.FrameObj.GetSection(name), 2, f"FrameObj.GetSection({name})")
                angle, advanced = _out(m.FrameObj.GetLocalAxes(name), 2, f"FrameObj.GetLocalAxes({name})")
                try:
                    (material,) = _out(m.PropFrame.GetMaterial(section), 1, "PropFrame.GetMaterial")
                except AdapterError:
                    material = None  # e.g. section "None" or a non-prismatic/auto-select section
                out.append({"id": name, "joint_i": p1, "joint_j": p2, "section": section, "material": material,
                            "local_axis_angle_deg": angle, "advanced_local_axes": bool(advanced)})
            else:
                _n, points = _out(m.AreaObj.GetPoints(name), 2, f"AreaObj.GetPoints({name})")
                (prop,) = _out(m.AreaObj.GetProperty(name), 1, f"AreaObj.GetProperty({name})")
                out.append({"id": name, "joints": _seq(points), "section": prop})
        return out

    def list_load_cases(self, kind: str) -> list[dict[str, Any]]:
        m = self._m()
        items: list[dict[str, Any]] = []
        if kind in ("cases", "all"):
            status = self._case_status()
            for name in self._names(m.LoadCases, "LoadCases"):
                case_type, _sub = _out(m.LoadCases.GetTypeOAPI(name), 2, f"LoadCases.GetTypeOAPI({name})")
                items.append({"id": name, "kind": "case", "type": CASE_TYPES.get(case_type, str(case_type)),
                              "analysis_status": status.get(name, "unknown")})
        if kind in ("combinations", "all"):
            for name in self._names(m.RespCombo, "RespCombo"):
                (combo_type,) = _out(m.RespCombo.GetTypeOAPI(name), 1, f"RespCombo.GetTypeOAPI({name})")
                _n, ctypes_, cnames, factors = _out(m.RespCombo.GetCaseList(name), 4,
                                                    f"RespCombo.GetCaseList({name})")
                items.append({
                    "id": name, "kind": "combination", "type": COMBO_TYPES.get(combo_type, str(combo_type)),
                    "members": [{"name": cn, "member_kind": "case" if ct == 0 else "combination", "factor": sf}
                                for ct, cn, sf in zip(_seq(ctypes_), _seq(cnames), _seq(factors))],
                })
        return items

    # ---- results ----------------------------------------------------------------
    def _select_output(self, load_cases: list[str]) -> None:
        m = self._m()
        setup = m.Results.Setup
        _out(setup.DeselectAllCasesAndCombosForOutput(), 0, "Results.Setup.DeselectAllCasesAndCombosForOutput")
        cases = set(self._names(m.LoadCases, "LoadCases"))
        combos = set(self._names(m.RespCombo, "RespCombo"))
        for name in load_cases:
            if name in cases:
                _out(setup.SetCaseSelectedForOutput(name), 0, f"SetCaseSelectedForOutput({name})")
            elif name in combos:
                _out(setup.SetComboSelectedForOutput(name), 0, f"SetComboSelectedForOutput({name})")
            else:
                raise AdapterError(errors.INVALID_INPUT, f"'{name}' is neither a load case nor a combination.",
                                   "Use names from sap2000_list_load_cases.")

    def _targets(self, element_ids: list[str] | None) -> list[tuple[str, int]]:
        return [(i, ITEM_OBJECT) for i in element_ids] if element_ids else [("ALL", ITEM_GROUP)]

    def get_results(self, result_type: str, load_cases: list[str], element_ids: list[str] | None,
                    options: dict[str, Any]) -> ResultSet:
        m = self._m()
        res = m.Results
        units = self._units()
        diagnostics: list[str] = []
        rows: list[dict[str, Any]] = []
        modal = result_type.startswith("modal_")

        status = self._case_status()
        if not any(s == "finished" for s in status.values()):
            raise AdapterError(errors.NO_RESULTS, "No load case of this model has finished analysis.",
                               "Run the analysis in SAP2000 (or sap2000_run_analysis on a working copy).")
        if modal and not load_cases:
            load_cases = [c["id"] for c in self.list_load_cases("cases") if c["type"] == "Modal"]
        if not load_cases:
            raise AdapterError(errors.INVALID_INPUT, "load_cases is required for this result type.",
                               "Pass one or more names from sap2000_list_load_cases.")
        for name in load_cases:
            if name in status and status[name] != "finished":
                diagnostics.append(f"Case '{name}' status is '{status[name]}': no results for it.")
        self._select_output(load_cases)

        def table(result: Any, n_out: int, what: str) -> list[list[Any]]:
            outs = _out(result, n_out, what)
            count = outs[0]
            return [_seq(col)[:count] for col in outs[1:]]

        if result_type in ("joint_displacements", "support_reactions"):
            fn, comps = ((res.JointDispl, ("U1", "U2", "U3", "R1", "R2", "R3"))
                         if result_type == "joint_displacements"
                         else (res.JointReact, ("F1", "F2", "F3", "M1", "M2", "M3")))
            for target, item in self._targets(element_ids):
                cols = table(fn(target, item), 12, f"Results ({result_type})")
                if not cols[0]:
                    if item == ITEM_OBJECT:
                        rows.append(row("no_result", joint=target, message="SAP2000 returned no rows"))
                    continue
                for obj, elm, case, stype, snum, *vals in zip(*cols):
                    rows.append(row(joint=obj, element=elm, load_case=case, step_type=stype, step_num=snum,
                                    **dict(zip(comps, vals))))
            coord = "joint local axes 1-2-3 (identical to global X-Y-Z unless joint local axes were redefined)"
        elif result_type == "frame_forces":
            for target, item in self._targets(element_ids):
                cols = table(res.FrameForce(target, item), 14, "Results.FrameForce")
                if not cols[0]:
                    if item == ITEM_OBJECT:
                        rows.append(row("no_result", frame=target, message="SAP2000 returned no rows"))
                    continue
                for obj, osta, elm, esta, case, stype, snum, p, v2, v3, t, m2, m3 in zip(*cols):
                    rows.append(row(frame=obj, station=osta, element=elm, element_station=esta, load_case=case,
                                    step_type=stype, step_num=snum, P=p, V2=v2, V3=v3, T=t, M2=m2, M3=m3))
            coord = "frame local axes 1-2-3; station measured from joint I in length units"
        elif result_type == "modal_periods":
            cols = table(res.ModalPeriod(), 8, "Results.ModalPeriod")
            for case, stype, snum, period, freq, circ, eig in zip(*cols):
                rows.append(row(load_case=case, step_type=stype, mode=snum, period_s=period, frequency_hz=freq,
                                circular_frequency_rad_s=circ, eigenvalue_rad2_s2=eig))
            coord = "n/a"
        else:  # modal_mass_ratios
            cols = table(res.ModalParticipatingMassRatios(), 17, "Results.ModalParticipatingMassRatios")
            keys = ("UX", "UY", "UZ", "SumUX", "SumUY", "SumUZ", "RX", "RY", "RZ", "SumRX", "SumRY", "SumRZ")
            for case, stype, snum, period, *vals in zip(*cols):
                rows.append(row(load_case=case, step_type=stype, mode=snum, period_s=period,
                                **dict(zip(keys, vals))))
            coord = "global X-Y-Z (ratios are dimensionless fractions of total mass)"

        if not rows:
            diagnostics.append("SAP2000 returned no rows for this request (no_result, not zero).")
        return ResultSet(rows=rows, units=units, coordinate_system=coord, diagnostics=diagnostics)

    # ---- analysis ---------------------------------------------------------------
    def run_analysis(self, load_cases: list[str] | None) -> dict[str, Any]:
        m = self._m()
        if load_cases:
            _out(m.Analyze.SetRunCaseFlag("", False, True), 0, "Analyze.SetRunCaseFlag(all, False)")
            for name in load_cases:
                _out(m.Analyze.SetRunCaseFlag(name, True, False), 0, f"Analyze.SetRunCaseFlag({name})")
        _out(m.Analyze.RunAnalysis(), 0, "Analyze.RunAnalysis")
        return {"return_code": 0, "case_status": self._case_status()}
