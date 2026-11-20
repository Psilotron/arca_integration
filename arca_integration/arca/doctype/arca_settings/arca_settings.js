// Copyright (c) 2026, Psilotron and contributors
// For license information, please see license.txt

frappe.ui.form.on("ARCA Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Sync Catalogs"), () => {
			frappe.prompt(
				{
					fieldname: "company",
					fieldtype: "Link",
					label: __("Company"),
					options: "ARCA Company Settings",
					reqd: 1,
					description: __(
						"Its credentials are used to query ARCA. Catalogs are shared by all companies; points of sale are synced for this company only."
					),
				},
				({ company }) =>
					frappe
						.call({
							method: "arca_integration.arca.catalogs.sync_now",
							args: { company },
							freeze: true,
							freeze_message: __("Querying ARCA..."),
						})
						.then(({ message }) => show_results(message)),
				__("Sync Catalogs"),
				__("Sync Now")
			);
		});
	},
});

function show_results(results) {
	const rows = Object.entries(results)
		.map(
			([doctype, r]) =>
				`<tr><td>${__(doctype)}</td><td>${r.added}</td><td>${r.updated}</td><td>${
					r.disabled
				}</td></tr>`
		)
		.join("");

	frappe.msgprint({
		title: __("Catalogs Synced"),
		indicator: "green",
		message: `<table class="table table-bordered">
			<thead><tr><th>${__("Catalog")}</th><th>${__("Added")}</th><th>${__("Updated")}</th><th>${__(
			"Disabled"
		)}</th></tr></thead>
			<tbody>${rows}</tbody>
		</table>`,
	});
}
