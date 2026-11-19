/**
 * The way in, from the invoice the operator already has open.
 *
 * Submitting the invoice does nothing: this button prepares a proposal, and the
 * emission stays on the proposal's own Emitir action.
 */
frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		agregar_boton_padron(frm);
		frm.remove_custom_button(__("Historial de consultas ARCA"), __("ARCA"));
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Historial de consultas ARCA"),
				() =>
					frappe.set_route("List", "ARCA CUIT Query", {
						sales_invoice: frm.doc.name,
					}),
				__("ARCA"),
			);
		}

		if (frm.doc.docstatus !== 1) {
			return;
		}

		frm.add_custom_button(
			__("Comprobante ARCA"),
			() => {
				frappe.call({
					method: "arca_integration.api.preparar_comprobante",
					args: { sales_invoice: frm.doc.name },
					freeze: true,
					freeze_message: __("Preparando el comprobante..."),
					callback(response) {
						if (response.message) {
							frappe.set_route("Form", "ARCA Comprobante", response.message);
						}
					},
				});
			},
			__("ARCA"),
		);
	},
	tax_id(frm) {
		agregar_boton_padron(frm);
	},
	customer(frm) {
		agregar_boton_padron(frm);
	},
});

function agregar_boton_padron(frm) {
	frm.remove_custom_button(__("Consultar CUIT en ARCA"), __("ARCA"));
	if (frm.is_new() || !frm.doc.customer || !frm.doc.tax_id) return;
	frm.add_custom_button(
		__("Consultar CUIT en ARCA"),
		() =>
			window.arca_consultar_padron(frm, {
				customer: frm.doc.customer,
				sales_invoice: frm.doc.name,
				company: frm.doc.company,
			}),
		__("ARCA"),
	);
}
