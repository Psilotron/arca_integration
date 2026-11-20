"""What ARCA's WSMTXCA error and observation codes mean for the user, and how to fix them.

Codes come from the manual (v0.25.10). Only codes that the builder cannot
prevent, or that it prevents but other systems may still trigger, are listed:
for any other code the caller shows ARCA's own description. Texts are English
and marked with `N_` so the Frappe adapter can translate them.
"""

from dataclasses import dataclass


def N_(message: str) -> str:
	"""Mark a string for extraction into main.pot without translating it here."""
	return message


@dataclass(frozen=True)
class Explanation:
	message: str
	action: str = ""


EXPLANATIONS: dict[str, Explanation] = {
	"101": Explanation(
		N_("The point of sale is not enabled for invoices with item detail, or it is blocked."),
		N_(
			"On the ARCA website, open 'Administración de puntos de venta y domicilios' and check that it is of type 'Factura electrónica - Codificación de productos - Web services'."
		),
	),
	"102": Explanation(
		N_("The voucher number is not the next one ARCA expects for this point of sale."),
		N_(
			"Another system may be issuing vouchers with this point of sale. Use a point of sale only for ERPNext and try again."
		),
	),
	"104": Explanation(
		N_(
			"The invoice date is earlier than the date of the last voucher authorized for this point of sale."
		),
		N_("Change the invoice date to that date or later."),
	),
	"119": Explanation(
		N_("The exchange rate is too far from ARCA's official rate."),
		N_("Check the exchange rate of the invoice."),
	),
	"135": Explanation(
		N_("ARCA was still processing another voucher of the same point of sale."),
		N_("Try again in a few seconds."),
	),
	"190": Explanation(
		N_("ARCA requires the customer's VAT condition."),
		N_("Complete it in the customer with 'Complete from ARCA'."),
	),
	"198": Explanation(
		N_("ARCA does not accept the customer's VAT condition for this voucher type."),
		N_("Check the customer's VAT condition with 'Complete from ARCA'."),
	),
	"253": Explanation(
		N_("ARCA reports the customer's CUIT as inactive or invalidated."),
		N_("Check the CUIT with the customer."),
	),
	"265": Explanation(
		N_("ARCA limits the customer's CUIT: it was characterized as unreliable for Social Security."),
		N_("Check the situation with the customer."),
	),
	"303": Explanation(
		N_("ARCA limits the customer's CUIT: it was marked as apocryphal."),
		N_("Check the situation with the customer."),
	),
	"349": Explanation(
		N_("For invoices from $10,000,000, the customer's CUIT must be registered and active in ARCA."),
		N_("Check the CUIT with 'Complete from ARCA'."),
	),
	"504": Explanation(
		N_(
			"A product code (GTIN) is not registered in ARCA. The invoice was authorized with an observation."
		),
		N_("Check the items' barcodes or the company's default GTIN."),
	),
	"10003": Explanation(
		N_("The company's CUIT is not registered for VAT in ARCA."),
		N_("Invoices with item detail (A and B) require being VAT registered (Responsable Inscripto)."),
	),
	"10010": Explanation(
		N_(
			"The company's CUIT is not registered for 'Codificación de Productos - opción Factura con Detalle'."
		),
		N_("Register it on the ARCA website before invoicing with this service."),
	),
}


def explain(code: str) -> Explanation | None:
	return EXPLANATIONS.get(str(code))
