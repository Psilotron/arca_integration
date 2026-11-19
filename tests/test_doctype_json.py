import json
import re
from pathlib import Path

from arca_integration.core.comprobante import ESTADOS, TIPOS_DE_COMPROBANTE, Estado
from arca_integration.core.wsfe.types import CondicionIvaReceptor

ROOT = Path(__file__).parent.parent / "arca_integration" / "arca_integration" / "doctype"


def load(name: str) -> dict:
    return json.loads((ROOT / name / f"{name}.json").read_text())


def test_settings_doctype_has_required_fields():
    doc = load("arca_settings")
    names = {field["fieldname"] for field in doc["fields"]}

    assert {
        "company",
        "environment",
        "is_default",
        "certificate",
        "private_key",
        "cuit_representada",
    } <= names
    assert doc.get("issingle", 0) == 0
    assert doc["module"] == "Arca Integration"
    assert doc["name"] == "ARCA Settings"
    assert doc["translated_doctype"] == 1


def test_settings_doctype_has_spanish_title_translation():
    path = ROOT.parent.parent / "translations" / "es.csv"
    assert "ARCA Settings,Configuracion ARCA" in path.read_text()


def test_settings_ship_no_arca_host_and_no_default_environment():
    doc = load("arca_settings")
    fields = {field["fieldname"]: field for field in doc["fields"]}

    assert fields["environment"]["options"] == "Homologación\nProducción"
    assert "default" not in fields["environment"]
    assert fields["private_key"]["fieldtype"] == "Password"
    assert fields["private_key"]["hidden"] == 1
    assert not fields["private_key_paste"].get("hidden")
    assert fields["is_default"]["default"] == 0
    assert not fields["company"].get("unique")
    assert doc["autoname"] == "hash"
    assert fields["cbte_tipo"]["fieldtype"] == "Select"
    assert fields["cbte_tipo"]["options"] == "1\n6"


def test_query_doctype_fields():
    doc = load("arca_cuit_query")
    names = {field["fieldname"] for field in doc["fields"]}

    assert {
        "company",
        "cuit",
        "status",
        "razon_social",
        "tipo_persona",
        "estado_clave",
        "error_message",
        "raw_response",
        "queried_at",
    } <= names
    assert doc["autoname"] == "hash"
    assert doc["module"] == "Arca Integration"


def test_query_doctype_belongs_to_a_company():
    doc = load("arca_cuit_query")
    fields = {field["fieldname"]: field for field in doc["fields"]}

    assert fields["company"]["fieldtype"] == "Link"
    assert fields["company"]["options"] == "Company"
    assert fields["company"]["reqd"] == 1
    assert doc["field_order"][0] == "company"


def test_query_status_defaults_to_pending():
    doc = load("arca_cuit_query")
    fields = {field["fieldname"]: field for field in doc["fields"]}

    assert fields["status"]["default"] == "Pendiente"
    assert fields["status"]["options"] == "Pendiente\nOK\nNo encontrado\nError"
    assert fields["cuit"]["reqd"] == 1


# --- ARCA Comprobante --------------------------------------------------------
#
# The document is not submittable on purpose: ERPNext's submit step means "this
# record is final", and a comprobante is only final once ARCA answered. The
# engine owns ``estado``; the form must not be able to invent a lifecycle.


def comprobante() -> dict:
    return load("arca_comprobante")


def comprobante_fields() -> dict:
    return {field["fieldname"]: field for field in comprobante()["fields"]}


def test_the_comprobante_is_a_regular_document_found_from_its_invoice():
    doc = comprobante()
    fields = comprobante_fields()

    assert doc["name"] == "ARCA Comprobante"
    assert doc["module"] == "Arca Integration"
    assert doc["autoname"] == "hash"
    assert not doc.get("istable")
    assert not doc.get("is_submittable")
    assert doc["title_field"] == "sales_invoice"

    assert fields["sales_invoice"]["fieldtype"] == "Link"
    assert fields["sales_invoice"]["options"] == "Sales Invoice"
    assert fields["sales_invoice"]["reqd"] == 1
    assert fields["sales_invoice"]["search_index"] == 1


