frappe.ui.form.on("ARCA Mapeo de Cuenta de Impuesto", {
	setup(frm) {
		frm.set_query("account", () => ({
			filters: { company: frm.doc.company },
		}));
	},
});
