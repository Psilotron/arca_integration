"""The receiver VAT conditions, as ARCA numbers them.

The names come from ``FEParamGetCondicionIvaReceptor``, measured against
homologación on 2026-11-12, not from memory. ARCA also answers ``Cmp_Clase``
with the comprobante classes each condition belongs to, and that field
contradicts what we already proved live: it puts ``Consumidor Final`` (5) in
``C/49``, yet a Factura B with condition 5 was authorized. The measured
authorization wins, and ``Cmp_Clase`` is not used to refuse anything until that
disagreement is understood.
"""

from arca_integration.core.comprobante import CONDICIONES_VERIFICADAS
from arca_integration.core.wsfe.types import CondicionIvaReceptor


def test_the_members_arca_reports_are_all_present():
    esperado = {
        1: "IVA Responsable Inscripto",
        4: "IVA Sujeto Exento",
        5: "Consumidor Final",
        6: "Responsable Monotributo",
        7: "Sujeto No Categorizado",
        8: "Proveedor del Exterior",
        9: "Cliente del Exterior",
        10: "IVA Liberado – Ley N° 19.640",
        13: "Monotributista Social",
        15: "IVA No Alcanzado",
        16: "Monotributo Trabajador Independiente Promovido",
    }

    assert {int(condicion) for condicion in CondicionIvaReceptor} == set(esperado)


def test_the_enum_is_an_int_enum_so_arca_reads_it_directly():
    assert int(CondicionIvaReceptor.CONSUMIDOR_FINAL) == 5
    assert isinstance(CondicionIvaReceptor.CONSUMIDOR_FINAL, int)


def test_every_condition_the_class_table_allows_exists_in_the_enum():
    """Holding a mapping for a value that cannot be built is a latent crash."""
    valores = {int(condicion) for condicion in CondicionIvaReceptor}

    for clase, permitidas in CONDICIONES_VERIFICADAS.items():
        faltantes = permitidas - valores

        assert not faltantes, f"clase {clase} permite condiciones ausentes: {faltantes}"


def test_a_condition_arca_accepts_for_class_b_is_no_longer_a_dead_end():
    """These used to raise ``ValueError`` because the enum stopped at four."""
    for valor in (7, 8, 9, 10, 15):
        assert CondicionIvaReceptor(valor).value == valor


def test_an_unknown_condition_still_fails_loudly():
    try:
        CondicionIvaReceptor(99)
    except ValueError:
        pass
    else:
        raise AssertionError("una condición inexistente no puede construirse")
