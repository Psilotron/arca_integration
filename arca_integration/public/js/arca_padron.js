// "Complete from ARCA": look up a CUIT in ARCA's registry and let the user pick
// which values to apply. Shared by the Customer and ARCA Company Settings forms.

frappe.provide("arca");

arca.lookup_cuit = function ({ cuit, company }) {
	return frappe
		.call({
			method: "arca_integration.arca.padron.lookup",
			args: { cuit, company },
			freeze: true,
			freeze_message: __("Looking up the CUIT in ARCA..."),
		})
		.then(({ message }) => message);
};

// rows: [{ key, label, current, arca, same? }]: `current` and `arca` are what the user reads;
// `same`, when given, overrides comparing them (e.g. codes shown with their description).
// on_apply receives the keys the user kept checked.
arca.show_comparison = function ({ taxpayer, rows, on_apply }) {
	const esc = frappe.utils.escape_html;
	const warnings = (taxpayer.warnings || [])
		.map((w) => `<div class="alert alert-warning">${esc(w)}</div>`)
		.join("");

	const body = rows
		.map((row, i) => {
			const same = row.same ?? (row.current || "") === (row.arca || "");
			const empty = !row.arca;
			const checked = !same && !empty ? "checked" : "";
			const disabled = same || empty ? "disabled" : "";
			return `<tr>
				<td><input type="checkbox" data-row="${i}" ${checked} ${disabled}></td>
				<td>${esc(row.label)}</td>
				<td class="text-muted">${esc(row.current || "—")}</td>
				<td>${esc(row.arca || "—")}${same ? ` <span class="text-muted">(${__("same")})</span>` : ""}</td>
			</tr>`;
		})
		.join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Data from ARCA"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "table" }],
		primary_action_label: __("Apply Selected"),
		primary_action() {
			const keys = [];
			dialog.$wrapper.find("input[data-row]:checked").each(function () {
				keys.push(rows[$(this).data("row")].key);
			});
			dialog.hide();
			on_apply(keys);
		},
	});

	dialog.fields_dict.table.$wrapper.html(`
		<p><b>${esc(taxpayer.name || "")}</b> · CUIT ${esc(taxpayer.cuit)}</p>
		${warnings}
		<table class="table table-bordered">
			<thead><tr><th></th><th>${__("Field")}</th><th>${__("Current Value")}</th><th>${__(
		"ARCA"
	)}</th></tr></thead>
			<tbody>${body}</tbody>
		</table>
		<p class="text-muted small">${__(
			"The VAT condition is deduced from the taxes registered in ARCA. Check it before applying."
		)}</p>
	`);
	dialog.show();
};

arca.apply_address = function (link_doctype, link_name, address) {
	return frappe
		.call({
			method: "arca_integration.arca.padron.apply_address",
			args: { link_doctype, link_name, address },
		})
		.then(({ message }) => message);
};
