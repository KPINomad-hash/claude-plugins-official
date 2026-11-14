import time

import pytest

from structural_mcp.core import sandbox
from structural_mcp.core.comthread import ComThread
from structural_mcp.core.errors import AdapterError


def test_open_working_copy_leaves_source_untouched(sap_service, settings):
    source = settings.model_dirs[0] / "cantilever.sdb"
    before = source.read_bytes()
    out = sap_service.open_working_copy(str(source))
    assert out["is_working_copy"] and out["working_copy"] != str(source)
    assert sandbox.is_inside(out["working_copy"], settings.workdir / "copies")
    assert source.read_bytes() == before
    assert out["revision"]["id"] == out["source_revision"]["id"]


def test_paths_outside_allowed_folders_are_refused(sap_service, tmp_path):
    outside = tmp_path / "secret.sdb"
    outside.write_bytes(b"x")
    with pytest.raises(AdapterError, match="not_allowed"):
        sap_service.open_working_copy(str(outside))
    link = sap_service.settings.model_dirs[0] / "link.sdb"
    link.symlink_to(outside)  # a link inside an allowed folder must not open what is outside
    with pytest.raises(AdapterError, match="not_allowed"):
        sap_service.open_working_copy(str(link))
    with pytest.raises(AdapterError, match="is not a SAP2000 model"):
        (sap_service.settings.model_dirs[0] / "a.txt").write_text("x")
        sap_service.open_working_copy(str(sap_service.settings.model_dirs[0] / "a.txt"))


def test_export_csv_and_no_overwrite(sap_service, settings):
    out = sap_service.export_results("frame_forces", ["P"], None, {}, "csv", "../../evil")
    assert out["rows_written"] == 3
    assert sandbox.is_inside(out["path"], settings.workdir / "exports")
    text = open(out["path"], encoding="utf-8-sig").read()
    assert text.splitlines()[0].startswith("status;frame;station")
    with pytest.raises(AdapterError, match="never overwrite"):
        sap_service.export_results("frame_forces", ["P"], None, {}, "csv", "evil")
    xlsx = sap_service.export_results("support_reactions", ["P"], None, {}, "xlsx", None)
    assert xlsx["path"].endswith(".xlsx")


def test_analysis_is_gated(sap_service, settings):
    with pytest.raises(AdapterError, match="disabled"):
        sap_service.run_analysis(None)
    settings.allow_analysis = True
    with pytest.raises(AdapterError, match="only run on working copies"):
        sap_service.run_analysis(None)
    sap_service.open_working_copy(str(settings.model_dirs[0] / "cantilever.sdb"))
    job = sap_service.run_analysis(["P"])
    for _ in range(50):
        status = sap_service.get_job_status(job["job_id"])
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "finished" and status["outcome"]["case_status"]["P"] == "finished"
    assert sap_service.adapter._model.Analyze.flags == [("", False, True), ("P", True, False)]


def test_timeout_then_busy_then_recovers():
    com = ComThread("t")
    with pytest.raises(AdapterError, match="timeout"):
        com.call(lambda: time.sleep(0.4), "slow", 0.05)
    with pytest.raises(AdapterError, match="busy"):
        com.call(lambda: 1, "next", 1)
    time.sleep(0.5)
    assert com.call(lambda: 2, "next", 1) == 2


def test_journal_written(sap_service, settings):
    sap_service.journal.record("t", {"a": 1}, "ok", revision="abc")
    assert '"tool": "t"' in (settings.workdir / "journal" / "sap2000.jsonl").read_text(encoding="utf-8")
