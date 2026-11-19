(() => {
	window.arca_consultar_padron = async function (
		frm,
		{ customer, sales_invoice, supplier, company } = {},
	) {
		const cuit = String(frm.doc.tax_id || "").trim();
		if (!cuit) {
				frappe.msgprint(__("Ingresá primero el CUIT en el identificador fiscal."));
			return;
		}

		const response = await frappe.call({
			method: "arca_integration.api.perfiles_padron",
		});
		let perfiles = response.message || [];
		if (company) {
			perfiles = perfiles.filter((perfil) => perfil.company === company);
		}
		if (!perfiles.length) {
			frappe.msgprint(
				company
						? __("No hay perfiles ARCA para la empresa {0}. Revisá la configuración ARCA.", [
							company,
						])
						: __("No hay perfiles ARCA disponibles para tu usuario. Revisá la configuración ARCA y los permisos de la empresa."),
			);
			return;
		}

		const por_etiqueta = {};
		const opciones = perfiles.map((perfil) => {
			const etiqueta = `${perfil.company} · ${perfil.environment}${perfil.is_default ? ` · ${__("predeterminado")}` : ""}`;
			por_etiqueta[etiqueta] = perfil;
			return etiqueta;
		});
		const selector = new frappe.ui.Dialog({
			title: __("Consultar CUIT en ARCA"),
			fields: [
				{
					fieldname: "profile",
					fieldtype: "Select",
					label: __("Empresa y ambiente"),
					options: ["", ...opciones].join("\n"),
					reqd: 1,
				},
			],
			primary_action_label: __("Consultar"),
			primary_action: async ({ profile }) => {
				const seleccionado = por_etiqueta[profile];
				if (!seleccionado) {
					frappe.msgprint(__("Elegí la empresa y el ambiente para la consulta."));
					return;
				}
				selector.hide();
				const resultado = await frappe.call({
					method: "arca_integration.api.consultar",
					args: {
						cuit,
						company: seleccionado.company,
						environment: seleccionado.environment,
						customer: customer || null,
						sales_invoice: sales_invoice || null,
						supplier: supplier || null,
					},
					freeze: true,
					freeze_message: __("Consultando ARCA..."),
				});
				const persona = resultado.message;
				if (!persona) return;
				if (persona.name && !customer && !sales_invoice) {
					frm.__arca_padron_queries ||= [];
					if (!frm.__arca_padron_queries.includes(persona.name)) {
						frm.__arca_padron_queries.push(persona.name);
					}
				}
				if (persona.status === "OK") {
					mostrar_comparacion(frm, persona);
				} else if (persona.status === "No encontrado") {
					frappe.msgprint({
						title: __("CUIT no encontrado"),
						message: `${contexto_consulta(persona)}<br>${__("ARCA no encontró una persona con el CUIT consultado.")}<br><br>${frappe.utils.escape_html(persona.error || "")}`,
						indicator: "orange",
					});
				} else {
					frappe.msgprint({
						title: __("No se pudo consultar ARCA"),
						message: `${contexto_consulta(persona)}<br>${frappe.utils.escape_html(persona.error || __("Revisá la configuración y volvé a intentar."))}`,
						indicator: "red",
					});
				}
			},
		});
		selector.show();
	};

	function mostrar_comparacion(frm, persona) {
		const actual = frm.doc.customer_name || frm.doc.supplier_name || "";
		const propuesto = persona.razon_social || "";
		const fieldname =
			frm.doctype === "Customer"
				? "customer_name"
				: frm.doctype === "Supplier"
					? "supplier_name"
					: null;
		const permite_aplicar = Boolean(fieldname);
		const entidad = frm.doctype === "Supplier" ? __("proveedor") : __("cliente");
		const fields = [
			{ fieldtype: "HTML", fieldname: "context" },
			{ fieldtype: "HTML", fieldname: "comparison" },
		];
		if (permite_aplicar) {
			fields.push({
				fieldtype: "Check",
				fieldname: "apply_registered_name",
				label: __("Aplicar la razón social al nombre del {0}", [entidad]),
				default: 0,
				description: actual
					? `${__("Actual")}: ${actual}`
					: __("El nombre actual está vacío."),
			});
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Revisar datos del Padrón"),
			fields,
			primary_action_label: permite_aplicar ? __("Aplicar selección") : __("Cerrar"),
			primary_action(values) {
				if (permite_aplicar && values.apply_registered_name && propuesto) {
					frm.set_value(fieldname, propuesto);
					frappe.show_alert({ message: __("Revisá los cambios y guardá el registro desde ERPNext."), indicator: "blue" });
				}
				dialog.hide();
			},
		});
		dialog.fields_dict.context.$wrapper.html(contexto_consulta(persona));
		dialog.fields_dict.comparison.$wrapper.html(
			`<div class="mb-3"><b>${__("CUIT")}</b>: ${frappe.utils.escape_html(persona.cuit || frm.doc.tax_id || "")}</div>` +
				`<div class="mb-3"><b>${__("Razón social en ERPNext")}</b>: ${frappe.utils.escape_html(actual || __("(vacío)"))}</div>` +
				`<div class="mb-3"><b>${__("Razón social informada por ARCA")}</b>: ${frappe.utils.escape_html(propuesto || __("(sin dato)"))}</div>` +
				`<div class="mb-3"><b>${__("Tipo de persona informado por ARCA")}</b>: ${frappe.utils.escape_html(persona.tipo_persona || __("(sin dato)"))}</div>` +
				`<div class="mb-3"><b>${__("Estado de la clave informado por ARCA")}</b>: ${frappe.utils.escape_html(persona.estado_clave || __("(sin dato)"))}</div>` +
				`<div class="text-muted">${__("La consulta es informativa; no determina la condición frente a WSFE.")}</div>`,
		);
		dialog.show();
	}

	function contexto_consulta(persona) {
		const empresa = frappe.utils.escape_html(persona.company || "");
		const ambiente = frappe.utils.escape_html(persona.environment || "");
		const consultado = frappe.utils.escape_html(persona.queried_at || "");
		return `<div class="text-muted mb-3"><b>${__("Empresa")}</b>: ${empresa} · <b>${__("Ambiente")}</b>: ${ambiente} · <b>${__("Consultado")}</b>: ${consultado}</div>`;
	}
})();
