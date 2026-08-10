"""Script 05: move the needle DOWN once, by the distance in its YAML file.

DOWN is this rig's name for the motion the shared motion core calls "backward",
which is a NEGATIVE step count on the wire (``MOVE -200``). The mapping is
stated in every preflight so the operator label and the firmware sign can never
drift apart.

Ordinary needle-motion scripts use low sequential numbers (01, 02, 03, ...).
Calibration, maintenance, and diagnostic scripts use high numbers (99, 98, ...).
See README.md and OPERATOR_GUIDE.md.

This script only ever moves down. Direction is fixed here in code, not in the
configuration file, so no YAML edit and no typo can turn it into an up move.
To move the other way, run ``04_needle_up.py``.

To change how far it moves, edit ``movement.distance`` in
``configs/05_needle_down.yaml``. That single value is the whole knob, and it is
a POSITIVE magnitude -- this script supplies the downward sign.

This is a ONE-WAY move: it does not return to the starting position, and
nothing here knows whether a matching up move was ever run. Use
``01_needle_move.py`` when a sequence must provably close at zero.

Ctrl+C during the move sends a software STOP on this process's own serial
connection. That halts the pulse train; it does NOT de-energise the DM542S and
is NOT an emergency stop.

Run:

    python .\\05_needle_down.py --config .\\configs\\05_needle_down.yaml
"""

from __future__ import annotations

from pathlib import Path

from motion_utils import BACKWARD
from single_move_utils import run_single_move_cli


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "configs" / "05_needle_down.yaml"


def main() -> None:
    run_single_move_cli(
        direction=BACKWARD,
        direction_label="down",
        move_name="needle_down",
        description="Move the needle down once by the distance configured in YAML.",
        default_config=DEFAULT_CONFIG,
        log_prefix="needle_down",
        counterpart_hint=(
            "Run 04_needle_up.py with the SAME distance to command the return."
        ),
    )


if __name__ == "__main__":
    main()
