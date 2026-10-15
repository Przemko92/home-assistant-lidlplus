"""Errors raised by the Lidl Plus client."""


class LidlPlusError(Exception):
    """Base error."""

    def __init__(
        self, message: str = "", *, status: int | None = None, body: str = ""
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class LidlPlusAuthError(LidlPlusError):
    """Authentication failed."""


class LidlPlusCannotConnect(LidlPlusError):
    """Network or HTTP failure."""
