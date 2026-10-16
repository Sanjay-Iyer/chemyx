> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Step 2 Package Relocation Audit

Date: 2026-07-20
Auditor: read-only second AI agent (`Hubble`)
Final status: PASS

The auditor verified that workflow 01 imports the package workflow directly,
the compatibility bridge remains, moved implementation bodies differ only by
required import paths, command framing/timing/order/response parsing/safety and
DX behavior remain intact, tests target the relocated modules, and no stale old
package imports or circular import chain remains.

The first pass found `real_framework.py` importing through the compatibility
bridge. It was updated to import the package workflow directly and passed the
recheck.
