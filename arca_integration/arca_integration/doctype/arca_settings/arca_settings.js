frappe.ui.form.on("ARCA Settings", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Consultar puntos de venta"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(
					__("Guardá los cambios del perfil antes de consultar ARCA.")
				);
				return;
			}

			frappe.call({
				method: "arca_integration.api.consultar_puntos_venta",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Consultando los puntos de venta en ARCA..."),
				callback(response) {
					const puntos = response.message;
					if (!Array.isArray(puntos)) {
						frappe.msgprint(
							__("ARCA devolvió una respuesta de puntos de venta inválida.")
						);
						return;
					}
					seleccionar_punto(frm, puntos);
				},
			});
		});
	},
});

function seleccionar_punto(frm, puntos) {
	if (!puntos.length) {
		frappe.msgprint({
			title: __("Sin puntos de venta"),
			message: __(
				"ARCA no devolvió puntos de venta. El valor configurado no se modificó."
			),
			indicator: "orange",
		});
		return;
	}

	const seleccionables = new Map();
	const opciones = puntos.map((punto, index) => {
		const etiqueta = `${index + 1}. ${etiqueta_punto(punto)}`;
		seleccionables.set(etiqueta, punto);
		return etiqueta;
	});
	const valorActual = opciones.find(
		(etiqueta) =>
			String(seleccionables.get(etiqueta).nro) === String(frm.doc.pto_venta)
	);
	const dialog = new frappe.ui.Dialog({
		title: __("Puntos de venta informados por ARCA"),
		fields: [
			{
				fieldname: "pto_venta",
				fieldtype: "Select",
				label: __("Punto de venta"),
				options: opciones,
				default: valorActual,
				reqd: 1,
			},
		],
		primary_action_label: __("Usar punto de venta"),
		primary_action(values) {
			const punto = seleccionables.get(values.pto_venta);
			if (!punto) {
				frappe.msgprint(__("Seleccioná un punto de venta del catálogo."));
				return;
			}
			if (!elegible_para_cae(punto)) {
				frappe.msgprint({
					message: __(
						"Este punto no figura como disponible para emitir CAE. El valor configurado no se modificó."
					),
					indicator: "orange",
				});
				return;
			}

			frm.set_value("pto_venta", punto.nro).then(() => dialog.hide());
		},
	});
	dialog.show();
}

function etiqueta_punto(punto) {
	const estado = [];
	if (punto.emision_tipo === "CAE") {
		estado.push(__("Emisión CAE"));
	} else {
		estado.push(
			__("Modalidad {0} (no compatible con emisión CAE)", [
				punto.emision_tipo || __("desconocida"),
			])
		);
	}
	if (punto.bloqueado) {
		estado.push(__("bloqueado"));
	}
	if (punto.fch_baja) {
		estado.push(__("fecha de baja: {0}", [punto.fch_baja]));
	}
	if (elegible_para_cae(punto)) {
		estado.push(__("disponible según el catálogo"));
	}

	return `${__("Punto {0}", [punto.nro])} — ${estado.join(", ")}`;
}

function elegible_para_cae(punto) {
	return (
		punto.emision_tipo === "CAE" &&
		!punto.bloqueado &&
		!punto.fch_baja
	);
}
