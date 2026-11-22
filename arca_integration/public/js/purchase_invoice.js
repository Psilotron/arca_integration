// Copyright (c) 2026, Psilotron and contributors
// For license information, please see license.txt

// Purchase invoices: read the supplier voucher's QR and verify it with ARCA before submitting.
frappe.provide("arca.purchase");

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) {
		arca.purchase.clear(frm);
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Scan QR"), () => arca.purchase.scan(frm), __("ARCA"));
			frm.add_custom_button(__("Paste QR Link"), () => arca.purchase.paste(frm), __("ARCA"));
		}
		if (frm.is_new()) return;
		frappe
			.call({
				method: "arca_integration.arca.purchase.get_status",
				args: { name: frm.doc.name },
			})
			.then(({ message }) => message.applies && arca.purchase.render(frm, message));
	},
});

arca.purchase.clear = function (frm) {
	frm.layout.message.find(".arca-purchase").remove();
	if (!frm.layout.message.children().length) frm.layout.message.addClass("hidden");
};

arca.purchase.render = function (frm, status) {
	const esc = frappe.utils.escape_html;
	let text, color;
	if (status.verified) {
		text = `✓ ${__("Verified with ARCA on {0}", [
			frappe.datetime.str_to_user(status.verified_on),
		])}`;
		color = "green";
	} else if (status.verification === "Rejected" && !status.outdated) {
		text = `${__("ARCA does not confirm this voucher:")} ${esc(status.message).replace(
			/\n/g,
			"<br>"
		)}`;
		color = "red";
	} else if (status.missing.length) {
		text = __("To verify it with ARCA, scan the voucher's QR or complete: {0}.", [
			esc(status.missing.join(", ")),
		]);
		color = "orange";
	} else {
		text = __("Not verified with ARCA yet. It is verified when you submit it.");
		color = "orange";
	}
	// Wrapped: without tags, Frappe shows the text as is, escaped entities included.
	frm.dashboard.set_headline(`<div>${text}</div>`, color, true).addClass("arca-purchase");

	if (frm.doc.docstatus === 0 && !status.missing.length && !status.verified) {
		frm.add_custom_button(__("Verify in ARCA"), () => arca.purchase.verify(frm), __("ARCA"));
	}
};

arca.purchase.scan = function (frm) {
	const scanner = new frappe.ui.Scanner({
		dialog: true,
		multiple: false,
		on_scan(data) {
			const text = data && (data.decodedText || (data.result && data.result.text));
			if (!text) return;
			scanner.dialog && scanner.dialog.hide();
			arca.purchase.read(frm, text);
		},
	});
};

arca.purchase.paste = function (frm) {
	frappe.prompt(
		{
			fieldname: "link",
			fieldtype: "Data",
			label: __("QR Link"),
			length: 2000,
			reqd: 1,
			description: __(
				"The link the voucher's QR opens: https://www.arca.gob.ar/fe/qr/?p=..."
			),
		},
		({ link }) => arca.purchase.read(frm, link),
		__("Paste QR Link"),
		__("Read")
	);
};

arca.purchase.read = function (frm, content) {
	frappe
		.call({
			method: "arca_integration.arca.purchase.read_qr",
			args: { content, company: frm.doc.company, supplier: frm.doc.supplier },
		})
		.then(async ({ message }) => {
			const { supplier, ...values } = message.values;
			if (supplier) await frm.set_value("supplier", supplier);
			await frm.set_value(values);

			const warnings = [...message.warnings];
			const total = flt(frm.doc.grand_total);
			if (total && Math.abs(Math.abs(total) - message.total) >= 1) {
				warnings.push(
					__("The voucher's total is {0}, the invoice's is {1}.", [
						format_currency(message.total, frm.doc.currency),
						format_currency(total, frm.doc.currency),
					])
				);
			}
			if (warnings.length) {
				frappe.msgprint({
					title: __("Check the voucher"),
					indicator: "orange",
					message: warnings.map((w) => `• ${frappe.utils.escape_html(w)}`).join("<br>"),
				});
			} else {
				frappe.show_alert({
					message: __("Voucher data loaded from the QR."),
					indicator: "green",
				});
			}
		});
};

arca.purchase.verify = async function (frm) {
	if (frm.is_dirty()) await frm.save();
	const { message } = await frappe.call({
		method: "arca_integration.arca.purchase.verify_in_arca",
		args: { name: frm.doc.name },
		freeze: true,
		freeze_message: __("Asking ARCA..."),
	});
	frappe.show_alert({
		message: message.verified
			? __("Verified with ARCA")
			: __("ARCA does not confirm this voucher"),
		indicator: message.verified ? "green" : "red",
	});
	frm.reload_doc();
};
