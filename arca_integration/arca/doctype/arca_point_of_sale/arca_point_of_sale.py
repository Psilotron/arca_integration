# Copyright (c) 2026, Psilotron and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ARCAPointofSale(Document):
	def autoname(self):
		self.name = point_of_sale_name(self.company, self.environment, self.number)


def point_of_sale_name(company: str, environment: str, number: int) -> str:
	return f"{company}-{environment}-{int(number):05d}"
