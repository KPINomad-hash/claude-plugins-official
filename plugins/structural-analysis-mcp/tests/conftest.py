import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from structural_mcp.core.config import Settings  # noqa: E402
from structural_mcp.core.server import Service  # noqa: E402
from structural_mcp.sap2000.adapter import Sap2000Adapter  # noqa: E402

from fake_sap import FakeSapModel  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    return Settings(workdir=tmp_path / "work", model_dirs=[models], timeout=5)


@pytest.fixture
def sap_adapter(settings):
    adapter = Sap2000Adapter()
    model_file = settings.model_dirs[0] / "cantilever.sdb"
    model_file.write_bytes(b"fake sdb")
    adapter._model = FakeSapModel(str(model_file))
    adapter._sap = object()
    return adapter


@pytest.fixture
def sap_service(sap_adapter, settings):
    return Service(sap_adapter, settings)
