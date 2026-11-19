"""The two fiscal mapping tables, independent of Frappe.

The mapping is configuration, but it decides what a comprobante says. Keeping it
as a plain value object with pure lookups means the refusals an operator will read
are testable without a site, a database or a bench.

Two tables, both keyed by ``(company, account)``:

- income accounts say whether revenue is taxable;
- tax accounts say what a taxes row is.

Neither stores the ARCA alícuota id. It is derived from the tax row's own rate
against the measured catalog, so the stored configuration cannot disagree with the
row it describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .comprobante import ComprobanteError

#: The Select options of ``ARCA Mapeo de Cuenta de Ingreso``.
INCOME_NATURES: tuple[str, ...] = ("Gravado", "Exento", "No gravado")

#: The Select options of ``ARCA Mapeo de Cuenta de Impuesto``.
TAX_NATURES: tuple[str, ...] = ("IVA", "Percepcion", "Otro")

_INCOME_DOCTYPE = "ARCA Mapeo de Cuenta de Ingreso"
_TAX_DOCTYPE = "ARCA Mapeo de Cuenta de Impuesto"


@dataclass(frozen=True)
class AccountMapping:
    """A snapshot of both tables, ready to be passed to the pure layer."""

    income_accounts: Mapping[tuple[str, str], str] = field(default_factory=dict)
    tax_accounts: Mapping[tuple[str, str], str] = field(default_factory=dict)

    @classmethod
    def from_rows(
        cls,
        *,
        income_rows: Iterable[Mapping[str, object]] = (),
        tax_rows: Iterable[Mapping[str, object]] = (),
    ) -> "AccountMapping":
        """Build the lookup from DocType rows.

        Inactive rows are dropped on purpose: ``activo`` is how an operator
        retires a mapping without deleting the record of what it used to say, and
        a retired row must never participate in an emission.
        """
        return cls(
            income_accounts=_index_rows(
                income_rows, INCOME_NATURES, _INCOME_DOCTYPE
            ),
            tax_accounts=_index_rows(tax_rows, TAX_NATURES, _TAX_DOCTYPE),
        )


def _index_rows(
    rows: Iterable[Mapping[str, object]],
    allowed_natures: tuple[str, ...],
    doctype: str,
) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}

    for row in rows:
        if not _is_active(row):
            continue

        nature = str(row.get("naturaleza") or "")
        if nature not in allowed_natures:
            raise ComprobanteError(
                f"La naturaleza {nature!r} no existe en {doctype}. "
                f"Las opciones son: {', '.join(allowed_natures)}."
            )

        company = str(row.get("company") or "")
        account = str(row.get("account") or "")
        if not company or not account:
            raise ComprobanteError(
                f"Una fila de {doctype} no tiene compañía o cuenta y no se puede usar."
            )

        key = (company, account)
        if key in index:
            raise ComprobanteError(
                f"La cuenta {account!r} de la compañía {company!r} tiene más de un "
                f"mapeo activo en {doctype}. Dejá uno solo activo: dos naturalezas "
                "para la misma cuenta harían que la emisión dependa del orden de "
                "las filas."
            )

        index[key] = nature

    return index


def _is_active(row: Mapping[str, object]) -> bool:
    """``activo`` absent means active, which is the DocType's default."""
    return bool(row.get("activo", 1))


def income_nature_for(mapping: AccountMapping, company: str, account: str) -> str:
    """What the income account is configured as, or a refusal naming it."""
    try:
        return mapping.income_accounts[(company, account)]
    except KeyError:
        raise ComprobanteError(
            f"La cuenta de ingreso {account!r} de la compañía {company!r} no está "
            "mapeada, o su mapeo está inactivo. Configurala en "
            f"{_INCOME_DOCTYPE}."
        )


def tax_nature_for(mapping: AccountMapping, company: str, account: str) -> str:
    """What the tax account is configured as, or a refusal naming it."""
    try:
        return mapping.tax_accounts[(company, account)]
    except KeyError:
        raise ComprobanteError(
            f"La cuenta de impuesto {account!r} de la compañía {company!r} no está "
            "mapeada, o su mapeo está inactivo. Configurala en "
            f"{_TAX_DOCTYPE}."
        )


def validate_income_mapping(
    account: str, nature: str, root_type: str | None
) -> None:
    """Check one ``ARCA Mapeo de Cuenta de Ingreso`` row before it is saved.

    ``root_type`` is the account's own ERPNext type, read by the controller. An
    expense account mapped as revenue would post a comprobante whose net is not
    revenue at all, so it is refused at configuration time rather than at
    emission time.
    """
    if nature not in INCOME_NATURES:
        raise ComprobanteError(
            f"La naturaleza {nature!r} no existe en {_INCOME_DOCTYPE}. "
            f"Las opciones son: {', '.join(INCOME_NATURES)}."
        )

    if root_type != "Income":
        raise ComprobanteError(
            f"La cuenta {account!r} es de tipo {root_type!r} y el mapeo de ingresos "
            "solo admite cuentas de ingreso (Income)."
        )


def validate_tax_mapping(account: str, nature: str) -> None:
    """Check one ``ARCA Mapeo de Cuenta de Impuesto`` row before it is saved.

    There is no ``root_type`` check here, and that is deliberate: an IVA account
    is a liability, a perception also is, and ``Otro`` can be either. The nature
    is what the engine reads, and it is the only thing the table promises.
    """
    if nature not in TAX_NATURES:
        raise ComprobanteError(
            f"La naturaleza {nature!r} no existe en {_TAX_DOCTYPE}. "
            f"Las opciones son: {', '.join(TAX_NATURES)}."
        )
