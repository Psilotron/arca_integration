frappe.listview_settings["ARCA Comprobante"] = {
	add_fields: ["estado"],
	get_indicator(doc) {
		const colores = {
			Borrador: "gray",
			Solicitando: "blue",
			Autorizado: "green",
			Rechazado: "red",
			Incierto: "orange",
		};

		return [
			__(doc.estado || __("Sin estado")),
			colores[doc.estado] || "gray",
			"estado,=," + (doc.estado || ""),
		];
	},
};
