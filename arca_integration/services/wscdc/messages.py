"""What WSCDC's codes mean for the user (manual of 2019-12-01, p. 11 to 14).

Texts are English and marked with `N_` so the Frappe adapter translates them.
For codes not listed here the caller shows ARCA's own text.
"""


def N_(message: str) -> str:
	"""Mark a string for extraction into main.pot without translating it here."""
	return message


EXPLANATIONS: dict[str, str] = {
	# Format of the data
	"1": N_("The authorization type must be CAE, CAEA or CAI."),
	"2": N_("The supplier's CUIT is not valid."),
	"3": N_("The point of sale must be a number between 1 and 99998."),
	"4": N_("ARCA does not know this voucher type."),
	"5": N_("The voucher number must be a number between 1 and 99999999."),
	"7": N_("The total of the invoice is not valid."),
	"10": N_("The authorization code must have 14 digits."),
	# The voucher, against what ARCA keeps
	"100": N_("ARCA has no voucher with this authorization code. Do not accept it."),
	"101": N_("ARCA only verifies vouchers dated from 2013-01-01."),
	"102": N_("The authorization code belongs to another CUIT, not to this supplier."),
	"103": N_("The voucher type is not the one ARCA authorized with this code."),
	"104": N_("The point of sale is not the one ARCA authorized with this code."),
	"105": N_("The voucher number is not the one ARCA authorized with this code."),
	"106": N_(
		"The supplier has not yet reported this CAEA voucher to ARCA, and the time to do so has passed."
	),
	"107": N_("The supplier invoice date is not the one ARCA authorized with this code."),
	"108": N_("The supplier invoice date is outside the dates allowed for this CAI."),
	"109": N_("The point of sale is not enabled for CAEA vouchers."),
	"110": N_("The total is not the one ARCA authorized. Check the invoice's grand total."),
	"111": N_("The voucher was not issued to your company: the receiver's document type differs."),
	"112": N_("The voucher was not issued to your company: the receiver's CUIT differs."),
	"200": N_("The supplier has not yet reported this CAEA voucher to ARCA, but is still in time."),
}
