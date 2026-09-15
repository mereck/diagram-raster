from pathlib import Path

import pytest
from PIL import Image

from diagram_raster.ledger import Ledger
from diagram_raster.models import Diagram


class StubRenderer:
    """Writes a small PNG instead of shelling out, and remembers what it rendered."""

    def __init__(self) -> None:
        self.rendered: list[Diagram] = []

    def render(self, diagram: Diagram, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 20), "white").save(out)
        self.rendered.append(diagram)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "store")


@pytest.fixture
def renderer() -> StubRenderer:
    return StubRenderer()
