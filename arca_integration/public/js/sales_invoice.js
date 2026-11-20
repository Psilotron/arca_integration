// Copyright (c) 2026, Psilotron and contributors
// For license information, please see license.txt

// "ARCA review" on draft invoices and the preview shown before submitting.
frappe.provide("arca");

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		frm.__arca_review = null;
		arca.clear_review(frm);
		if (frm.doc.docstatus === 1 && frm.doc.arca_cae) return arca.render_authorized(frm);
		if (frm.doc.docstatus !== 0 || frm.is_new()) return;
		arca.load_review(frm);
	},

	on_submit(frm) {
		if (!frm.doc.arca_cae) return;
		frappe.show_alert({
			message: __("Authorized by ARCA · CAE {0}", [frm.doc.arca_cae]),
			indicator: "green",
		});
	},

	before_submit(frm) {
		if (!frm.__arca_review || !frm.__arca_review.applies) return;
		return arca.confirm_preview(frm);
	},
});

arca.load_review = function (frm) {
	return frappe
		.call({
			method: "arca_integration.arca.invoice.get_review",
			args: { name: frm.doc.name },
		})
		.then(({ message }) => {
			frm.__arca_review = message;
			if (message.applies) arca.render_review(frm, message);
			return message;
		});
};

arca.clear_review = function (frm) {
	frm.layout.message.find(".arca-review").remove();
	if (!frm.layout.message.children().length) frm.layout.message.addClass("hidden");
};

arca.render_authorized = function (frm) {
	const doc = frm.doc;
	const number = `${String(doc.arca_point_of_sale || "").slice(-5)}-${String(
		doc.arca_voucher_number
	).padStart(8, "0")}`;
	frm.dashboard
		.set_headline(
			`✓ ${__("Authorized by ARCA")} · ${frappe.utils.escape_html(number)} · ${__(
				"CAE {0}, due {1}",
				[doc.arca_cae, frappe.datetime.str_to_user(doc.arca_cae_due_date)]
			)}`,
			"green",
			true
		)
		.addClass("arca-review");
};

arca.render_review = function (frm, review) {
	arca.clear_review(frm);
	const esc = frappe.utils.escape_html;
	const voucher = review.voucher;
	const title = voucher
		? `${esc(voucher.voucher_type_name)} · ${__("Point of sale {0}", [
				String(voucher.point_of_sale).padStart(5, "0"),
		  ])}`
		: __("ARCA review");
	const homologation =
		review.environment === "Homologation" ? ` · <b>${__("Homologation")}</b>` : "";

	if (review.ready) {
		frm.dashboard
			.set_headline(
				`<b>${title}</b>${homologation} · ✓ ${__("Ready to authorize")}`,
				"green",
				true
			)
			.addClass("arca-review");
		return;
	}

	const rows = review.issues
		.map((issue, i) => {
			const button = issue.action
				? ` <button class="btn btn-xs btn-default ml-2" data-issue="${i}">${esc(
						issue.action.label
				  )}</button>`
				: "";
			return `<li class="mb-1">${esc(issue.message)}${button}</li>`;
		})
		.join("");
	const $panel = frm.dashboard
		.set_headline(
			`<div><b>${title}</b>${homologation} · ${__("ARCA would reject it:")}</div>
		<ul class="mb-0 mt-1 pl-4">${rows}</ul>`,
			"orange",
			true
		)
		.addClass("arca-review");
	$panel.find("[data-issue]").on("click", function () {
		arca.run_action(frm, review.issues[$(this).data("issue")].action);
	});
};

arca.run_action = function (frm, action) {
	if (action.doctype === frm.doctype && action.name === frm.doc.name) {
		frm.scroll_to_field(action.fieldname);
		return;
	}
	if (!action.name) return;
	frappe.set_route("Form", action.doctype, action.name);
};

arca.confirm_preview = function (frm) {
	return new Promise((resolve) => {
		// Always review the saved draft: what is shown is what will be checked on the server.
		arca.load_review(frm).then((review) => {
			if (!review.ready) {
				frappe.validated = false;
				frappe.msgprint({
					title: __("ARCA would reject this invoice"),
					message: review.issues
						.map((issue) => `• ${frappe.utils.escape_html(issue.message)}`)
						.join("<br>"),
					indicator: "orange",
				});
				return resolve();
			}

			const dialog = new frappe.ui.Dialog({
				title: __("ARCA Preview"),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "preview" }],
				primary_action_label:
					review.environment === "Homologation"
						? __("Submit and request test CAE")
						: __("Submit and request CAE"),
				primary_action() {
					dialog.__confirmed = true;
					dialog.hide();
					resolve();
				},
				secondary_action_label: __("Cancel"),
				secondary_action() {
					dialog.hide();
				},
				on_hide() {
					if (!dialog.__confirmed) {
						frappe.validated = false;
						resolve();
					}
				},
			});
			dialog.fields_dict.preview.$wrapper.html(arca.preview_html(review));
			dialog.show();
		});
	});
};

arca.preview_html = function (review) {
	const v = review.voucher;
	const esc = frappe.utils.escape_html;
	const money = (value) => format_currency(value, v.currency);
	const date = (value) => (value ? frappe.datetime.str_to_user(value) : "");
	const row = (label, value, extra = "") =>
		`<tr><td>${esc(label)}</td><td class="text-right">${money(
			value
		)}</td><td class="text-right text-muted">${extra}</td></tr>`;

	const lines = [];
	if (flt(v.taxed)) lines.push(row(__("Taxed"), v.taxed));
	for (const vat of v.vat_subtotals) lines.push(row(__("VAT {0}", [vat.name]), vat.amount));
	if (flt(v.exempt)) lines.push(row(__("Exempt"), v.exempt));
	if (flt(v.untaxed)) lines.push(row(__("Untaxed"), v.untaxed));
	for (const tax of v.other_taxes) lines.push(row(tax.name, tax.amount));

	const service =
		v.service_start &&
		` · ${__("from {0} to {1}", [date(v.service_start), date(v.service_end)])} · ${__(
			"payment due {0}",
			[date(v.payment_due)]
		)}`;
	const document = v.document_number
		? ` · ${esc(v.document_type)} ${esc(v.document_number)}`
		: ` · ${__("Unidentified")}`;
	const homologation =
		review.environment === "Homologation"
			? `<div class="alert alert-warning">${__(
					"Homologation: vouchers authorized in this environment have no fiscal validity."
			  )}</div>`
			: "";

	return `${homologation}
		<h5 class="mb-1">${esc(v.voucher_type_name)} · ${__("Point of sale {0}", [
		String(v.point_of_sale).padStart(5, "0"),
	])}</h5>
		<p class="text-muted mb-3">${__("The number is assigned by ARCA when authorizing.")}</p>
		<p class="mb-1"><b>${__("Customer")}:</b> ${esc(v.receiver)}${document} · ${esc(
		v.receiver_vat_condition || ""
	)}</p>
		<p><b>${__("ARCA Concept")}:</b> ${esc(v.concept)}${service || ""}</p>
		<table class="table table-sm">
			<tbody>${lines.join("")}</tbody>
			<tfoot><tr class="bold"><td>${__("ARCA Total")}</td><td class="text-right">${money(
		v.total
	)}</td><td class="text-right text-success">✓ ${__("matches the invoice")}</td></tr></tfoot>
		</table>
		<div class="alert alert-info mb-0">${__(
			"Once authorized it cannot be changed or deleted. To correct it you will have to issue a credit note."
		)}</div>`;
};
