frappe.ui.form.on("Customer", {
	refresh(frm) {
		agregar_boton(frm);
		if (!frm.is_new()) {
			frm.add_custom_button(
				__("Historial de consultas ARCA"),
				() => frappe.set_route("List", "ARCA CUIT Query", { customer: frm.doc.name }),
				__("ARCA"),
			);
		}
	},
	tax_id(frm) {
		agregar_boton(frm);
	},
	after_save(frm) {
		const pending = frm.__arca_padron_queries || [];
		if (!pending.length) return;
		Promise.all(
			pending.map((query) =>
				frappe.call({
					method: "arca_integration.api.vincular_consulta_a_cliente",
					args: { query, customer: frm.doc.name },
				}),
			),
		).then(() => {
			frm.__arca_padron_queries = (frm.__arca_padron_queries || []).filter(
				(query) => !pending.includes(query),
			);
		});
	},
});

function agregar_boton(frm) {
	frm.remove_custom_button(__("Consultar CUIT en ARCA"), __("ARCA"));
	if (!frm.doc.tax_id) return;
	frm.add_custom_button(
		__("Consultar CUIT en ARCA"),
		() =>
			window.arca_consultar_padron(frm, {
				customer: frm.is_new() ? null : frm.doc.name,
			}),
		__("ARCA"),
	);
}
