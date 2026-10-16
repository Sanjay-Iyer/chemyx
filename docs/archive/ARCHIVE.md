> **Historical document — archived 2026-10-16.** Preserved for provenance; its commands/settings may be superseded. Use [the current simple operating guide](../SI6_SIMPLE_RUN_GUIDE.md) and [errors/fixes](../SI6_ERRORS_AND_FIXES.md) at the instruments.

# Local Archive

The cleanup archive is stored at:

```text
_archive/2026-07-20_repository_cleanup/
```

Every archived file is recorded in `docs/archive/ARCHIVE_MANIFEST.csv` with its
original path, archive path, classification, reason, replacement, Git tracking
state, size, SHA-256 checksum, and date. All 97 post-move checksums were
verified.

`_archive/` is intentionally ignored by Git. Of the 97 archived files, 64 were
already untracked. They will remain only on this home laptop unless the archive
directory is copied separately. This includes ten human-authored migration
reports and nine generated result/smoke artifacts. The successful raw DX files
and timestamped processed analyses remain under active `results/` and are not
dependent on the local archive.
