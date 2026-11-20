frappe.ui.form.on("Customer", {
	refresh(frm) {
		frm.add_custom_button(__("Complete from ARCA"), () => complete_from_arca(frm), __("ARCA"));
	},

	after_save(frm) {
		const address = frm.__arca_pending_address;
		if (!address) return;
		frm.__arca_pending_address = null;
		arca.apply_address("Customer", frm.doc.name, address).then(() => frm.reload_doc());
	},
});

async function complete_from_arca(frm) {
	if (!frm.doc.tax_id) {
		frappe.msgprint(__("Enter the CUIT in Tax ID first."));
		frm.scroll_to_field("tax_id");
		return;
	}

	const taxpayer = await arca.lookup_cuit({ cuit: frm.doc.tax_id });
	const current_address = frm.doc.__onload?.addr_list?.[0];
	const address = taxpayer.address;
	const current_condition = frm.doc.arca_receiver_vat_condition
		? (
				await frappe.db.get_value(
					"ARCA Receiver VAT Condition",
					frm.doc.arca_receiver_vat_condition,
					"description"
				)
		  ).message?.description
		: "";
	const join = (a) => (a ? [a.address_line1, a.city].filter(Boolean).join(", ") : "");

	arca.show_comparison({
		taxpayer,
		rows: [
			{
				key: "customer_name",
				label: __("Customer Name"),
				current: frm.doc.customer_name,
				arca: taxpayer.name,
			},
			{
				key: "customer_type",
				label: __("Customer Type"),
				current: __(frm.doc.customer_type || ""),
				arca: __(taxpayer.customer_type),
				same: frm.doc.customer_type === taxpayer.customer_type,
			},
			{ key: "tax_id", label: __("Tax ID"), current: frm.doc.tax_id, arca: taxpayer.cuit },
			{
				key: "arca_document_type",
				label: __("ARCA Document Type"),
				current: frm.doc.arca_document_type,
				arca: taxpayer.document_type,
			},
			{
				key: "arca_receiver_vat_condition",
				label: __("ARCA VAT Condition"),
				current: current_condition,
				arca: taxpayer.vat_condition_label,
				same: frm.doc.arca_receiver_vat_condition === taxpayer.vat_condition,
			},
			{
				key: "address",
				label: __("Fiscal Address"),
				current: join(current_address),
				arca: join(address),
			},
		],
		on_apply(keys) {
			const values = {
				customer_name: taxpayer.name,
				customer_type: taxpayer.customer_type,
				tax_id: taxpayer.cuit,
				arca_document_type: taxpayer.document_type,
				arca_receiver_vat_condition: taxpayer.vat_condition,
			};
			for (const key of keys) {
				if (key in values) frm.set_value(key, values[key]);
			}
			if (keys.includes("address") && address) {
				if (frm.is_new()) {
					frm.__arca_pending_address = address;
					frappe.show_alert(__("The address will be created when you save."));
				} else {
					arca.apply_address("Customer", frm.doc.name, address).then(() => {
						frappe.show_alert({ message: __("Address updated"), indicator: "green" });
						frm.reload_doc();
					});
				}
			}
		},
	});
}