def test_the_states_are_the_ones_the_engine_knows():
    fields = comprobante_fields()

    assert fields["estado"]["options"].split("\n") == list(ESTADOS)
    assert fields["estado"]["default"] == Estado.BORRADOR.value
    assert fields["estado"]["read_only"] == 1


def test_the_comprobante_types_are_the_measured_ones():
    fields = comprobante_fields()

    assert fields["cbte_tipo"]["options"].split("\n") == [
        str(tipo) for tipo in TIPOS_DE_COMPROBANTE
    ]
    assert fields["cbte_tipo"].get("read_only") != 1, "el operador confirma el tipo"
    assert fields["cbte_clase"]["options"].split("\n") == sorted(
        TIPOS_DE_COMPROBANTE.values()
    )
    assert fields["cbte_clase"]["read_only"] == 1


def test_the_receiver_condition_is_the_catalog_and_has_no_default():
    fields = comprobante_fields()

    assert fields["condicion_iva_receptor"]["options"].split("\n") == [
        str(condicion.value) for condicion in CondicionIvaReceptor
    ]
    assert "default" not in fields["condicion_iva_receptor"]
    assert fields["condicion_iva_receptor"].get("read_only") != 1


def test_the_receiver_document_is_proposed_but_correctable():
    fields = comprobante_fields()

    assert fields["doc_tipo"]["options"].split("\n") == ["80", "96", "99"]
    assert "default" not in fields["doc_tipo"]
    assert fields["doc_tipo"].get("read_only") != 1
    assert fields["doc_nro"]["fieldtype"] == "Data"


def test_everything_the_engine_writes_is_read_only_in_the_form():
    fields = comprobante_fields()

    for fieldname in (
        "company",
        "customer",
        "estado",
        "cbte_clase",
        "problema",
        "environment",
        "fecha_comprobante",
        "concepto",
        "imp_neto",
        "imp_iva",
        "imp_trib",
        "imp_total",
        "alicuotas",
        "resultado",
        "cbte_nro",
        "cae",
        "cae_vence",
        "intentos",
        "solicitado_at",
        "respondido_at",
        "conciliado_at",
        "motivo_rechazo",
        "solicitud_xml",
        "respuesta_xml",
    ):
        assert fields[fieldname].get("read_only") == 1, fieldname


def test_the_amounts_table_is_the_alicuota_child():
    fields = comprobante_fields()

    assert fields["alicuotas"]["fieldtype"] == "Table"
    assert fields["alicuotas"]["options"] == "ARCA Alicuota IVA"


def test_the_comprobante_records_who_may_emit():
    roles = {row["role"]: row for row in comprobante()["permissions"]}

    assert roles["System Manager"]["write"] == 1
    assert roles["Accounts Manager"]["write"] == 1
    assert roles["Accounts User"]["read"] == 1
    assert not roles["Accounts User"].get("write")



# --- the form ---------------------------------------------------------------
#
# The document is read by an accountant, not by the engine: ARCA's codes are
# shown as words, the fields the engine writes are read-only, the technical
# detail is out of the way, and a field appears only when it has something to
# say. The stored values never change, so the engine and the documents already
# written keep working.

COMPROBANTE_JS = ROOT / "arca_comprobante" / "arca_comprobante.js"


def secciones() -> list[str]:
    return [
        field["label"]
        for field in comprobante()["fields"]
        if field["fieldtype"] == "Section Break"
    ]


def campos_de_seccion(label: str) -> list[str]:
    order = comprobante()["field_order"]
    inicio = next(
        field["fieldname"]
        for field in comprobante()["fields"]
        if field["fieldtype"] == "Section Break" and field["label"] == label
    )
    despues = order[order.index(inicio) + 1 :]
    siguiente = next(
        (name for name in despues if name.startswith("section_break_")), None
    )

    return despues if siguiente is None else despues[: despues.index(siguiente)]


