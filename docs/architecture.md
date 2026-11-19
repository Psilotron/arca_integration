# Architecture

## 1. Layers

```
arca_integration/
├── core/                     pure Python — no `frappe` import anywhere
│   ├── errors.py             error taxonomy
│   ├── wsaa.py               login ticket, CMS signing, certificate identity
│   ├── ticket_cache.py       12-hour ticket, per service, company and environment
│   ├── padron.py             padrón envelope + parser, CUIT normalization
│   ├── client.py             lookup orchestration
│   ├── comprobante.py        importes validation, request, state machine, reconcile
│   ├── mapeo.py              account → fiscal nature
│   ├── factura.py            Sales Invoice → ARCA `Importes`
│   └── wsfe/                 hand-built SOAP: params, numbering, cae, consulta
├── api.py                    the only whitelisted module: lookup, prepare, emit, reconcile
└── arca_integration/doctype/ controllers, one per DocType
```

Two rules hold the separation: `core/` never imports `frappe`, and the Frappe
layer never contains fiscal logic. `api.py` resolves configuration, opens the
private key, builds the client and stores the outcome; every decision about
amounts, numbering, classification and state lives in `core/`. Every network
call in `core/` is injectable, so the whole suite runs offline.

## 2. DocTypes

| DocType | State | Responsibility |
|---|---|---|
| `ARCA Settings` | **built** | one document per company and environment: certificate, key, CUIT (from `Company.tax_id`), derived URLs, point-of-sale default, suggested type |
| `ARCA CUIT Query` | **built** | one immutable padrón lookup and its raw answer, optionally linked to a Customer, Supplier or Sales Invoice |
| `ARCA Alicuota IVA` | **built** | child table: one IVA detail row (`alicuota`, `porcentaje`, `base_imp`, `importe`), all read-only |
| `ARCA Mapeo de Cuenta de Ingreso` | **built** | `(company, account)` → `Gravado` / `Exento` / `No gravado` |
| `ARCA Mapeo de Cuenta de Impuesto` | **built** | `(company, account)` → `IVA` / `Percepcion` / `Otro` |
| `ARCA Comprobante` | **built** | one emission attempt per submitted invoice: the fiscal snapshot, the amounts, the operator's confirmation and ARCA's answer |
| `ARCA Bloqueo de Emision` | **built** | one row per company while an emission runs; its name is the key, so two processes cannot both take it, and `vence` frees it if the process dies |

Controllers are thin: each validates through a pure function in `core/` and
throws on failure. Frappe resolves a controller by `doctype.replace(" ", "")`,
so the class name is `ARCASettings`, `ARCACUITQuery`, `ARCAAlicuotaIVA`… — a
test checks every one.

### 2.1 ARCA Settings profiles

Each issuing company may have one `ARCA Settings` profile for Homologación and
one for Producción. Exactly one profile is the default used by operations that
do not carry an environment (new invoice proposals and padrón lookups). The
environment is copied into each comprobante; emission and reconciliation
resolve that exact profile rather than following a later change to the default.
The existing profile is kept as the default during migration, preserving the
current invoice workflow.

Service URLs are derived from the selected environment on the server. Legacy URL
columns remain hidden for compatibility, but the backend ignores their stored
values. Ticket caches are scoped by company and environment, with entries
separated by WSAA service.

The issuer VAT-condition field is retained in the DocType for compatibility
with existing records but hidden from the form. It currently has no runtime
consumer; no Company-field migration is justified until a verified fiscal rule
needs the value.

## 3. The fiscal contract

### 3.1 Settled decisions

| # | Decision | Why |
|---|---|---|
| 1 | A transport failure during an issuance is `Incierto`, never `Rechazado` | `TransportError` cannot prove "not authorized". One reconciliation is cheap; a lost comprobante number is not |
| 2 | The fiscal nature of an account is **configured, never inferred** | names are free text. An unmapped account is a refusal, not a default |
| 3 | The receiver's VAT condition is a **snapshot on the comprobante**, chosen by the operator | the fiscal document must record what was agreed, and no default or class-derived guess may fill it |
| 4 | The comprobante class is **suggested, then confirmed** by the operator | deriving it silently produces a valid CAE for the wrong document, which is worse than a rejection |
| 5 | `cbte_fch` is `Sales Invoice.posting_date`, never `today()` | a comprobante dated differently from its invoice surfaces months later |
| 6 | Concurrency is a real lock on the invoice, not a hope | two tabs must not both reach `FECAESolicitar` for the same invoice, and two comprobantes must not compete for the same next number. A company-wide lock row is enough for v1; distributed recovery is deferred (§6.3) |

