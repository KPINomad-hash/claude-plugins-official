import pytest

from structural_mcp.core.errors import AdapterError
from structural_mcp.core.server import Service
from structural_mcp.lira.adapter import NO_RESULTS_HRESULT, LiraAdapter, parse_numbers

TABLES = {
    "Nodes_Coordinates": [[1, 0.0, 0.0, 0.0], [2, 3.0, 0.0, 0.0]],
    "Elements_TypeAndNodes": [[1, 10, "1, 2"]],
    "Nodes_Restraints": [[1, "1", "1", "1", "1", "1", "1", ""]],
    "Elements_Stiffnesses": [[1, 1]],
    "Stiffnesses": [[1, 0, "#ff0000", "beam", "Ro:2.75 E:3e+006 B:60 H:40 STD_END"]],
    "LoadCases": [[1, "DEAD", "", "Постоянное(П)", ""], [2, "P", "", "Кратковременное(К)", ""]],
}


class _Consts:
    kLiraModelPart_All, kLiraModelPart_FromArray = 0, 3
    kLiraRequest_MainData, kLiraRequest_LoadCaseDisplacement, kLiraRequest_LoadCaseForces = 10, 1, 2
    kLiraRequest_PeriodsOfVibrations, kLiraRequest_LoadCombinationDisplacements = 8, 6
    __dicts__ = [{f"kLiraTable_{n}": i for i, n in enumerate(["AllTables", "Group", *TABLES, "Cnt"])}]

    def __getattr__(self, name):
        return self.__dicts__[0][name]


class _Item:
    def __init__(self, kind, pars, name):
        self.kind, self.pars, self.Name = kind, pars, name

    def GetContents(self, _):
        name = [n for n, i in _Consts.__dicts__[0].items() if i == self.kind][0][len("kLiraTable_"):]
        rows = TABLES[name]
        if not rows:
            raise RuntimeError("empty table")
        return tuple(tuple(r) for r in rows if self.pars is None or r[0] in self.pars)


class _Tables:
    def __init__(self):
        self.items = [_Item(0, None, "user table")]

    def CreateNewItem(self, kind, pars, part, name, at):
        self.items.append(_Item(kind, pars, name))
        return self.items[-1]

    @property
    def ItemCount(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i]

    def Delete(self, i):
        del self.items[i]


class _Doc:
    def __init__(self, path):
        self.PathName, self.Title, self.AllTables = path, "c", _Tables()

    def IsModified(self):
        return False


class _App:
    def __init__(self, doc):
        self.ActiveDocument, self.DocumentCount = doc, 1

    def Document(self, i):
        return self.ActiveDocument


class _Longs:
    def __init__(self, values=()):
        self.values = list(values)

    def AddFromString(self, text):
        self.values += parse_numbers(text)

    def AddToString(self):
        return " ".join(map(str, self.values))

    @property
    def Count(self):
        return len(self.values)

    def Item(self, i):
        return self.values[i]


class _Req:
    def __init__(self):
        self.Nodes, self.Elements, self.LoadCases = _Longs(), _Longs(), _Longs()


class _CaseInfo:
    Number, SummaryShapeCount = 2, 0
    DeformedShapes = _Longs()


class _Infos:
    Count = 1

    def Item(self, i):
        return _CaseInfo()


class _Resp:
    def __init__(self, req):
        self.req = req
        self.LoadCases = _Infos()
        self.NodeNumbers = _Longs(req.Nodes.values or [1, 2])
        self.LoadCaseNumbers = _Longs([5])
        self.bar_calls = []

    def GetNodeCount(self): return 2
    def GetElementCount(self): return 1
    def GetFamily(self, el): return 1
    def GetForces(self, el): return _Longs([1, 3, 4])
    def GetSectionCount(self, el): return 2
    def GetBarN(self, el, cs, lc, pos, hist): return 0.0
    def GetBarQz(self, el, cs, lc, pos, hist): return -1.0
    def GetBarMy(self, el, cs, lc, pos, hist):
        self.bar_calls.append((el, cs, lc, pos, hist))
        return -3.0 if cs == 1 else 0.0
    def GetPeriods(self, lc): return _Longs([0.5, 0.1])
    def GetFrequenciesHz(self, lc): return _Longs([2.0, 10.0])
    def GetModalMasses(self, lc): return _Longs([80.0, 10.0])
    def GetAccumulatedModalMasses(self, lc): return _Longs([80.0, 90.0])
    def GetDynamicLoadCaseType(self, lc): return 100

    def __getattr__(self, name):
        if name.startswith("GetNode"):
            return lambda node, lc, shape, hist: {"Z": -0.01}.get(name[7:], 0.0) if node == 2 else 0.0
        raise AttributeError(name)


