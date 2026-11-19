# arca_integration

A Frappe/ERPNext app for the web services of **ARCA** (formerly AFIP),
Argentina's tax authority: taxpayer registry lookups and electronic invoicing.

## Objective

Bring ARCA's electronic invoicing into ERPNext without touching ERPNext
itself. The app adds its own DocTypes and settings, keeps the fiscal logic in a
standalone Python package, and reads everything it needs from your existing
company and chart of accounts.

**Main features**

- Look up a taxpayer's tax status (*constancia de inscripción*) from ARCA's
  registry, from Customer, Supplier or Sales Invoice with an explicit review
  before applying the taxpayer's registered name.
- Per-company ARCA configuration: environment, certificate, private key,
  endpoints and point of sale.
- A fiscal engine that validates an invoice's amounts before anything is sent
  to ARCA, and records what ARCA answered.
- Account mapping: the fiscal nature of each account (taxable, exempt, VAT,
  perception) is configured once per company, never inferred from its name.
- Homologación and production environments, selected explicitly per company.
- A comprobante proposal prepared from a submitted Sales Invoice, reviewed and
  confirmed by the operator, and emitted by hand — nothing is sent on save or
  on submit, and nothing is retried automatically.
- Reconciliation of an uncertain answer by asking ARCA whether that number
  exists, instead of guessing and issuing twice.

## Status

**Not ready for production.**

Everything runs against ARCA's *homologación* environment, where documents have
no fiscal validity.

What works today:

- taxpayer lookup, exercised against homologación;
- one comprobante per submitted invoice: prepared, reviewed, confirmed and
  emitted on demand, with its CAE, its number and the exact bytes exchanged;
- the fiscal engine: amount validation, request building, reconciliation of an
  uncertain result;
- the account and tax mapping, and the IVA breakdown of an invoice;
- one emission at a time per company, with the assigned number stored before the
  request leaves and the environment checked again at every attempt;
- seven DocTypes, including the per-company settings, the comprobante itself and
  the emission lock.

Still missing before you can invoice for real: the printed QR, and the fiscal
cases version 1 deliberately refuses (exemptions, perceptions, credit notes,
multiple tax rates, foreign currency).

[docs/architecture.md](docs/architecture.md) has the details and the build
order.

## Compatibility and requirements

| Component | Requirement |
|---|---|
| Frappe | v15 – v16 |
| ERPNext | v16 (verified). v15 is handled field by field but was not tested on a site |
| Python | 3.10 or later |
| Extra dependencies | none — `cryptography` and `requests` ship with Frappe |

## Installation

```bash
bench get-app https://github.com/Psilotron/arca_integration.git
bench --site <site> install-app arca_integration
bench restart
```

Then create one **ARCA Settings** document per emitting company.

Installing the app creates no company, assigns no CUIT and loads no
credentials. Never commit the certificate or the private key: `.gitignore`
already excludes `tmp/`, `*.crt`, `*.key`, `*.pem` and `*.p12`.

Step by step: [docs/installation.md](docs/installation.md) ·
[docs/configuration.md](docs/configuration.md).

## Documentation

| Document | What it covers |
|---|---|
| [Installation](docs/installation.md) | install the app and verify it |
| [Configuration](docs/configuration.md) | companies, CUIT, certificates, environments |
| [Architecture](docs/architecture.md) | components, fiscal rules, current scope |
| [Testing](docs/testing.md) | running the tests and the live suites |

## Tests

From a clean clone. Offline: no bench, no site, no network.

```bash
git clone https://github.com/Psilotron/arca_integration.git
cd arca_integration
python3 -m venv .venv
.venv/bin/python -m pip install cryptography requests pytest
.venv/bin/python -m pytest -q
```

Result: `351 passed, 13 skipped`. The skips are the live tests, which need a
certificate and reach homologación. See [docs/testing.md](docs/testing.md).

**Issuing a document is irreversible.** The live suite never issues one unless
`ARCA_LIVE_EMITIR=1` is set in addition to `ARCA_LIVE=1`.

## License

MIT — see [LICENSE](LICENSE). `arca_integration/hooks.py` declares the same, so
the app list in a bench shows it correctly.