### 3.2 Refused on purpose in v1

Each refuses with a Spanish message naming the invoice row, never a silent sum.

| Situation | Why |
|---|---|
| income account with no mapping | the fiscal nature is unknown |
| tax account with no mapping | the IVA breakdown cannot be built |
| an `Exento` or `No gravado` line | `ImpOpEx` / `ImpTotConc` are not modeled |
| any percepción (IIBB, IVA) | `Tributos` is not modeled |
| currency other than the company's | `MonId` / `MonCotiz` beyond pesos are not modeled |
| receiver condition ARCA does not publish | would come back as `10242` |
| receiver condition invalid for the class | would come back as `10243` |
| a customer `Tax ID` that is not a CUIT, while only `80` and `99` are modelled | sending it as a CUIT would be refused by ARCA; dropping it would turn an identified receiver into an anonymous one |
| two active mappings for one account | the emission would depend on the order the rows came back in |
| more than one IVA rate per comprobante | single-row v1 |
| credit / debit notes | needs `CbtesAsoc` and a different `cbte_tipo` |
| service invoices (`Concepto` 2/3) | needs `FchServDesde`, `FchServHasta`, `FchVtoPago` |
| a `cbte_tipo` outside `{1, 6}` | only two classes were measured |
| `discount_amount != 0`, `rounding_adjustment != 0` | they break the component identity |

### 3.3 The two self-checks

`validate_amounts` refuses before any request when:

- the alícuota bases do not add up to the net amount;
- `|base × rate/100 − importe| > 0.01` for any row (ARCA's own `10048`
  tolerance: relative ≤ 0.01 % or absolute ≤ 0.01).

## 4. What ARCA answered, measured

These are not assumptions; each was measured against homologación on 2026-11-12
and is pinned by a test.

| Fact | Value |
|---|---|
| HTTP 200 does not mean authorized | the verdict is `Resultado` = `A` / `R` / `P` |
| `Resultado: P` | not approval, not rejection: `WsfeUncertain`, reconcile |
| re-sending an authorized number | `10016` — `Reproceso` is not an idempotency lever |
| importes tolerance | `10048`, relative ≤ 0.01 % or absolute ≤ 0.01 |
| rejection sources | header in `Errors.Err`, detail in `Observaciones.Obs` |
| no matching `FECompConsultar` record at query time | `Err 602`; it does not prove a recent authorization will not appear later |
| `FEParamGetPtosVenta` in homologación | `602` even when issuing works — not a health check |
| envelope | every element needs the `ar:` prefix; unqualified children are silently zeroed |
| alícuotas | `3=0 %`, `4=10,5 %`, `5=21 %`, `6=27 %`, `8=5 %`, `9=2,5 %` |
| `CantReg` / `Concepto` | always `1` in v1 |
| `Cmp_Clase` | never used as automatic validation; it contradicts a measured authorization |

## 5. The emission path

### 5.1 What makes an emission safe

Six properties, each pinned by an offline test.

1. **The engine owns `estado`.** `TRANSICIONES` in `core/comprobante.py` lists
   every allowed move and `exigir_transicion` refuses anything else; `api.py`
   calls it before every state write. The controller makes the document
   read-only in `Solicitando`, `Autorizado` and `Incierto`, and no `validate`
   writes the state.
2. **One emission or reconciliation at a time per company, held by a row.**
   Both actions insert `ARCA Bloqueo de Emision` with `emision:<company>` as its
   name before rereading or changing the stored comprobante state, and delete it
   in a `finally`. A second process cannot create the same name, so it is refused
   with the expiry of the live lock. A `select … for update` cannot do this job:
   the row lock dies with the `commit` that stores the assigned number, and when
   no other comprobante exists there is no row to lock at all. The lock is
   deliberately coarser than "environment + point of sale + type": profiles
   have environment-specific defaults and vouchers can override the point of
   sale, but one company-wide lock also excludes concurrent attempts across
   environments. An expired lock (`vence`, 300 s)
   is reclaimed by one conditional `update` and read back, so a killed process
   cannot block the company forever. Because the TTL cannot cover an arbitrarily
   slow answer, the holder re-reads its own token right before sending: a
   process that outlived its lock goes back to `Borrador` without consuming a
   number.
