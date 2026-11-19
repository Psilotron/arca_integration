app_name = "arca_integration"
app_title = "ARCA Integration"
app_publisher = "Psilotron"
app_description = "Consulta de contribuyentes contra los web services de ARCA (ex-AFIP)."
app_email = "sistemas@psilotron.com"
app_license = "mit"
app_version = "0.1.0"

required_apps = ["frappe"]

# Includes in <head>
# ------------------
# app_include_css = "/assets/arca_integration/css/arca_integration.css"
# app_include_js = "/assets/arca_integration/js/arca_integration.js"

# Include js in doctype views
# ---------------------------
# The emission proposal is reached from the invoice it belongs to. No DocType of
# ERPNext is modified: the button only navigates.
doctype_js = {
	"Customer": ["public/js/padron_lookup.js", "public/js/customer.js"],
	"Supplier": ["public/js/padron_lookup.js", "public/js/supplier.js"],
	"Sales Invoice": ["public/js/padron_lookup.js", "public/js/sales_invoice.js"],
}

doc_events = {
	"Sales Invoice": {
		"on_trash": "arca_integration.api.evitar_borrar_factura_con_intento_arca",
	},
}

