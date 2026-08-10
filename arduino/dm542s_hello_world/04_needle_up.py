"""Script 04: move the needle UP once, by the distance in its YAML file.

UP is this rig's name for the motion the shared motion core calls "forward",
which is a POSITIVE step count on the wire (``MOVE +200``). The mapping is
stated in every preflight so the operator label and the firmware sign can never
drift apart.

Ordinary needle-motion scripts use low sequential numbers (01, 02, 03, ...).
Calibration, maintenance, and diagnostic scripts use high numbers (99, 98, ...).
See README.md and OPERATOR_GUIDE.md.

This script only ever moves up. Direction is fixed here in code, not in the
configuration file, so no YAML edit and no typo can turn it into a down move.
To move the other way, run ``05_needle_down.py``.

To change how far it moves, edit ``movement.distance`` in
``configs/04_needle_up.yaml``. That single value is the whole knob.

This is a ONE-WAY move: it does not return to the starting position, and
nothing here knows whether a matching down move was ever run. Use
``01_needle_move.py`` when a sequence must provably close at zero.

Ctrl+C during the move sends a software STOP on this process's own serial
connection. That halts the pulse train; it does NOT de-energise the DM542S and
is NOT an emergency stop.

Run:

    python .\\04_needle_up.py --config .\\configs\\04_needle_up.yaml
"""

from __future__ import annotations

from pathlib import Path

from motion_utils import FORWARD
from single_move_utils import run_single_move_cli


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "configs" / "04_needle_up.yaml"


def main() -> None:
    run_single_move_cli(
        direction=FORWARD,
        direction_label="up",
        move_name="needle_up",
        description="Move the needle up once by the distance configured in YAML.",
        default_config=DEFAULT_CONFIG,
        log_prefix="needle_up",
        counterpart_hint=(
            "Run 05_needle_down.py with the SAME distance to command the return."
        ),
    )


if __name__ == "__main__":
    main()
