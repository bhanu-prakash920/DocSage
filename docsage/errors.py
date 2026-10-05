"""Domain errors with messages written for end users."""


class DocSageError(Exception):
    """Base class. ``message`` is safe to show in the UI."""

    status_code = 400

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigurationError(DocSageError):
    status_code = 400


class ProviderError(DocSageError):
    status_code = 502


class BudgetExceeded(DocSageError):
    status_code = 402


class NotFound(DocSageError):
    status_code = 404


class Cancelled(DocSageError):
    status_code = 409


class UnsupportedFile(DocSageError):
    status_code = 415
