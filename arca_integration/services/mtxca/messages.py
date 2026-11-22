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
	"117": Explanation(
		N_("ARCA does not know the currency of the invoice."),
		N_("Sync the ARCA catalogs and check the ARCA Currency of the invoice's currency."),
	),
	"118": Explanation(
		N_("Only invoices can be marked as paid in the same foreign currency, not credit or debit notes."),
		N_("Uncheck 'Paid in the Same Foreign Currency'."),
	),
	"119": Explanation(
		N_("The exchange rate is too far from ARCA's official rate."),
		N_("Use ARCA's rate with 'Use ARCA Rate', or check the exchange rate of the invoice."),
	),
	"164": Explanation(
		N_("ARCA did not accept the 'paid in the same foreign currency' mark."),
		N_("Check 'Paid in the Same Foreign Currency' again and save the invoice."),
	),
	"169": Explanation(
		N_("An invoice in pesos cannot be marked as paid in the same foreign currency."),
		N_("Uncheck 'Paid in the Same Foreign Currency'."),
	),
	"192": Explanation(
		N_("The invoice is paid in a Banco Nación currency, so its exchange rate must be exactly ARCA's."),
		N_("Use ARCA's rate with 'Use ARCA Rate' and try again."),
	),
	"195": Explanation(
		N_("The exchange rate cannot be negative."),
		N_("Check the exchange rate of the invoice."),
	),
	"130": Explanation(  # an observation: the CAE is granted anyway
		N_(
			"ARCA's records do not show the customer as VAT registered or Monotributo, which an A invoice requires."
		),
		N_(
			"Check the customer's VAT condition with 'Complete from ARCA'. If it is another condition, issue B invoices to this customer."
		),
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
