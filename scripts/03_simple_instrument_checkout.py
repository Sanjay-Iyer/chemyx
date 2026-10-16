"""One standalone Arduino / Chemyx 1 / Chemyx 2 / two-scan NMR bench check."""
import _bootstrap  # noqa: F401
from chemyx_lab.workflows.simple_instrument_checkout import main

if __name__ == "__main__":
    raise SystemExit(main())
