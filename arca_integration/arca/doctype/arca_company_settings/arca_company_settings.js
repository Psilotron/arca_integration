// Copyright (c) 2026, Psilotron and contributors
// For license information, please see license.txt

frappe.ui.form.on("ARCA Company Settings", {
	setup(frm) {
		frm.set_query("commercial_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Company", link_name: frm.doc.company },
		}));
	},

	refresh(frm) {
		if (frm.is_new()) return;

		for (const environment of ["Homologation", "Production"]) {
			frm.add_custom_button(
				__(environment),
				() => generate_csr(frm, environment),
				__("Generate Key and CSR")
			);
		}
	},
});

function generate_csr(frm, environment) {
	const has_key = frm.doc[`${environment.toLowerCase()}_private_key`];

	const ask = () =>
		frappe.prompt(
			{
				fieldname: "common_name",
				fieldtype: "Data",
				label: __("Certificate Name"),
				description: __(
					"Alias registered in ARCA for this certificate. Letters, numbers, hyphens and underscores only."
				),
				default: `erpnext-${environment.toLowerCase()}`,
				reqd: 1,
			},
			({ common_name }) =>
				frappe
					.call({
						method: "arca_integration.arca.doctype.arca_company_settings.arca_company_settings.generate_csr",
						args: { name: frm.doc.name, environment, common_name },
						freeze: true,
					})
					.then(({ message }) => {
						download(message.filename, message.csr);
						frm.reload_doc();
						frappe.msgprint(
							__(
								"Upload the downloaded CSR to ARCA, then paste the certificate it returns in {0}.",
								[__("{0} Credentials", [__(environment)])]
							)
						);
					}),
			__("Generate Key and CSR for {0}", [__(environment)]),
			__("Generate")
		);

	if (has_key) {
		frappe.confirm(
			__("This replaces the current {0} private key and clears its certificate. Continue?", [
				__(environment),
			]),
			ask
		);
	} else {
		ask();
	}
}

function download(filename, content) {
	const url = URL.createObjectURL(new Blob([content], { type: "application/pkcs10" }));
	const link = document.createElement("a");
	link.href = url;
	link.download = filename;
	link.click();
	URL.revokeObjectURL(url);
}
