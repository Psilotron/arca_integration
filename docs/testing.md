# Testing

## 1. Offline suite

No bench, no site, no network:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install cryptography requests pytest
.venv/bin/python -m pytest -q
```

Expected: `351 passed, 13 skipped`. `pytest` finds the package through
`pythonpath = ["."]` in `pyproject.toml`, so no `pip install -e` is needed.

What it covers: the WSAA/WSFE envelopes and parsers, the alícuota catalog, the
state machine and reconciliation, the account and tax mapping,
`invoice_amounts` and its seven worked cases, every refusal, the DocType
schemas and controller names, the three emission endpoints and the comprobante
controller against a stub `frappe` — including the emission lock, the durable
number and the environment guard — and the two bench scripts.

## 2. Live tests

The 13 skips are the live tests. They need a certificate and reach ARCA
homologación:

```bash
ARCA_LIVE=1 ARCA_CUIT=<cuit of the certificate> \
  .venv/bin/python -m pytest tests/test_live_homologacion.py -q

ARCA_LIVE=1 ARCA_CUIT=<cuit of the certificate> \
  .venv/bin/python -m pytest tests/test_live_wsfe.py -q
```

**`ARCA_CUIT` is required, with no default.** It must be the CUIT the
certificate was issued to: homologación rejects a header whose CUIT does not
match the signer, and the WSAA client now checks that identity before signing.

| Variable | Default | Meaning |
|---|---|---|
| `ARCA_LIVE` | — | `1` enables any network call at all |
| `ARCA_LIVE_EMITIR` | — | `1` additionally allows minting a CAE |
| `ARCA_CUIT` | **required** | CUIT of the certificate |
| `ARCA_CERT` | `tmp/erp.crt` | certificate path |
| `ARCA_KEY` | `tmp/erp.pem` | private key path |
| `ARCA_WSAA_URL` | homologación WSAA | endpoint override |
| `ARCA_PADRON_URL` | homologación padrón | endpoint override |
| `ARCA_WSFE_URL` | homologación WSFE | endpoint override |
| `ARCA_TA_CACHE` | `/tmp/arca-ta.json` | lookup ticket cache |
| `ARCA_WSFE_TA_CACHE` | `/tmp/arca-wsfe-ta.json` | fiscal ticket cache |

**Issuing a comprobante is irreversible.** `ARCA_LIVE=1` only means the suite
may reach the network; it never means it may mint a comprobante. That needs
`ARCA_LIVE_EMITIR=1` on top, and an explicit operator decision.

## 3. Fixtures

Two kinds of file live in `tests/fixtures/`, and the difference matters:

- **Captured** — a verbatim answer from ARCA's homologación services or its
  padrón. These are the real rejection codes and shapes.
- **Constructed** — written by hand because no such answer exists yet. Each one
  says `CONSTRUCTED` in its own header comment. A constructed fixture proves the
  parser handles a shape; it never proves ARCA sends that shape.

`tests/fixtures/README.md` lists which is which and records the one
sanitization applied before publication: the issuer CUIT in the captured
response headers was replaced by the placeholder `20000000001`. Codes, messages,
dates, points of sale and CAE values are as received.

`tests/fixtures/sales_invoice/caso_*.json` are a third kind: **provisional**.
They encode the design's arithmetic, not captured ERPNext output, and every one
carries `"_source": "provisional"`.

## 4. Replacing the provisional fixtures

Two scripts in `tests/fixtures/sales_invoice/` replace the provisional files
with captured ones. They use only `frappe` and the standard library, so they run
**without installing this app**, on whatever host runs the bench. Nothing below
depends on how that host is provisioned.

Both run from `bench --site <site> console`, which defines no `__file__`: every
path is explicit.

1. **Pick a site** with a company, a customer, an item and the accounts the
   seven cases name.
2. **Get the two scripts onto the bench host** by whatever transport you use
   (`scp`, a shared mount, a checkout).
3. **Discover what exists, then create the seven drafts.** They carry a fixed
   `posting_date`; nothing is submitted, so no ledger entry and no stock move is
   created.

   ```python
   exec(open("/path/to/preparar.py").read())
   descubrir()
   nombres = preparar(
       company="…", customer="…", item_code="…",
       ingreso="…", iva_21="…", iva_105="…", iva_0="…",
   )
   ```

   The account arguments are `account_name` values, not full names: ERPNext
   names an account `<account_name> - <company abbr>`, and the script resolves
   that name and creates the account only when it is missing.
4. **Capture.** `preparar()` prints the exact `capturar(...)` call to run next:

   ```python
   exec(open("/path/to/capturar.py").read())
   capturar(nombres, destino="/tmp/arca-fixtures")
   ```

   `destino` defaults to the `ARCA_FIXTURE_DIR` variable, or
   `/tmp/arca-fixtures`. Write outside the repository: the console must not
   write into a checkout it did not mount.
5. **Bring the JSON back** over the provisional files and diff them. The numbers
   must match, or the design was wrong — not the fixture.
6. **Update the account names** in `tests/test_factura.py`, then re-run
   `.venv/bin/python -m pytest -q`.

`capturar.py` records only the fields `invoice_amounts` reads, marks the file
`"_source": "captured"`, refuses to overwrite an already captured file unless
told to, and keeps `add_deduct_tax` only when the site has that field. It never
touches the site's documents and never creates a comprobante.

## 5. What the offline suite cannot prove

- that ERPNext stores the numbers the provisional fixtures assume;
- the form layout, the grid, the unique patch and the permission behaviour of
  the DocTypes;
- that `validate()` runs at all inside Frappe for the child table and the two
  mappings;
- anything about ARCA's live behaviour beyond what the captured fixtures pin.
