from pathlib import Path


def test_commercial_firmware_tracks_reviewed_implementation():
    firmware_root = Path(__file__).resolve().parents[1] / "firmware"
    legacy = (firmware_root / "needle_controller" / "needle_controller.ino").read_text(
        encoding="utf-8"
    )
    commercial = (
        firmware_root
        / "commercial_needle_controller"
        / "commercial_needle_controller.ino"
    ).read_text(encoding="utf-8")
    assert commercial.rstrip() == (
        "#define COMMERCIAL_RUNTIME_CONFIG 1\n" + legacy
    ).rstrip()
