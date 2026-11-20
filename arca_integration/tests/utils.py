import frappe

TEST_COMPANY = "_Test ARCA Company"


def get_test_company() -> str:
	"""Minimal Argentine company for integration tests, created once per site."""
	if not frappe.db.exists("Company", TEST_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": TEST_COMPANY,
				"abbr": "_TARCA",
				"default_currency": "ARS",
				"country": "Argentina",
				"tax_id": "30-00000000-7",
			}
		).insert(ignore_permissions=True)
	return TEST_COMPANY