3. **One live comprobante per invoice.** `before_insert` refuses a second
   document for the same invoice, and `emitir_comprobante` repeats the check
   while holding the lock. The second check is the one that decides: the first
   one ran before the lock existed.
4. **The number is durable before the request leaves.** Once the WSAA ticket is
   warm (so an authentication failure is provably pre-send), the attempt is
   frozen in `Solicitando` and the number ARCA reports is written and committed
   **before** `FECAESolicitar`. A failure before that write — the ticket, the
   numbering read, the request validation — returns the document to `Borrador`
   and uncounts the attempt, because no number was consumed. A failure after it
   is `Incierto`, never `Borrador`: nothing in the process can prove the request
   did not arrive. A frozen attempt without a stored number was therefore never
   sent, and Conciliar returns it to `Borrador` without asking ARCA.
5. **Evidence is immutable.** `resultado`, `cae`, `cae_vence`, `cbte_nro`,
   `intentos`, the timestamps, `motivo_rechazo` and the two traces are
   read-only fields written only through `frappe.db.set_value`; `on_trash`
   refuses a document with `intentos > 0`; and `Autorizado` has no outgoing
   transition, so no code path can re-send it.
6. **Nothing is emitted automatically.** No emission on save or on submit, no
   retry. The operator presses Emitir once. A transport failure or an answer
   that cannot be classified leaves `Incierto`, and only Conciliar can move it.
   A narrow `Sales Invoice.on_trash` guard prevents deleting an invoice after an
   ARCA attempt, preserving the link to its fiscal evidence.

The environment is a snapshot the operator reviews. A new proposal uses the
company's default profile; an existing proposal is refreshed against the profile
for its saved environment. Emission and reconciliation also resolve that exact
company/environment profile, so changing the default never moves a saved
voucher or redirects an attempt. If the required profile is missing, the
operation stops before contacting ARCA.

Deferred by §6.3: distributed recovery, multi-site coordination and high-volume
concurrency. The lock covers one site and one company, which is the first
release's case.

### 5.2 `ARCA Comprobante`, in one paragraph

Not submittable, `autoname: hash`, found from `Sales Invoice` through
`sales_invoice` (indexed; no reverse field, that would be a core change). It
carries the fiscal snapshot (`environment`, `fecha_comprobante`, `pto_venta`,
`cbte_tipo`, `cbte_clase`, `condicion_iva_receptor`, `doc_tipo`, `doc_nro`,
`concepto`), the read-only `imp_*` built by `invoice_amounts`, the child
`alicuotas`, the engine-written result (`resultado`, `cae`, `cae_vence`,
`cbte_nro`, `intentos`, timestamps, `motivo_rechazo`), the two traces
`solicitud_xml` / `respuesta_xml`, and `problema`: what still stops an emission.

`cbte_nro` is deliberately not required, because between creation and
`FECompUltimoAutorizado` there is no number. There is no unique index either:
every new document would carry the same empty value and collide. Duplicates are
prevented by the emission lock and the live-comprobante check in §5.1, not by a
constraint.

It must never: emit from a `doc_events` hook on `Sales Invoice`, retry
automatically, write `estado` / `cae` / `cbte_nro` from `validate`, delete
anything with `intentos > 0`, or accept a `cbte_tipo` whose class was not
measured.

### 5.3 The three endpoints

All of them live in `api.py`; none of them decides a fiscal rule.

| Endpoint | What it does | What it never does |
|---|---|---|
| `preparar_comprobante(sales_invoice)` | creates or refreshes the proposal: snapshot, receiver document from the customer's `Tax ID`, amounts and IVA rows, and `problema` with what is missing | send anything. A refusal from `core/` is stored on the document so the operator can open it and fix the mapping |
| `emitir_comprobante(name)` | takes the company lock, reads the stored state again, refuses a changed environment, re-derives the amounts from the invoice, freezes the attempt, stores the number before sending, and records `Autorizado` / `Rechazado` / `Incierto` with the exchanged bytes | emit twice for one invoice, emit an `Autorizado`, an `Incierto` or a `Solicitando`, emit into an environment the proposal was not reviewed for, or retry after a failure |
| `conciliar_comprobante(name)` | takes the company lock, rereads the state, asks `FECompConsultar` whether that number exists and settles only a matching authorization; 602 stays `Incierto`; a frozen attempt without a number returns to `Borrador` without a request, because it was never sent | send, renumber, emit, reconcile while an operation is active, or ask an environment other than the one the comprobante was prepared for |

