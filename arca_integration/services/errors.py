"""Errors raised by the ARCA service layer.

Messages are English and technical. Frappe adapters catch these classes and
show translated messages, so nothing here depends on the user's language.
"""


class ArcaError(Exception):
	"""Base class. `detail` keeps technical context for logs."""

	def __init__(self, message: str, detail: str | None = None) -> None:
		super().__init__(message)
		self.message = message
		self.detail = detail


class TransportError(ArcaError):
	"""The request did not get a usable answer: network error, HTTP error or unreadable XML."""


class TransportTimeout(TransportError):
	"""No answer in time. ARCA may still have processed the request."""


class ServiceFault(ArcaError):
	"""ARCA answered with a SOAP fault."""

	def __init__(self, code: str, message: str, detail: str | None = None) -> None:
		super().__init__(message, detail)
		self.code = code


class AuthError(ArcaError):
	"""WSAA did not issue a ticket."""


class AlreadyAuthenticated(AuthError):
	"""WSAA refused a new login because a ticket for this certificate and service is still valid."""
