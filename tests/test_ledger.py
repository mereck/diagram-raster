from pathlib import Path

import pytest

from diagram_raster.ledger import Ledger, NoBriefbackError
from diagram_raster.models import Verdict


def test_pass_verdict_requires_a_captured_briefback(ledger: Ledger) -> None:
    with pytest.raises(NoBriefbackError):
        ledger.record_verdict("abc123", Verdict.PASS, intent="request flows api to db")


def test_fail_verdict_does_not_require_a_briefback(ledger: Ledger) -> None:
    ledger.record_verdict("abc123", Verdict.FAIL, intent="x")
    record = ledger.verdict("abc123")
    assert record is not None
    assert record.verdict is Verdict.FAIL


def test_pass_verdict_counts_briefbacks(ledger: Ledger) -> None:
    ledger.add_briefback("abc123", "Two boxes, api points at db.")
    ledger.add_briefback("abc123", "api calls db.")
    ledger.record_verdict("abc123", Verdict.PASS, intent="api calls db")
    record = ledger.verdict("abc123")
    assert record is not None
    assert record.briefback_count == 2
    assert [b.text for b in ledger.briefbacks("abc123")][-1] == "api calls db."


def test_png_paths_resolve_to_digests_only_inside_the_store(ledger: Ledger, tmp_path: Path) -> None:
    png = ledger.png_path("abc123")
    png.parent.mkdir(parents=True)
    png.write_bytes(b"png")
    outside = tmp_path / "abc123" / "render.png"
    outside.parent.mkdir()
    outside.write_bytes(b"png")
    assert ledger.digest_for_png(str(png)) == "abc123"
    assert ledger.digest_for_png(str(outside)) is None
    assert ledger.digest_for_png(str(ledger.png_path("0000000000000000"))) is None


@pytest.mark.parametrize("digest", ["../escape", "abc/def", "", "ABC123", "/tmp/x"])
def test_digests_that_are_not_hex_are_refused(ledger: Ledger, digest: str) -> None:
    with pytest.raises(ValueError):
        ledger.entry_dir(digest)


def test_a_tilde_for_an_unknown_user_is_not_a_render(ledger: Ledger) -> None:
    assert ledger.digest_for_png("~no-such-user-xyz/render.png") is None
