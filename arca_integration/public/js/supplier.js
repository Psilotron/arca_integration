// Copyright (c) 2026, Psilotron and contributors
// For license information, please see license.txt

// Only an Accounts Manager can exempt a supplier from verifying its vouchers with ARCA.
frappe.ui.form.on("Supplier", {
	refresh(frm) {
		frm.set_df_property(
			"arca_no_vouchers",
			"read_only",
			frappe.user.has_role("Accounts Manager") ? 0 : 1
		);
	},
});
