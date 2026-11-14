import pytest

from structural_mcp.core.errors import AdapterError
from structural_mcp.core.server import Service
from structural_mcp.staad.adapter import StaadAdapter


class _Geometry:
    def GetNodeCount(self): return 2
    def GetMemberCount(self): return 1
    def GetPlateCount(self): return 0
    def GetNodeList(self): return (1, 2)
    def GetBeamList(self): return (7,)
    def GetPlateList(self): return ()
    def GetNodeCoordinates(self, n): return (0.0, 0.0, 0.0) if n == 1 else (3.0, 0.0, 0.0)
    def GetMemberIncidence(self, n): return (1, 2)
    def GetBeamLength(self, n): return 3.0
    def IsZUp(self): return False


class _Property:
    def GetBeamSectionName(self, n): return "W8X31"
    def GetBeamMaterialName(self, n): raise RuntimeError("no material")
    def GetBetaAngle(self, n): return 0


class _Support:
    def GetSupportCount(self): return 1
    def GetSupportNodes(self): return (1,)
    def GetSupportType(self, n): return 2 if n == 1 else 0


class _Load:
    def GetPrimaryLoadCaseCount(self): return 2
    def GetLoadCombinationCaseCount(self): return 1
    def GetPrimaryLoadCaseNumbers(self): return [1, 2]
    def GetLoadCombinationCaseNumbers(self): return (10,)
    def GetLoadCaseTitle(self, n): return {1: "DEAD", 2: "P", 10: "1.0D+1.5P"}[n]
    def GetLoadType(self, n): return 0 if n == 1 else 1
    def GetNoOfLoadAndFactorPairsForCombination(self, n): return 2
    def GetLoadAndFactorForCombination(self, n): return ((1, 2), (1.0, 1.5, 0.0))


class _Output:
    available = True
    calls = []
    def AreResultsAvailable(self): return self.available
    def GetSupportReactions(self, node, lc): return [0.0, 10.0 * (lc == 2), 0.0, 0.0, 0.0, 30.0 * (lc == 2)]
    def GetNodeDisplacements(self, node, lc): raise RuntimeError("[-2001] node not found")
    def GetMemberEndForces(self, member, end, lc, flag):
        self.calls.append((member, end, lc, flag))
        return [0.0, 10.0 if end == 0 else -10.0, 0.0, 0.0, 0.0, 30.0 if end == 0 else 0.0]
    def GetNoOfModesExtracted(self): return 1
    def GetModeFrequency(self, m): return 2.0
    def GetModalMassParticipationFactors(self, m): return (80.0, 0.0, 0.0)


class FakeStaad:
    def __init__(self, path):
        self.path = path
        self.Geometry, self.Property, self.Support = _Geometry(), _Property(), _Support()
        self.Load, self.Output = _Load(), _Output()
        self.silent = []
    def GetApplicationVersion(self): return "25.0.1.424"
    def GetSTAADFile(self, full=True): return self.path
    def GetBaseUnit(self): return "Metric"
    def IsAnalyzing(self): return False
    def GetAnalysisStatus(self): return {"ReturnValue": 2, "NoOfWarnings": 0, "NoOfErrors": 0}
    def SetSilentMode(self, v): self.silent.append(v)
    def AnalyzeEx(self, a, b, c): return 2
    def GetAnalysisErrorMessages(self): raise RuntimeError("update STAAD.Pro")


@pytest.fixture
def staad(settings):
    adapter = StaadAdapter()
    f = settings.model_dirs[0] / "c.std"
    f.write_text("STAAD SPACE")
    adapter._staad = FakeStaad(str(f))
    return Service(adapter, settings)


def test_model_and_elements(staad):
    info = staad.get_model_info()
    assert info["units"]["force"] == "kN" and info["units"]["length"] == "m" and info["vertical_axis"] == "Y"
    assert info["results"]["status"] == "completed"
    member = staad.list_elements("member", 0, 10, None)["items"][0]
    assert member == {"id": "7", "node_a": "1", "node_b": "2", "length": 3.0, "length_unit": "m",
                      "section": "W8X31", "material": None, "beta_angle_deg": 0}
    nodes = staad.list_elements("node", 0, 10, None)["items"]
    assert [n["support"] for n in nodes] == ["fixed", "none"]


def test_combination_factors_drop_trailing_element(staad):
    combo = staad.list_load_cases("combinations")["items"][0]
    assert combo["members"] == [{"name": "1", "factor": 1.0}, {"name": "2", "factor": 1.5}]


def test_reactions_default_to_supported_nodes(staad):
    res = staad.get_results("support_reactions", ["2"], None, {}, 0, 10)
    assert res["total"] == 1 and res["rows"][0]["FY"] == 10.0 and res["rows"][0]["MZ"] == 30.0
    assert res["coordinate_system"] == "global X-Y-Z" and res["units"]["moment"] == "kN*m"


def test_end_forces_axes_flag_and_ends(staad):
    staad.adapter._staad.Output.calls.clear()
    res = staad.get_results("member_end_forces", ["2"], ["7"], {"axes": "global"}, 0, 10)
    assert staad.adapter._staad.Output.calls == [(7, 0, 2, 1), (7, 1, 2, 1)]
    assert [r["end"] for r in res["rows"]] == ["A", "B"] and res["coordinate_system"] == "global X-Y-Z"
    staad.adapter._staad.Output.calls.clear()
    staad.get_results("member_end_forces", ["2"], ["7"], {}, 0, 10)
    assert staad.adapter._staad.Output.calls[0][3] == 0  # local by default
    with pytest.raises(AdapterError, match="options.axes"):
        staad.get_results("member_end_forces", ["2"], None, {"axes": "x"}, 0, 10)


def test_api_error_rows_are_not_zeros(staad):
    res = staad.get_results("node_displacements", ["2"], ["99"], {}, 0, 10)
    assert res["rows"][0]["status"] == "error" and "X" not in res["rows"][0]


def test_modal_and_no_results(staad):
    res = staad.get_results("modal", [], None, {}, 0, 10)
    assert res["rows"][0]["period_s"] == 0.5 and res["rows"][0]["participation_x_percent"] == 80.0
    staad.adapter._staad.Output.available = False
    try:
        with pytest.raises(AdapterError, match="no_results"):
            staad.get_results("support_reactions", ["2"], None, {}, 0, 10)
    finally:
        staad.adapter._staad.Output.available = True
    with pytest.raises(AdapterError, match="must be STAAD numbers"):
        staad.get_results("support_reactions", ["DEAD"], None, {}, 0, 10)


def test_run_analysis_restores_silent_mode(staad):
    out = staad.adapter.run_analysis(None)
    assert out["status"] == "completed" and staad.adapter._staad.silent == [True, False]
    with pytest.raises(AdapterError, match="unsupported"):
        staad.adapter.run_analysis(["1"])
