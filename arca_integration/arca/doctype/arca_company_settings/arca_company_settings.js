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
		for (const environment of ENVIRONMENTS) render_credentials(frm, environment);
		show_environment_banner(frm);
		if (frm.is_new()) return;

		frm.add_custom_button(__("Verify Connection"), () => verify_connection(frm)).addClass(
			"btn-primary"
		);
		frm.add_custom_button(__("Complete from ARCA"), () => complete_company_from_arca(frm));
	},

	environment(frm) {
		show_environment_banner(frm);
	},
});

const ENVIRONMENTS = ["Homologation", "Production"];

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
								"Upload the downloaded CSR to ARCA. When ARCA returns the certificate (.crt), load it with 'Load Files' in {0}.",
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

async function complete_company_from_arca(frm) {
	const taxpayer = await arca.lookup_cuit({ cuit: frm.doc.cuit, company: frm.doc.company });
	if (taxpayer.vat_condition && taxpayer.vat_condition !== "1") {
		taxpayer.warnings.push(
			__(
				"ARCA does not register this CUIT as VAT registered (Responsable Inscripto). WSMTXCA only authorizes A and B invoices, which require it."
			)
		);
	}
	const address = taxpayer.address;
	const current = frm.doc.commercial_address
		? (
				await frappe.db.get_value("Address", frm.doc.commercial_address, [
					"address_line1",
					"city",
				])
		  ).message
		: null;
	const join = (a) => (a ? [a.address_line1, a.city].filter(Boolean).join(", ") : "");

	arca.show_comparison({
		taxpayer,
		rows: [
			{
				key: "address",
				label: __("Commercial Address"),
				current: join(current),
				arca: join(address),
			},
		],
		on_apply(keys) {
			if (!keys.includes("address") || !address) return;
			arca.apply_address("Company", frm.doc.company, address).then((name) => {
				frm.set_value("commercial_address", name);
				frm.save();
			});
		},
	});
}

// ---- Credentials: status panel and file loading ----

function render_credentials(frm, environment) {
	const prefix = environment.toLowerCase();
	const field = frm.get_field(`${prefix}_credentials_html`);
	if (!field) return;
	const esc = frappe.utils.escape_html;

	if (frm.is_new()) {
		field.$wrapper.html(
			`<p class="text-muted">${__("Save the company first to load its credentials.")}</p>`
		);
		return;
	}

	const has_certificate = !!frm.doc[`${prefix}_certificate`];
	const has_key = !!frm.doc[`${prefix}_private_key`];
	const expiry = frm.doc[`${prefix}_certificate_expiry`];
	const days_left = expiry
		? frappe.datetime.get_day_diff(expiry, frappe.datetime.now_date())
		: null;

	const line = (ok, text, color) =>
		`<div class="mb-1"><span class="bold" style="color: ${
			color || (ok ? "var(--green-600)" : "var(--red-600)")
		}">${ok ? "✓" : "✗"}</span> ${text}</div>`;

	let certificate_line = line(false, __("No certificate loaded."));
	if (has_certificate) {
		const expired = days_left !== null && days_left < 0;
		const soon = days_left !== null && days_left <= 30;
		certificate_line = line(
			!expired,
			`${__("Certificate valid until {0}", [
				frappe.datetime.str_to_user(expiry).split(" ")[0],
			])}${soon && !expired ? ` · <b>${__("expires in {0} days", [days_left])}</b>` : ""}${
				expired ? ` · <b>${__("expired")}</b>` : ""
			}
			<div class="text-muted small">${esc(frm.doc[`${prefix}_certificate_subject`] || "")}</div>`,
			expired ? "var(--red-600)" : soon ? "var(--orange-500)" : null
		);
	}
	const key_line = line(
		has_key,
		has_key
			? __("Private key loaded. It is stored encrypted and never shown.")
			: __("No private key loaded.")
	);

	field.$wrapper.html(`
		<div class="mb-3">${certificate_line}${key_line}</div>
		<button class="btn btn-sm btn-default mr-2" data-action="load">${__("Load Files")}</button>
		<button class="btn btn-sm btn-default" data-action="csr">${__("Generate Key and CSR")}</button>
		<p class="text-muted small mt-2">${__(
			"New to ARCA? Use 'Generate Key and CSR': the key is created here and never leaves the server. Already have a certificate and key? Use 'Load Files'."
		)}</p>
	`);
	field.$wrapper
		.find("[data-action=load]")
		.on("click", () => load_files_dialog(frm, environment));
	field.$wrapper.find("[data-action=csr]").on("click", () => generate_csr(frm, environment));
}

function read_file(input) {
	const file = input.files && input.files[0];
	if (!file) return Promise.resolve(null);
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(reader.result);
		reader.onerror = () => reject(reader.error);
		reader.readAsText(file);
	});
}

function load_files_dialog(frm, environment) {
	const method =
		"arca_integration.arca.doctype.arca_company_settings.arca_company_settings.load_credentials";
	const esc = frappe.utils.escape_html;
	const files = { certificate: null, private_key: null };

	const dialog = new frappe.ui.Dialog({
		title: __("Load Credentials for {0}", [__(environment)]),
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Save"),
		primary_action() {
			frappe
				.call({
					method,
					args: { name: frm.doc.name, environment, ...files, save: 1 },
					freeze: true,
				})
				.then(() => {
					dialog.hide();
					frm.reload_doc();
					frappe.show_alert({ message: __("Credentials saved"), indicator: "green" });
				});
		},
	});

	const body = dialog.fields_dict.body.$wrapper;
	body.html(`
		<div class="form-group">
			<label class="control-label">${__("Certificate issued by ARCA (.crt)")}</label>
			<input type="file" class="form-control" data-file="certificate" accept=".crt,.pem,.cer,.txt">
		</div>
		<div class="form-group">
			<label class="control-label">${__("Private key (.key)")}</label>
			<input type="file" class="form-control" data-file="private_key" accept=".key,.pem,.txt">
			<p class="help-box small text-muted">${__(
				"Optional if the key was generated here. The file is read by your browser and sent only to this site; it is stored encrypted, not as an attachment."
			)}</p>
		</div>
		<div data-result></div>
	`);
	dialog.get_primary_btn().prop("disabled", true);

	const show = (html) => body.find("[data-result]").html(html);

	body.find("input[type=file]").on("change", async function () {
		files[$(this).data("file")] = await read_file(this);
		dialog.get_primary_btn().prop("disabled", true);
		if (!files.certificate && !files.private_key) return show("");

		frappe
			.call({
				method,
				args: { name: frm.doc.name, environment, ...files },
				error_msg: false,
			})
			.then(({ message }) => {
				const parts = [];
				if (message.subject) {
					parts.push(
						`<div>✓ ${__("Certificate of CUIT {0}, valid until {1}", [
							esc(message.cuit || "—"),
							esc(message.expires_on),
						])}</div>`
					);
				}
				if (message.matches)
					parts.push(`<div>✓ ${__("The certificate matches the private key.")}</div>`);
				else if (!message.key_loaded)
					parts.push(
						`<div class="text-muted">${__(
							"There is no private key yet for this certificate."
						)}</div>`
					);
				for (const warning of message.warnings || []) {
					parts.push(`<div class="alert alert-warning mt-2">${esc(warning)}</div>`);
				}
				show(`<div class="alert alert-success">${parts.join("")}</div>`);
				dialog.get_primary_btn().prop("disabled", false);
			})
			.catch(() => show(""));
	});

	dialog.show();
}
