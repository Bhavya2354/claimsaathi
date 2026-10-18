"""Exception hierarchy shared across the package."""


class AppError(Exception):
    """Base class for every error raised by this package."""


class ConfigurationError(AppError):
    """Required settings are missing or invalid."""


class DocumentError(AppError):
    """An uploaded document could not be accepted or read."""


class ModelUnavailableError(AppError):
    """Every model on a route failed or is cooling down."""


class StructuredOutputError(AppError):
    """A model answer did not match the requested schema after repair."""


class NotFoundError(AppError):
    """A requested record does not exist or has expired."""


class AccessDeniedError(AppError):
    """The caller's case token does not match."""


class VerificationError(AppError):
    """An agent's output failed the checker after all retries."""