def etiquetas_del_script(campo: str) -> dict[str, str]:
    script = COMPROBANTE_JS.read_text()
    bloque = re.search(rf"{campo}: \{{(.*?)\n\t+\}},", script, re.S)

    assert bloque, f"el script no explica el campo {campo}"

    return dict(re.findall(r'(\w+): __\("([^"]+)"\)', bloque.group(1)))


def test_the_form_is_grouped_into_sections_a_person_reads():
    assert secciones() == [
        "Comprobante",
        "Receptor",
        "Emisión",
        "Importes",
        "Resultado de ARCA",
        "Detalle técnico",
    ]
    assert comprobante()["field_order"][0] == "section_break_comprobante"


def test_the_sections_lose_their_boxes():
    """Six boxed groups read as six equal weights; a plain form reads in order."""
    for field in comprobante()["fields"]:
        if field["fieldtype"] == "Section Break":
            assert field.get("hide_border") == 1, field["fieldname"]


def test_the_receiver_section_holds_only_the_receiver():
    assert campos_de_seccion("Receptor") == [
        "customer",
        "condicion_iva_receptor",
        "column_break_receptor",
        "doc_tipo",
        "doc_nro",
    ]


def test_the_emission_section_holds_what_the_operator_confirms():
    assert campos_de_seccion("Emisión") == [
        "pto_venta",
        "cbte_tipo",
        "fecha_comprobante",
        "column_break_fiscal",
        "environment",
    ]


def test_the_result_section_shows_the_cae_and_the_arca_answer():
    assert campos_de_seccion("Resultado de ARCA") == [
        "resultado",
        "cbte_nro",
        "cae",
        "column_break_resultado",
        "cae_vence",
        "motivo_rechazo",
    ]
    assert comprobante_fields()["motivo_rechazo"]["label"] == "Motivo / observaciones"


def test_the_internal_fields_live_in_a_collapsible_technical_section():
    tecnica = campos_de_seccion("Detalle técnico")
    fields = comprobante_fields()

    assert fields["section_break_trazas"]["collapsible"] == 1
    assert tecnica == [
        "cbte_clase",
        "concepto",
        "intentos",
        "column_break_tecnico",
        "solicitado_at",
        "respondido_at",
        "conciliado_at",
        "solicitud_xml",
        "respuesta_xml",
        "alicuotas",
    ]

    for fieldname in tecnica:
        for label in secciones():
            if label != "Detalle técnico":
                assert fieldname not in campos_de_seccion(label), fieldname


def test_the_form_ships_no_help_text():
    """A description under every field is noise; the labels carry the meaning."""
    assert [
        field["fieldname"]
        for field in comprobante()["fields"]
        if field.get("description")
    ] == []


def test_the_form_shows_words_instead_of_arca_codes():
    fields = comprobante_fields()
    esperados = {
        "cbte_tipo": {"1", "6"},
        "condicion_iva_receptor": {str(c.value) for c in CondicionIvaReceptor},
        "doc_tipo": {"80", "96", "99"},
        "cbte_clase": set(fields["cbte_clase"]["options"].split("\n")),
        "resultado": {"A", "R"},
    }

    script = COMPROBANTE_JS.read_text()
    assert 'frm.set_df_property(campo, "options", opciones)' in script
    assert 'docfield.formatter = (value)' in script
    assert '.control-value' not in script

    for campo, codigos in esperados.items():
        etiquetas = etiquetas_del_script(campo)

        assert set(etiquetas) == codigos, campo
        assert set(etiquetas) == set(fields[campo]["options"].split("\n")), campo
        for codigo, etiqueta in etiquetas.items():
            assert etiqueta.strip(), campo
            assert etiqueta.strip() != codigo, f"{campo} sigue mostrando {codigo}"


def test_the_receiver_condition_words_are_the_measured_catalog():
    etiquetas = etiquetas_del_script("condicion_iva_receptor")

    assert etiquetas["1"] == "IVA Responsable Inscripto"
    assert etiquetas["5"] == "Consumidor Final"
    assert etiquetas["6"] == "Responsable Monotributo"
    assert etiquetas["15"] == "IVA No Alcanzado"


