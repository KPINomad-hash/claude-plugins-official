"""In-memory stand-in for the SAP2000 OAPI, following the comtypes return
convention (outputs..., ret). Model: 3 m cantilever along X, fixed at joint 1,
10 kN downward at joint 2, units kN-m. Used to test parsing, not engineering."""

from __future__ import annotations


class _Obj:
    def __init__(self, names):
        self.names = list(names)

    def Count(self):
        return len(self.names)

    def GetNameList(self):
        return [len(self.names), tuple(self.names), 0]


class _Points(_Obj):
    coords = {"1": (0.0, 0.0, 0.0), "2": (3.0, 0.0, 0.0)}

    def GetCoordCartesian(self, name):
        return [*self.coords[name], 0]

    def GetRestraint(self, name):
        return [(name == "1",) * 6, 0]


class _Frames(_Obj):
    def GetPoints(self, name):
        return ["1", "2", 0]

    def GetSection(self, name):
        return ["W200", "", 0]

    def GetLocalAxes(self, name):
        return [0.0, False, 0]


class _Areas(_Obj):
    pass


class _PropFrame:
    def GetMaterial(self, name):
        return ["S355", 0]


class _LoadCases(_Obj):
    types = {"DEAD": 1, "P": 1, "MODAL": 3}

    def GetTypeOAPI(self, name):
        return [self.types[name], 0, 0]


class _Combos(_Obj):
    def GetTypeOAPI(self, name):
        return [0, 0]

    def GetCaseList(self, name):
        return [2, (0, 0), ("DEAD", "P"), (1.0, 1.5), 0]


class _Analyze:
    def __init__(self, model):
        self.model = model
        self.flags = []

    def GetCaseStatus(self):
        names = ("DEAD", "P", "MODAL")
        return [3, names, tuple(4 if self.model.analysed else 1 for _ in names), 0]

    def SetRunCaseFlag(self, name, run, all_=False):
        self.flags.append((name, run, all_))
        return 0

    def RunAnalysis(self):
        self.model.analysed = True
        return 0


class _Setup:
    def __init__(self):
        self.selected = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.selected = []
        return 0

    def SetCaseSelectedForOutput(self, name):
        self.selected.append(name)
        return 0

    def SetComboSelectedForOutput(self, name):
        self.selected.append(name)
        return 0


class _Results:
    def __init__(self):
        self.Setup = _Setup()

    def _factor(self, case):
        return {"DEAD": 0.0, "P": 1.0, "C1": 1.5}[case]

    def JointReact(self, name, item):
        cases = [c for c in self.Setup.selected if c != "MODAL"]
        if name not in ("ALL", "1"):
            return [0, (), (), (), (), (), (), (), (), (), (), (), 0]
        n = len(cases)
        f = [self._factor(c) for c in cases]
        z = (0.0,) * n
        return [n, ("1",) * n, ("1",) * n, tuple(cases), ("",) * n, (0.0,) * n,
                z, z, tuple(10.0 * k for k in f), z, tuple(-30.0 * k for k in f), z, 0]

    def JointDispl(self, name, item):
        return [0, None, None, None, None, None, None, None, None, None, None, None, 0]

    def FrameForce(self, name, item):
        cases = [c for c in self.Setup.selected if c != "MODAL"]
        rows = [(c, s) for c in cases for s in (0.0, 1.5, 3.0)]
        n = len(rows)
        z = (0.0,) * n
        return [n, ("1",) * n, tuple(s for _, s in rows), ("1-1",) * n, tuple(s for _, s in rows),
                tuple(c for c, _ in rows), ("",) * n, (0.0,) * n,
                z, tuple(-10.0 * self._factor(c) for c, _ in rows), z, z, z,
                tuple(-10.0 * self._factor(c) * (3.0 - s) for c, s in rows), 0]

    def ModalPeriod(self):
        return [2, ("MODAL",) * 2, ("Mode",) * 2, (1.0, 2.0), (0.5, 0.1), (2.0, 10.0), (12.566, 62.83),
                (157.9, 3947.8), 0]

    def ModalParticipatingMassRatios(self):
        v = (0.6, 0.2)
        return [2, ("MODAL",) * 2, ("Mode",) * 2, (1.0, 2.0), (0.5, 0.1), *([v] * 12), 0]


class _File:
    def __init__(self, model):
        self.model = model

    def OpenFile(self, path):
        self.model.path = path
        self.model.analysed = False
        return 0


class FakeSapModel:
    def __init__(self, path="", analysed=True):
        self.path = path
        self.analysed = analysed
        self.PointObj = _Points(["1", "2"])
        self.FrameObj = _Frames(["1"])
        self.AreaObj = _Areas([])
        self.PropFrame = _PropFrame()
        self.LoadCases = _LoadCases(["DEAD", "P", "MODAL"])
        self.RespCombo = _Combos(["C1"])
        self.Analyze = _Analyze(self)
        self.Results = _Results()
        self.File = _File(self)

    def GetModelFilename(self, include_path=True):
        return self.path

    def GetVersion(self):
        return ["26.0.0", 26.0, 0]

    def GetPresentUnits(self):
        return 6

    def GetDatabaseUnits(self):
        return 6

    def GetModelIsLocked(self):
        return self.analysed
