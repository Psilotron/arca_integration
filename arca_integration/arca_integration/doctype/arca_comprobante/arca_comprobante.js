// ARCA speaks in numeric codes and the DocType stores them as they arrive.
// The form shows what each code means; the stored value is never rewritten, so
// existing documents and the emission engine keep reading the same fields.
(() => {
	const ETIQUETAS_DE_CODIGO = {
		cbte_tipo: {
			1: __("Factura A"),
			6: __("Factura B"),
		},
		condicion_iva_receptor: {
			1: __("IVA Responsable Inscripto"),
			4: __("Sujeto Exento"),
			5: __("Consumidor Final"),
			6: __("Responsable Monotributo"),
			7: __("Sujeto No Categorizado"),
			8: __("Proveedor del Exterior"),
			9: __("Cliente del Exterior"),
			10: __("IVA Liberado - Ley 19.640"),
			13: __("Monotributista Social"),
			15: __("IVA No Alcanzado"),
			16: __("Monotributo Trabajador Independiente Promovido"),
		},
		doc_tipo: {
			80: __("CUIT"),
			96: __("DNI"),
			99: __("Sin identificar"),
		},
		cbte_clase: {
			A: __("Factura A"),
			B: __("Factura B"),
		},
		resultado: {
			A: __("Autorizado"),
			R: __("Rechazado"),
		},
	};

	const COLORES_DE_ESTADO = {
		Borrador: "gray",
		Solicitando: "blue",
		Autorizado: "green",
		Rechazado: "red",
		Incierto: "orange",
	};

	frappe.ui.form.on("ARCA Comprobante", {
		refresh(frm) {
			mostrar_etiquetas(frm);
			pintar_estado(frm);

			if (frm.is_new()) {
				return;
			}

			if (["Borrador", "Rechazado"].includes(frm.doc.estado)) {
				frm.add_custom_button(__("Actualizar datos de la factura"), () =>
					actualizar(frm)
				);
				frm.add_custom_button(__("Emitir"), () => emitir(frm)).addClass(
					"btn-primary"
				);
			}

			if (["Incierto", "Solicitando"].includes(frm.doc.estado)) {
				frm.add_custom_button(__("Conciliar"), () => conciliar(frm)).addClass(
					"btn-primary"
				);
			}
		},
		doc_tipo(frm) {
			if (String(frm.doc.doc_tipo || "") === "99") {
				frm.set_value("doc_nro", "0");
			}
		},
	});

	// Frappe's Select accepts { label, value } options at runtime. The dropdown
	// shows the wording while the document continues storing ARCA's code.
	function mostrar_etiquetas(frm) {
		Object.entries(ETIQUETAS_DE_CODIGO).forEach(([campo, etiquetas]) => {
			const docfield = frm.get_docfield(campo);
			const opciones = Object.entries(etiquetas).map(([value, label]) => ({
				label,
				value,
			}));

			docfield.formatter = (value) => etiquetas[String(value)] || value || "";

			if (JSON.stringify(docfield.options) !== JSON.stringify(opciones)) {
				frm.set_df_property(campo, "options", opciones);
			}
		});
	}

	function pintar_estado(frm) {
		if (!frm.doc.estado) {
			return;
		}

		frm.page.set_indicator(
			__(frm.doc.estado),
			COLORES_DE_ESTADO[frm.doc.estado] || "gray"
		);

		if (frm.doc.problema) {
			frm.dashboard.set_headline(
				__("No se puede emitir todavía: {0}", [frm.doc.problema]),
				"red"
			);
			return;
		}

		if (frm.doc.estado === "Autorizado" && frm.doc.cae) {
			const titular = frm.doc.cae_vence
				? __("Autorizado por ARCA. CAE {0}, vence el {1}.", [
						frm.doc.cae,
						frappe.datetime.str_to_user(frm.doc.cae_vence),
				  ])
				: __("Autorizado por ARCA. CAE {0}.", [frm.doc.cae]);
			frm.dashboard.set_headline(titular, "green");
			return;
		}

		if (["Borrador", "Rechazado"].includes(frm.doc.estado)) {
			frm.set_intro(
				__("Revisá los datos propuestos y emití cuando estén listos."),
				"blue"
			);
		}
	}

	function actualizar(frm) {
		frappe.call({
			method: "arca_integration.api.preparar_comprobante",
			args: { sales_invoice: frm.doc.sales_invoice },
			freeze: true,
			freeze_message: __("Leyendo la factura..."),
			callback: () => frm.reload_doc(),
			error: () => frm.reload_doc(),
		});
	}

	function emitir(frm) {
		frappe.confirm(
			__(
				"Se va a pedir el CAE a ARCA para la factura {0}. La emisión no se puede deshacer. ¿Continuar?",
				[frm.doc.sales_invoice]
			),
			() => {
				frappe.call({
					method: "arca_integration.api.emitir_comprobante",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Pidiendo el CAE a ARCA..."),
					callback(response) {
						const result = response.message;
						if (result && result.mensaje) {
							frappe.show_alert({
								message: result.mensaje,
								indicator:
									result.estado === "Autorizado" ? "green" : "orange",
							});
						}
						frm.reload_doc();
					},
					// A refusal can update the proposal (the environment, the pending
					// problem), so the form is read again before the next attempt.
					error: () => frm.reload_doc(),
				});
			}
		);
	}

	function conciliar(frm) {
		frappe.call({
			method: "arca_integration.api.conciliar_comprobante",
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: __("Consultando el comprobante en ARCA..."),
			callback(response) {
				const result = response.message;
				if (result && result.mensaje) {
					frappe.show_alert({
						message: result.mensaje,
						indicator: result.estado === "Autorizado" ? "green" : "orange",
					});
				}
				frm.reload_doc();
			},
			error: () => frm.reload_doc(),
		});
	}
})();
