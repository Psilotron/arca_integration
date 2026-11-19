# Fixtures of `Sales Invoice` for `invoice_amounts`

## These are **provisional**

Every file here has `"_source": "provisional"`. They were written from the
worked cases in `tests/test_factura.py`, not captured from ERPNext. They
prove the derivation is internally consistent; they do **not** prove that ERPNext
stores those numbers.

A hand-written fixture tests my assumptions about ERPNext. A captured one tests
ERPNext. Before the first live emission from a mapping-derived `Importes`, every
file must be replaced by `capturar.py`, which writes `"_source": "captured"`.

`tests/test_factura.py` refuses to treat a provisional file as captured: the
label is asserted, so a fixture cannot change provenance silently.

## Cases

| file | what it pins |
|---|---|
| `caso_a.json` | one item at 21 %, the plain case |
| `caso_b.json` | 10,5 %, and the sub-cent residual |
| `caso_c.json` | two lines where the tax row rounds: 1999.99 × 21 % = 419.9979 → 420.00 |
| `caso_d.json` | 0 % is an alícuota with `importe 0`, not an exemption |
| `caso_e.json` | **refused**: global discount changes `grand_total` without changing the base |
| `caso_f.json` | item-level discount: `net_amount` already carries it |
| `caso_g.json` | **refused**: two IVA rows |

The two refusal cases are fixtures on purpose: a refusal against the wrong shape
is worse than no refusal.

## Fields kept

Only what the mapping reads, so a schema change in ERPNext shows up as a missing
field instead of passing unnoticed behind a full `as_dict()`:

`company`, `currency`, `conversion_rate`, `posting_date`, `net_total`,
`total_taxes_and_charges`, `grand_total`, `rounded_total`, `discount_amount`,
`rounding_adjustment`, `is_return`, `items[]` (`idx`, `income_account`,
`net_amount`, `item_tax_template`, `item_tax_rate`) and `taxes[]` (`charge_type`,
`account_head`, `rate`, `tax_amount`, `included_in_print_rate`, plus
`add_deduct_tax` only when the site has that field — v15 and v16 do not).

## Replacing them

The procedure, step by step, is `docs/testing.md` §4. In short:

1. On the Testing bench, `preparar.py` creates the seven **drafts** and the four
   accounts they need. Drafts are enough: ERPNext computes the totals on save, so
   no submission, no ledger entry and no stock move is required — and none is
   created.
2. `preparar()` prints, per case, ERPNext's `net_total`,
   `total_taxes_and_charges`, `grand_total` and `rounding_adjustment`. Record
   them: they are what the fixture must contain.
3. `capturar(nombres, destino="/tmp/arca-fixtures")` writes one JSON per case and
   refuses to overwrite a `"_source": "captured"` file unless asked to.
4. Copy `caso_*.json` back over the files here, update the four captured account
   names in `tests/test_factura.py`, and run
   `.venv/bin/python -m pytest -q tests/test_factura.py`. If a captured number
   disagrees with the design, the test fails: that is the point of the exercise,
   and the code or the document must be corrected before the emission.

The capture writes outside the repository by default (`/tmp/arca-fixtures`),
because `bench console` has no `__file__` and must not write into a checkout it
did not mount. It never touches the site's documents and never creates a
comprobante.