class _Access:
    made = 0

    def __init__(self, fail):
        self.fail = fail
        _Access.made += 1

    def CreateNewRequest(self, kind):
        return _Req()

    def __getattr__(self, method):
        def call(req):
            if self.fail:
                exc = RuntimeError("com_error")
                exc.hresult = NO_RESULTS_HRESULT
                raise exc
            self.last = _Resp(req)
            return self.last
        return call


class FakeCom:
    fail_results = False

    def cast(self, obj, interface):
        return obj

    def results_access(self, root):
        self.access = _Access(self.fail_results)
        return self.access, _Consts()


@pytest.fixture
def lira(settings):
    com = FakeCom()
    adapter = LiraAdapter(com)
    f = settings.model_dirs[0] / "c.lir"
    f.write_bytes(b"lir")
    adapter._app, adapter._c, adapter._progid = _App(_Doc(str(f))), _Consts(), "LiraSapr.Application.2024"
    adapter._load_table_kinds()
    return Service(adapter, settings)


def test_parse_numbers():
    assert parse_numbers("1-3, 7 9") == [1, 2, 3, 7, 9] and parse_numbers("") == [] and parse_numbers(None) == []


def test_tables_and_scratch_cleanup(lira):
    assert sorted(lira.adapter.table_kinds) == sorted(TABLES)
    info = lira.get_model_info()
    assert info["counts"] == {"nodes": 2, "elements": 1, "load_cases": 2}
    assert info["results"]["available"] and info["results"]["matches_model_counts"]
    tables = lira.adapter._app.ActiveDocument.AllTables
    assert [t.Name for t in tables.items] == ["user table"]  # only our scratch tables were removed
    nodes = lira.list_elements("node", 0, 10, None)["items"]
    assert nodes[0]["restraint_X_Y_Z_UX_UY_UZ"] == [True] * 6 and nodes[1]["restraint_X_Y_Z_UX_UY_UZ"] is None
    el = lira.list_elements("element", 0, 10, None)["items"][0]
    assert el == {"id": "1", "fe_type": 10, "nodes": ["1", "2"], "stiffness": 1}
    assert lira.list_elements("stiffness", 0, 10, None)["items"][0]["parameters"].endswith("STD_END")
    with pytest.raises(AdapterError, match="not available in this LIRA"):
        lira.adapter.read_table("Bars_Hinges")


def test_displacements_fixed_units_and_fresh_access(lira):
    before = _Access.made
    res = lira.get_results("node_displacements", ["2"], ["2"], {}, 0, 10)
    assert res["rows"] == [{"status": "ok", "node": "2", "load_case": "2", "shape": 1, "X": 0.0, "Y": 0.0,
                            "Z": -0.01, "UX": 0.0, "UY": 0.0, "UZ": 0.0}]
    assert res["units"]["force"] == "tf" and res["units"]["length"] == "m"
    lira._result_cache = None
    lira.get_results("node_displacements", ["2"], ["2"], {}, 0, 10)
    assert _Access.made - before >= 2  # a new LiraResultsAccess per request


def test_element_forces_by_family(lira):
    res = lira.get_results("element_forces", ["2"], None, {}, 0, 10)
    assert [(r["section"], r["My"], r["Qz"], r["N"]) for r in res["rows"]] == [(1, -3.0, -1.0, 0.0),
                                                                               (2, 0.0, -1.0, 0.0)]
    assert res["rows"][0]["family"] == "bar" and "shape" not in res["rows"][0]
    assert lira.adapter._com.access.last.bar_calls[0] == (1, 1, 2, 1, 0)


def test_modal_and_missing_results(lira):
    rows = lira.get_results("modal", [], None, {}, 0, 10)["rows"]
    assert rows[1]["period_s"] == 0.1 and rows[1]["accumulated_modal_mass_percent"] == 90.0
    lira.adapter._com.fail_results = True
    with pytest.raises(AdapterError, match="no_results"):
        lira.get_results("element_forces", ["2"], ["1"], {}, 0, 10)
    assert lira.get_model_info()["results"]["available"] is False
    combos = lira.list_load_cases("all")["items"]
    assert [c["kind"] for c in combos] == ["case", "case", "diagnostic"]


def test_open_refuses_names_that_block_lira(lira, settings):
    bad = settings.model_dirs[0] / "frame.v2.lir"
    bad.write_bytes(b"x")
    with pytest.raises(AdapterError, match="blocking dialog"):
        lira.open_working_copy(str(bad))