def test_the_form_paints_the_state_and_the_cae():
    script = COMPROBANTE_JS.read_text()

    assert "set_indicator" in script
    assert 'COLORES_DE_ESTADO' in script
    assert "frm.doc.cae" in script
    assert "frm.doc.cae_vence" in script


def test_the_proposals_stop_being_editable_once_the_attempt_starts():
    fields = comprobante_fields()

    for campo in ("pto_venta", "cbte_tipo", "condicion_iva_receptor", "doc_tipo", "doc_nro"):
        condicion = fields[campo].get("read_only_depends_on", "")

        assert "Borrador" in condicion, campo
        assert "Rechazado" in condicion, campo


def test_the_amounts_read_as_one_row_and_a_total():
    assert campos_de_seccion("Importes") == [
        "imp_neto",
        "imp_iva",
        "column_break_importes",
        "imp_trib",
        "imp_total",
    ]
    assert comprobante_fields()["imp_total"].get("bold") == 1


def test_the_alicuota_rows_are_evidence_not_a_decision():
    """The operator reads the total; the rows behind it belong to the evidence."""
    assert "alicuotas" in campos_de_seccion("Detalle técnico")
    assert comprobante_fields()["alicuotas"]["read_only"] == 1


def test_the_number_is_asked_for_without_blocking_the_save():
    """The proposal is savable before the receiver number is known.

    ``_faltantes`` in ``api`` refuses the emission without it, and the guided
    flow has to let the operator save, leave and come back. ``depends_on`` only
    hides the field: no ``reqd`` and no ``mandatory_depends_on`` may block it.
    """
    doc_nro = comprobante_fields()["doc_nro"]

    assert doc_nro["depends_on"] == (
        "eval:['80', '96'].includes(String(doc.doc_tipo || ''))"
    )
    assert not doc_nro.get("reqd")
    assert not doc_nro.get("mandatory_depends_on")


def test_the_technical_detail_opens_only_when_it_helps():
    seccion = comprobante_fields()["section_break_trazas"]

    assert seccion["collapsible"] == 1
    for estado in ("Incierto", "Solicitando", "Rechazado"):
        assert estado in seccion["collapsible_depends_on"], estado


def test_the_arca_answer_appears_only_when_arca_answered():
    fields = comprobante_fields()

    assert "Autorizado" in fields["resultado"]["depends_on"]
    assert "Rechazado" in fields["resultado"]["depends_on"]
    assert fields["cbte_nro"]["depends_on"] == "eval:doc.cbte_nro > 0"
    for campo in ("cae", "cae_vence"):
        assert "Autorizado" in fields[campo]["depends_on"], campo
    for estado in ("Incierto", "Rechazado"):
        assert estado in fields["motivo_rechazo"]["depends_on"]
    assert fields["cae"].get("bold") == 1


def test_the_technical_evidence_is_not_printed_or_copied():
    fields = comprobante_fields()

    for campo in (
        "problema",
        "concepto",
        "intentos",
        "solicitado_at",
        "respondido_at",
        "conciliado_at",
        "solicitud_xml",
        "respuesta_xml",
    ):
        assert fields[campo].get("print_hide") == 1, campo

    for campo in (
        "estado",
        "resultado",
        "cbte_nro",
        "cae",
        "cae_vence",
        "motivo_rechazo",
        "cbte_clase",
        "concepto",
        "intentos",
        "solicitado_at",
        "respondido_at",
        "conciliado_at",
        "solicitud_xml",
        "respuesta_xml",
    ):
        assert fields[campo].get("no_copy") == 1, campo


def test_the_environment_is_a_standard_filter():
    fields = comprobante_fields()

    assert fields["environment"].get("in_standard_filter") == 1
    assert fields["environment"].get("read_only") == 1


def test_the_fixed_concept_and_the_pending_problem_take_no_line():
    """``concepto`` is a constant and ``problema`` is announced above the form."""
    fields = comprobante_fields()

    assert fields["concepto"]["hidden"] == 1
    assert fields["problema"]["hidden"] == 1

