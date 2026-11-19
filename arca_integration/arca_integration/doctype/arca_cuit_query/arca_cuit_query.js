frappe.ui.form.on("ARCA CUIT Query", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Consultar"), () => {
			if (!["Homologación", "Producción"].includes(frm.doc.environment)) {
				frappe.msgprint(__("Elegí Homologación o Producción antes de consultar."));
				return;
			}

			frappe.call({
				method: "arca_integration.api.consultar",
				args: {
					company: frm.doc.company,
					cuit: frm.doc.cuit,
					environment: frm.doc.environment,
					name: frm.doc.name,
					customer: frm.doc.customer,
					supplier: frm.doc.supplier,
					sales_invoice: frm.doc.sales_invoice,
				},
				freeze: true,
				freeze_message: __("Consultando ARCA..."),
				callback(response) {
					const result = response.message;
					if (!result) {
						return;
					}
					if (result.status === "OK") {
						frappe.show_alert({
							message: result.razon_social || result.tipo_persona,
							indicator: "green",
						});
					} else if (result.status === "No encontrado") {
						frappe.show_alert({ message: result.error, indicator: "orange" });
					} else {
						frappe.show_alert({ message: result.error, indicator: "red" });
					}
					if (result.name) {
						frappe.set_route("Form", "ARCA CUIT Query", result.name);
					} else {
						frm.reload_doc();
					}
				},
			});
		});
	},
});
