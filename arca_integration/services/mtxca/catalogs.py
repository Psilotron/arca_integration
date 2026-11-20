"""Pure helpers to turn MTXCA catalog answers into what the app stores."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .client import CodeDescription

# Voucher types that WSMTXCA lists only for use as associated vouchers (e.g. tobacco
# delivery notes). They cannot be authorized, so no receiver conditions apply to them.
ASSOCIATED_ONLY_MARKER = "sólo para comprobantes asociados"

_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


@dataclass(frozen=True)
class ReceiverCondition:
	code: str
	description: str
	voucher_types: tuple[str, ...]


def vat_rate(description: str) -> float | None:
	"""'10.5%' -> 10.5; 'Exento' -> None."""
	match = _RATE_RE.search(description or "")
	return float(match.group(1).replace(",", ".")) if match else None


def is_issuable(voucher_type: CodeDescription) -> bool:
	return ASSOCIATED_ONLY_MARKER not in " ".join(voucher_type.description.lower().split())


def group_receiver_conditions(
	by_voucher_type: Mapping[str, Iterable[CodeDescription]],
) -> list[ReceiverCondition]:
	"""Invert {voucher type: [conditions]} into one entry per condition with its voucher types."""
	descriptions: dict[str, str] = {}
	voucher_types: dict[str, list[str]] = {}
	for voucher_type, conditions in by_voucher_type.items():
		for condition in conditions:
			descriptions[condition.code] = condition.description
			voucher_types.setdefault(condition.code, []).append(voucher_type)

	return [
		ReceiverCondition(code, descriptions[code], tuple(sorted(voucher_types[code], key=int)))
		for code in sorted(descriptions, key=int)
	]
