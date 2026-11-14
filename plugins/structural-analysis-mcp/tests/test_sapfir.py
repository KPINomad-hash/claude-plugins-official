import pytest

from structural_mcp.core.errors import AdapterError
from structural_mcp.core.server import Service
from structural_mcp.sapfir.adapter import SapfirAdapter
from structural_mcp.sapfir.ifc import decode_string, split_args

IFC = """ISO-10303-21;
HEADER;
FILE_SCHEMA(('IFC2X3'));
ENDSEC;
DATA;
#1=IFCBUILDINGSTOREY('g1',#9,'\\X2\\042D0442043004360031\\X0\\',$,$,#8,$,$,.ELEMENT.,3.3);
#2=IFCBUILDINGSTOREY('g0',#9,'Level, ''A''',$,$,#8,$,$,.ELEMENT.,0.);
#10=IFCCOLUMN('c1',#9,'K1',$,$,#8,#7,'T-1');
#11=IFCCOLUMN('c2',#9,'K2',$,$,#8,#7,'T-2');
#12=IFCBEAM('b1',#9,'B1',$,$,#8,#7,'T-3');
#13=IFCSLAB('s1',#9,'P1',$,$,#8,#7,'T-4',.FLOOR.);
#14=IFCWALLSTANDARDCASE('w1',#9,'W1',$,$,#8,#7,'T-5');
#20=IFCRELCONTAINEDINSPATIALSTRUCTURE('r1',#9,$,$,(#10,#11,
 #12),#2);
#21=IFCRELCONTAINEDINSPATIALSTRUCTURE('r2',#9,$,$,(#13),#1);
ENDSEC;
END-ISO-10303-21;
"""


@pytest.fixture
def sapfir(settings):
    adapter = SapfirAdapter(settings)
    (settings.model_dirs[0] / "m.ifc").write_text(IFC, encoding="utf-8")
    return Service(adapter, settings)


def test_step_helpers():
    assert decode_string("\\X2\\042D0442043004360031\\X0\\") == "Этаж1"
    assert split_args("'a, ''b''',$,(#1,#2),3.5") == ["'a, ''b'''", "$", "(#1,#2)", "3.5"]


def test_ifc_summary(sapfir, settings):
    out = sapfir.adapter.load_ifc_export(str(settings.model_dirs[0] / "m.ifc"))
    assert out["ifc_schema"] == "IFC2X3"
    assert [(s["name"], s["elevation"]) for s in out["storeys"]] == [("Level, 'A'", 0.0), ("Этаж1", 3.3)]
    assert out["storeys"][0]["elements"] == {"column": 2, "beam": 1}
    assert out["element_totals"] == {"column": 2, "beam": 1, "slab": 1, "wall": 1}
    assert "1 element(s) are not assigned" in out["diagnostics"][0]  # the wall
    cols = sapfir.list_elements("column", 0, 10, None)
    assert [c["tag"] for c in cols["items"]] == ["T-1", "T-2"] and cols["items"][0]["storey"] == "Level, 'A'"


def test_ifc_required_and_sandboxed(sapfir, tmp_path):
    with pytest.raises(AdapterError, match="No IFC export"):
        sapfir.list_elements("column", 0, 10, None)
    (tmp_path / "x.ifc").write_text(IFC)
    with pytest.raises(AdapterError, match="not_allowed"):
        sapfir.adapter.load_ifc_export(str(tmp_path / "x.ifc"))


class _Model:
    def __init__(self, code, ident):
        self.TypeModel, self.ID = code, ident

    def Parameter(self, name):
        if name == "M_THICKNESS" and self.TypeModel == 10:
            return 0.25
        if name == "M_NAME":
            return f"obj{self.ID}"
        raise RuntimeError("no such parameter")


class _Storey:
    ID = 100
    CountModel = 4
    models = [_Model(10, 1), _Model(12, 2), _Model(81, 3), _Model(77, 4)]

    def Parameter(self, name):
        return "Этаж 1" if name == "M_NAME" else 3.3

    def GetModelByIndex(self, i):
        return self.models[i]

    def GetName(self):
        return "1"

    def NewModel(self, kind):  # must never be reachable
        raise AssertionError("model was modified")


class _Project:
    ID, CountStorey = 7, 1

    def GetStoreyByIndex(self, i):
        return _Storey()

    def Parameter(self, name):
        return "Здание"


class _Doc:
    CountProjects, fullPath = 1, "C:\\m\\house.spf"

    def GetProjectByIndex(self, i):
        return _Project()

    def GetActiveStorey(self):
        return _Storey()


class _App:
    AppVersion = "2024"

    def GetActiveDoc(self):
        return _Doc()


def test_only_getters_can_be_called(sapfir):
    sapfir.adapter._app = _App()
    assert sapfir.adapter.read_property(["GetActiveDoc", "GetActiveStorey", "GetName"])["value"] == "1"
    for bad in (["GetActiveDoc", "GetActiveStorey", "NewModel"], ["DrawModel"], ["__class__"], ["Get Active"]):
        with pytest.raises(AdapterError, match="not_allowed"):
            sapfir.adapter.read_property(bad)
    info = sapfir.get_model_info()
    assert info["com"]["active_storey"] == "present"


def test_model_structure_over_com(sapfir):
    sapfir.adapter._app = _App()
    out = sapfir.adapter.get_model_structure(include_objects=True, parameters=["M_THICKNESS"])
    assert out["project_count"] == 1
    assert out["totals_by_type"] == {"wall": 1, "column": 1, "analytical_bar": 1, "type_77": 1}
    assert out["analytical_objects_in_storeys"] == {"analytical_bar": 1}
    storey = out["projects"][0]["storeys"][0]
    assert storey["name"] == "Этаж 1" and storey["M_THICKNESS"] == 3.3
    objects = storey["objects"]
    assert objects[0]["M_THICKNESS"] == 0.25 and objects[1]["M_THICKNESS"].startswith("unavailable")
    assert objects[0]["M_NAME"] == "obj1"
    assert sapfir.adapter.read_property(["GetActiveDoc", "CountProjects"])["value"] == 1
    assert sapfir.adapter.model_path() == "C:\\m\\house.spf"
    assert sapfir.adapter.program_info()["version"] == "2024"
    with pytest.raises(AdapterError, match="not a parameter mnemonic"):
        sapfir.adapter.get_model_structure(parameters=["M_X = 1"])
