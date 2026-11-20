// Copyright (c) 2026, Psilotron and contributors
// For license information, please see license.txt

frappe.ui.form.on("ARCA Company Settings", {
	setup(frm) {
		frm.set_query("commercial_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Company", link_name: frm.doc.company },
		}));
		frm.set_query("default_point_of_sale", () => ({
			filters: {
				company: frm.doc.company,
				environment: frm.doc.environment,
				disabled: 0,
				blocked: 0,
			},
		}));
	},

	refresh(frm) {
		render_checklist(frm);
		show_environment_banner(frm);
		if (frm.is_new()) return;

		frm.add_custom_button(__("Verify Connection"), () => verify_connection(frm)).addClass(
			"btn-primary"
		);
		for (const environment of ["Homologation", "Production"]) {
			frm.add_custom_button(
				__(environment),
				() => generate_csr(frm, environment),
				__("Generate Key and CSR")
			);
		}
	},

	environment(frm) {
		show_environment_banner(frm);
	},
});

const STEP_STYLE = {
	ok: { symbol: "✓", color: "var(--green-600)" },
	warning: { symbol: "!", color: "var(--orange-500)" },
	error: { symbol: "✗", color: "var(--red-600)" },
	skipped: { symbol: "–", color: "var(--gray-500)" },
};

function verify_connection(frm) {
	frappe
		.call({
			method: "arca_integration.arca.connection.verify_connection",
			args: { company: frm.doc.name },
			freeze: true,
			freeze_message: __("Checking the connection with ARCA..."),
		})
		.then(({ message }) => {
			frm.reload_doc();
			const ready = message.status === "Ready";
			frappe.show_alert({
				message: ready ? __("Ready to invoice") : __("Some steps need attention"),
				indicator: ready ? "green" : "orange",
			});
		});
}

function render_checklist(frm) {
	const wrapper = frm.get_field("connection_html").$wrapper;
	let steps = [];
	try {
		steps = JSON.parse(frm.doc.connection_report || "[]");
	} catch (e) {
		steps = [];
	}

	if (!steps.length) {
		wrapper.html(
			`<p class="text-muted">${__(
				"Complete the certificate below and press 'Verify Connection'. Each step is checked against ARCA."
			)}</p>`
		);
		return;
	}

	const rows = steps
		.map((step) => {
			const style = STEP_STYLE[step.status] || STEP_STYLE.skipped;
			const detail = step.detail
				? `<details class="text-muted small"><summary>${__(
						"Technical detail"
				  )}</summary>${frappe.utils.escape_html(step.detail)}</details>`
				: "";
			return `<div class="d-flex mb-3" style="gap: var(--padding-sm)">
				<span class="bold" style="color: ${style.color}; min-width: 1em">${style.symbol}</span>
				<div>
					<div class="bold">${frappe.utils.escape_html(step.title)}</div>
					<div>${frappe.utils.escape_html(step.message || "")}</div>
					${
						step.action
							? `<div style="color: ${style.color}">${frappe.utils.escape_html(
									step.action
							  )}</div>`
							: ""
					}
					${detail}
				</div>
			</div>`;
		})
		.join("");
	wrapper.html(rows);
}

function show_environment_banner(frm) {
	if (frm.doc.environment === "Homologation") {
		frm.set_intro(
			__("Homologation: vouchers authorized in this environment have no fiscal validity."),
			"yellow"
		);
	} else {
		frm.set_intro("");
	}
}

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
