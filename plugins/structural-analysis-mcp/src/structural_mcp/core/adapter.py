"""Contract every program adapter implements. The MCP layer in server.py is shared."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors
from .errors import AdapterError


@dataclass
class ResultSet:
    """Typed result rows plus what is needed to read them correctly."""

    rows: list[dict[str, Any]]
    units: dict[str, str]
    coordinate_system: str
    diagnostics: list[str] = field(default_factory=list)


class Adapter(ABC):
    #: short machine name, also the tool prefix: "sap2000", "staad", "lira", "sapfir"
    program: str = ""
    #: human name for descriptions
    title: str = ""
    #: model file extensions this program opens, lower-case with dot
    model_extensions: tuple[str, ...] = ()
    #: extra files copied next to a working copy (e.g. results)
    companion_extensions: tuple[str, ...] = ()
    #: element_type -> description
    element_types: dict[str, str] = {}
    #: result_type -> description
    result_types: dict[str, str] = {}
    #: load case kinds accepted by list_load_cases
    load_case_kinds: tuple[str, ...] = ("cases", "combinations", "all")
    supports_analysis: bool = False
    supports_open: bool = True

    # ---- connection -----------------------------------------------------------------
    @abstractmethod
    def probe(self) -> dict[str, Any]:
        """Inspect the workstation without touching a model: registered COM classes,
        installed packages, bitness. Must not raise for a missing program."""

    @abstractmethod
    def connect(self, model_path: str | None = None, start_if_needed: bool = False) -> dict[str, Any]:
        """Attach to a running instance (optionally the one holding `model_path`)."""

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def program_info(self) -> dict[str, Any]:
        """Program name, version/build as reported by the API."""

    def open_model(self, path: Path) -> None:
        raise AdapterError(errors.UNSUPPORTED, f"{self.title}: opening a file through the API is not available.")

    # ---- model ----------------------------------------------------------------------
    @abstractmethod
    def model_path(self) -> str | None: ...

    @abstractmethod
    def model_info(self) -> dict[str, Any]:
        """Units, object counts, results state ({'available': bool|None, 'detail': ...})."""

    @abstractmethod
    def element_ids(self, element_type: str) -> list[str]: ...

    @abstractmethod
    def describe_elements(self, element_type: str, ids: list[str]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_load_cases(self, kind: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_results(
        self,
        result_type: str,
        load_cases: list[str],
        element_ids: list[str] | None,
        options: dict[str, Any],
    ) -> ResultSet: ...

    # ---- analysis -------------------------------------------------------------------
    def run_analysis(self, load_cases: list[str] | None) -> dict[str, Any]:
        raise AdapterError(errors.UNSUPPORTED, f"{self.title}: running analysis is not implemented in this server.")

    # ---- extras ---------------------------------------------------------------------
    def extra_tools(self) -> dict[str, Any]:
        """Program-specific read-only tools: name -> (callable, description)."""
        return {}

    # ---- helpers --------------------------------------------------------------------
    def check_choice(self, value: str, allowed: dict[str, str] | tuple[str, ...], what: str) -> None:
        if value not in allowed:
            raise AdapterError(
                errors.INVALID_INPUT,
                f"Unknown {what} '{value}' for {self.title}.",
                f"Use one of: {', '.join(allowed)}.",
            )


def row(status: str = "ok", **fields: Any) -> dict[str, Any]:
    """A result row. status: 'ok' (values are computed, zeros are real zeros),
    'no_result' (the program has nothing for this item) or 'error' (API failure,
    see 'message')."""
    return {"status": status, **fields}
