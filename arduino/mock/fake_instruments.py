"""Minimal mocks where the repository has no existing NMR RPC fake."""

from __future__ import annotations

from pathlib import Path


class FakeNmrClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.acquiring = False

    def ping(self):
        self.calls.append("ping")
        return {"ready": True, "mock": True}

    def acquire_diagnostic(self, output_path: Path) -> Path:
        self.calls.append("acquire_diagnostic")
        self.acquiring = True
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("##TITLE=MOCK NMR DIAGNOSTIC\n##END=\n", encoding="utf-8")
        self.acquiring = False
        return output_path

