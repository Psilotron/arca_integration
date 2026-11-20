from datetime import datetime

from frappe.tests import UnitTestCase

from arca_integration.services.diagnosis import Problem, classify, days_until
from arca_integration.services.errors import (
	AlreadyAuthenticated,
	ArcaError,
	AuthError,
	ServiceFault,
	TransportError,
	TransportTimeout,
)


class TestDiagnosis(UnitTestCase):
	def test_wsaa_fault_codes(self):
		cases = {
			"ns1:coe.notAuthorized": Problem.SERVICE_NOT_AUTHORIZED,
			"ns1:cms.cert.expired": Problem.CERTIFICATE_EXPIRED,
			"ns1:cms.cert.untrusted": Problem.CERTIFICATE_NOT_TRUSTED,
			"ns1:xml.generationTime.invalid": Problem.CLOCK_SKEW,
			"ns1:wsaa.unavailable": Problem.ARCA_UNAVAILABLE,
		}
		for code, problem in cases.items():
			with self.subTest(code=code):
				self.assertEqual(classify(AuthError("Computador no autorizado", detail=code)), problem)

	def test_already_authenticated(self):
		self.assertEqual(classify(AlreadyAuthenticated("x")), Problem.ALREADY_AUTHENTICATED)

	def test_cuit_not_related_from_service_fault(self):
		fault = ServiceFault(
			"soap:Server", "ValidacionDeToken: No apareció CUIT en lista de relaciones: 30000000007"
		)
		self.assertEqual(classify(fault), Problem.CUIT_NOT_RELATED)

	def test_network(self):
		self.assertEqual(classify(TransportTimeout("t")), Problem.ARCA_UNREACHABLE)
		self.assertEqual(classify(TransportError("e")), Problem.ARCA_UNREACHABLE)

	def test_unknown(self):
		self.assertEqual(classify(ArcaError("algo raro")), Problem.UNKNOWN)
		self.assertEqual(classify(ValueError("x")), Problem.UNKNOWN)

	def test_days_until(self):
		now = datetime(2026, 11, 20, 23, 0)
		self.assertEqual(days_until(datetime(2026, 11, 27, 1, 0), now), 7)
		self.assertEqual(days_until(datetime(2026, 11, 19, 12, 0), now), -1)
