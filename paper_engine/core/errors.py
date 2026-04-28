"""Domain exceptions used below the FastAPI route layer."""


class PaperEngineError(Exception):
    """Base exception for expected application errors."""


class ActiveSpaceRequired(PaperEngineError):
    """Raised when an operation needs an active idea space."""


class NotFound(PaperEngineError):
    """Raised when a requested object does not exist."""


class Conflict(PaperEngineError):
    """Raised when a request conflicts with existing local state."""


class ValidationError(PaperEngineError):
    """Raised when a request is structurally invalid for this domain."""


class ParserFailed(PaperEngineError):
    """Raised when PDF parsing cannot produce a usable result."""
