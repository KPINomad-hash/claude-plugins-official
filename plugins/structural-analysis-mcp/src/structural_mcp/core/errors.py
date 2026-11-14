"""Error type shared by all adapters.

Three situations must stay distinguishable for the engineer reading the output
(dossier, section 6): the program has no result, the API call failed, and the
computed value is zero. The first two are raised/returned with a code; a zero
is just a number in a row with status "ok".
"""

from __future__ import annotations

# Codes used across adapters.
NOT_CONNECTED = "not_connected"      # program is not running / not attached
NO_MODEL = "no_model"                # program is attached but no model is open
NO_RESULTS = "no_results"            # model has no (current) analysis results
API_ERROR = "api_error"              # the program's API returned an error
UNSUPPORTED = "unsupported"          # operation is not available in this program/version
NOT_ALLOWED = "not_allowed"          # blocked by access mode or folder restriction
INVALID_INPUT = "invalid_input"
BUSY = "busy"                        # a previous operation is still running
TIMEOUT = "timeout"                  # we stopped waiting; the program may still be working
MISSING_DEPENDENCY = "missing_dependency"


class AdapterError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        return f"{text} Hint: {self.hint}" if self.hint else text
