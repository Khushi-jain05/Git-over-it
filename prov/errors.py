from __future__ import annotations

EXIT_CODES = {
    "usage": 2,
    "no_conflict": 3,
    "empty_resolution": 3,
    "unresolved": 4,
    "gates_failed": 5,
    "stale_hunk": 6,
    "git_error": 7,
    "llm_unavailable": 8,
    "llm_rate_limited": 8,
    "not_installed": 9,
}


class ProvError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    @property
    def exit(self) -> int:
        return EXIT_CODES.get(self.code, 1)

    def to_json(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint, "exit": self.exit}
