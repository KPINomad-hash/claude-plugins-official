import pytest

from structural_mcp.core.errors import AdapterError
from structural_mcp.sap2000.adapter import _out


def test_out_checks_return_code_and_shape():
    assert _out(0, 0, "x") == []
    assert _out([1.0, 2.0, 3.0, 0], 3, "x") == [1.0, 2.0, 3.0]
    with pytest.raises(AdapterError, match="return code 1"):
        _out([1.0, 1], 1, "x")
    with pytest.raises(AdapterError, match="expected 3 outputs"):
        _out([1.0, 0], 3, "x")  # shifted/short result must not be read as data
    with pytest.raises(AdapterError, match="not an integer"):
        _out([0, "name"], 1, "x")  # code-first convention is rejected, not misread


def test_model_info(sap_service):
    info = sap_service.get_model_info()
    assert info["units"]["force"] == "kN" and info["units"]["length"] == "m"
    assert info["counts"] == {"joints": 2, "frames": 1, "areas": 0, "load_cases": 3}
    assert info["results"]["available"] is True
    assert info["revision"]["id"] and info["is_working_copy"] is False


def test_list_elements_and_cases(sap_service):
    joints = sap_service.list_elements("joint", 0, 1, None)
    assert joints["total"] == 2 and joints["has_more"] and joints["next_offset"] == 1
    assert joints["items"][0]["restraint_U1_U2_U3_R1_R2_R3"] == [True] * 6
    frame = sap_service.list_elements("frame", 0, 10, None)["items"][0]
    assert (frame["joint_i"], frame["joint_j"], frame["section"], frame["material"]) == ("1", "2", "W200", "S355")
    missing = sap_service.list_elements("joint", 0, 10, ["2", "99"])
    assert [i["id"] for i in missing["items"]] == ["2"] and "99" in missing["diagnostics"][0]
    cases = {c["id"]: c for c in sap_service.list_load_cases("all")["items"]}
    assert cases["MODAL"]["type"] == "Modal" and cases["P"]["analysis_status"] == "finished"
    assert cases["C1"]["members"] == [{"name": "DEAD", "member_kind": "case", "factor": 1.0},
                                      {"name": "P", "member_kind": "case", "factor": 1.5}]
    with pytest.raises(AdapterError, match="Unknown element_type"):
        sap_service.list_elements("plate", 0, 10, None)


def test_cantilever_reaction_and_combination(sap_service):
    res = sap_service.get_results("support_reactions", ["P", "C1"], None, {}, 0, 100)
    by_case = {r["load_case"]: r for r in res["rows"]}
    assert by_case["P"]["F3"] == 10.0 and abs(by_case["P"]["M2"]) == 30.0
    assert by_case["C1"]["F3"] == 15.0
    assert res["units"]["moment"] == "kN*m" and "joint local" in res["coordinate_system"]
    assert res["revision"]["id"]


def test_frame_forces_stations_and_paging(sap_service):
    res = sap_service.get_results("frame_forces", ["P"], ["1"], {}, 0, 2)
    assert res["total"] == 3 and res["count"] == 2 and res["has_more"]
    assert res["rows"][0]["station"] == 0.0 and res["rows"][0]["M3"] == -30.0
    last = sap_service.get_results("frame_forces", ["P"], ["1"], {}, 2, 2)["rows"][0]
    assert last["station"] == 3.0 and last["M3"] == 0.0 and last["status"] == "ok"  # a real zero


def test_no_rows_is_not_zero(sap_service):
    res = sap_service.get_results("joint_displacements", ["P"], ["2"], {}, 0, 10)
    assert res["rows"] == [{"status": "no_result", "joint": "2", "message": "SAP2000 returned no rows"}]


def test_modal(sap_service):
    res = sap_service.get_results("modal_periods", [], None, {}, 0, 10)
    assert [r["period_s"] for r in res["rows"]] == [0.5, 0.1]
    ratios = sap_service.get_results("modal_mass_ratios", [], None, {}, 0, 10)["rows"]
    assert ratios[0]["UX"] == 0.6 and ratios[0]["SumRZ"] == 0.6


def test_results_require_finished_analysis(sap_service, sap_adapter):
    sap_adapter._model.analysed = False
    with pytest.raises(AdapterError, match="no_results"):
        sap_service.get_results("support_reactions", ["P"], None, {}, 0, 10)
    with pytest.raises(AdapterError, match="neither a load case"):
        sap_adapter._model.analysed = True
        sap_service.get_results("support_reactions", ["NOPE"], None, {}, 0, 10)
