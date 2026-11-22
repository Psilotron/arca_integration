"""Classify ARCA failures into problems a user can act on.

ARCA answers with fault codes and Spanish messages that mean nothing to most
users ("Computador no autorizado a acceder al servicio"). This module maps
them to a small set of `Problem` values; the Frappe layer turns each one into
a translated explanation and the action that fixes it.
"""

from datetime import datetime
from enum import StrEnum

from .errors import AlreadyAuthenticated, ArcaError, AuthError, ServiceFault, TransportError, TransportTimeout

CERTIFICATE_WARNING_DAYS = 30


class Problem(StrEnum):
	ARCA_UNREACHABLE = "arca_unreachable"
	ARCA_UNAVAILABLE = "arca_unavailable"
	CERTIFICATE_EXPIRED = "certificate_expired"
	CERTIFICATE_NOT_TRUSTED = "certificate_not_trusted"
	SERVICE_NOT_AUTHORIZED = "service_not_authorized"
	CUIT_NOT_RELATED = "cuit_not_related"
	ALREADY_AUTHENTICATED = "already_authenticated"
	CLOCK_SKEW = "clock_skew"
	UNKNOWN = "unknown"


# WSAA fault codes ("ns1:<code>") from the WSAA technical specification.
_WSAA_FAULTS = {
	"cms.cert.expired": Problem.CERTIFICATE_EXPIRED,
	"cms.cert.untrusted": Problem.CERTIFICATE_NOT_TRUSTED,
	"cms.cert.invalid": Problem.CERTIFICATE_NOT_TRUSTED,
	"cms.cert.notFound": Problem.CERTIFICATE_NOT_TRUSTED,
	"coe.notAuthorized": Problem.SERVICE_NOT_AUTHORIZED,
	"coe.alreadyAuthenticated": Problem.ALREADY_AUTHENTICATED,
	"xml.generationTime.invalid": Problem.CLOCK_SKEW,
	"xml.expirationTime.expired": Problem.CLOCK_SKEW,
	"wsn.unavailable": Problem.ARCA_UNAVAILABLE,
	"wsaa.unavailable": Problem.ARCA_UNAVAILABLE,
	"wsaa.internalError": Problem.ARCA_UNAVAILABLE,
}

# Fragments of fault messages returned by business services (MTXCA, padrón).
_SERVICE_MESSAGES = {
	"no apareció cuit en lista de relaciones": Problem.CUIT_NOT_RELATED,
	"no aparecio cuit en lista de relaciones": Problem.CUIT_NOT_RELATED,
	"computador no autorizado": Problem.SERVICE_NOT_AUTHORIZED,
}


def classify(error: Exception) -> Problem:
	if isinstance(error, AlreadyAuthenticated):
		return Problem.ALREADY_AUTHENTICATED
	if isinstance(error, TransportTimeout | TransportError):
		return Problem.ARCA_UNREACHABLE

	if isinstance(error, AuthError | ServiceFault):
		code = (error.detail if isinstance(error, AuthError) else error.code) or ""
		if problem := _WSAA_FAULTS.get(code.rsplit(":", 1)[-1]):
			return problem

	if isinstance(error, ArcaError):
		message = " ".join(error.message.lower().split())
		for fragment, problem in _SERVICE_MESSAGES.items():
			if fragment in message:
				return problem

	return Problem.UNKNOWN


def days_until(expiry: datetime, now: datetime) -> int:
	"""Whole days left before `expiry`; negative once it has passed."""
	return (expiry.date() - now.date()).days
