"""Error taxonomy for ARCA calls.

Three failure classes are kept apart on purpose: authentication (credential or
ticket problems), transport (network or HTTP), and ARCA business answers
(unknown taxpayer, blocked constancia).
"""


class ArcaError(Exception):
    """Base error carrying a user-facing message and optional technical detail."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class AuthError(ArcaError):
    """Certificate, private key, or WSAA ticket problem."""


class TransportError(ArcaError):
    """Network failure, timeout, or non-200 HTTP answer."""


class PersonaNotFound(ArcaError):
    """ARCA answered with a SOAP fault for an unknown taxpayer."""


class ConstanciaError(ArcaError):
    """ARCA answered 200 with an errorConstancia body."""


class WsfeError(ArcaError):
    """Base error for the WSFE fiscal engine.

    ``code`` is ARCA's own error code when the answer carried one, so callers can
    branch on it without parsing the message.
    """

    def __init__(
        self, message: str, code: int | str | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message, detail)
        self.code = code


class WsfeRejected(WsfeError):
    """ARCA answered ``Resultado: R`` or reported an ``Errors`` list.

    A rejection is a definite answer: the comprobante was not authorized.
    """


class WsfeUncertain(WsfeError):
    """ARCA's answer does not settle whether the comprobante exists.

    Guessing which half happened is never allowed: reconcile with the comprobante
    query before issuing again.
    """