Amounts are re-derived on every attempt instead of trusted from `imp_*`: a
mapping the operator corrected has to be the one that travels.

### 5.4 Build order

| # | Task | State |
|---|---|---|
| B0 | company/environment `ARCA Settings` profiles | **done** |
| B1 | `core/comprobante.py` validation and state machine | **done** |
| B2 | `ARCA Alicuota IVA` + the two mapping DocTypes | **done** |
| B2c | `invoice_amounts` + fixtures | **done** |
| B3 | `ARCA Comprobante` DocType | **done** |
| B4 | `emitir_comprobante` endpoint | **done** |
| B5 | reconciliation (`FECompConsultar`) | **done** |
| B6 | button, status colours, read-only rules | **done** |
| B7 | manual acceptance run in homologación | not built |
| B8 | emission lock | **done** |
| B9 | printed QR | not built, deferred by §6.3 |

B1 came before B2 on purpose: everything a test can catch lives in `core/`,
which has no Frappe import. The DocType is then a shell.

## 6. Product scope and operator experience

### 6.1 First usable release

The first release should work for any ERPNext installation with a single
issuing company, a low volume of invoices and an operator who can review each
result. The goal is a dependable, understandable path from a submitted ERPNext
Sales Invoice to an ARCA-authorized comprobante, with enough information to
resolve an uncertain answer safely. Keep the implementation small and
maintainable; do not build high-volume concurrency controls, distributed
recovery or advanced multi-user audit workflows without a demonstrated need.
The minimum integrity rules in sections 3 and 5 still apply.

The first usable release should let the operator:

1. Configure one ARCA profile per company and environment in use, including
   credentials and an optional point-of-sale default; choose one profile as the
   company's default for new proposals and padrón lookups.
2. Map ERPNext income and tax accounts to fiscal concepts, with the company
   and account visible in each mapping and duplicate or missing mappings
   prevented or clearly reported.
3. Review a proposed comprobante from a submitted Sales Invoice, including
   document class, receiver VAT condition, date, amounts and IVA breakdown.
4. See which invoice details or mappings need attention before sending.
5. Explicitly start an emission and see its outcome, CAE and expiry when
   authorized, or a clear next step when rejected or uncertain.
6. Reconcile an uncertain result before any further emission attempt.

No invoice should be emitted automatically by saving or submitting a Sales
Invoice. The operator remains in control of confirming the fiscal document
class and starting the emission.

### 6.2 Usability requirements

- Use Spanish labels and actionable Spanish validation messages in the ERPNext
  interface. Name the invoice, account or field that needs correction and
  explain what the operator should do next.
- Make configuration and mappings discoverable from the issuing company and
  the invoice workflow. Filter account choices to the relevant company, show
  the mapped fiscal nature in plain terms, and make missing mappings easy to
  find.
- Show a pre-emission summary of the fiscal data and calculated amounts.
  Explain refused cases before the operator reaches the send action where
  feasible; do not silently omit, default or reinterpret amounts.
- Make each emission state legible: draft, requesting, authorized, rejected
  or uncertain. For uncertain outcomes, state that ARCA may have received the
  request and direct the operator to reconciliation; never suggest an
  immediate retry.
- Keep CAE, number, ARCA response and other fiscal evidence visible and
  read-only after authorization. Present the QR and printable comprobante as
  part of the operator workflow before considering the first release
  complete.
- Prefer a small number of clear actions and screens over generic settings
  machinery. Do not add background automation that can surprise the operator.

### 6.3 Explicitly deferred

Until real usage justifies them, defer high-volume concurrency mechanisms,
automatic retries, distributed recovery, advanced multi-user audit, and
generalized onboarding for many adopting companies. Do not defer basic
protection against duplicate emission, durable emission state, safe handling
of uncertain responses, reconciliation, or the operator-visible fiscal
record. These are required for fiscal correctness even at low volume.

