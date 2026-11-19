# Configuration

ARCA configuration is isolated by company and environment. Each company can
have one profile for Homologación and one for Producción. Mark exactly one
profile as the company's default: new comprobante proposals use it when they
do not yet have a saved environment. CUIT queries select their environment per
query. Existing comprobantes keep their environment snapshot for emission and
reconciliation.

## 1. `ARCA Settings`: one profile per company and environment

Create a profile for each environment the company will use. A company can keep
Homologación and Producción configured at the same time. Choose the environment
explicitly and decide separately which profile should be the default.

| Field | Value |
|---|---|
| Empresa | the issuing ERPNext company |
| Ambiente | `Homologación` or `Producción`, chosen explicitly |
| Certificado | attach `erp.crt` |
| Clave privada | set or replace it in **Nueva clave privada (PEM)** below |
| CUIT consultante | filled from the company's `Tax ID`, **read-only** |
| Ambiente predeterminado | choose one profile per company for operations without a saved environment |
| Punto de venta predeterminado | optional default for this company and environment; can be overridden on each voucher |
| Tipo de comprobante sugerido | optional; `1` (Factura A) or `6` (Factura B). The operator can change it on each voucher |
| Condición IVA del emisor | legacy field retained for compatibility; hidden and unused |

On a saved profile, use **Consultar puntos de venta** to retrieve the WSFE
catalog. The selector shows the emission mode, blocked state, and any reported
date of deregistration; only an unblocked CAE point without a deregistration
date can be selected. A catalog error does not change the saved value. In
particular, homologación may answer 602 even when a point can still be used, so
that response is not proof that the configured point is invalid.

Changing the company's default profile affects only new proposals and padrón
lookups. A CUIT query uses its selected environment and never falls back to the
default profile if its matching configuration is missing. Changing the default
does not change the environment or saved point of sale/type of an existing
voucher or CUIT query.

The private key is pasted into a **multiline** field, because a PEM has line
breaks and a password input cannot hold them. On save the controller moves it
into the hidden `Password` storage field and empties the paste. From there
Frappe takes over: the value is encrypted into the `__Auth` table, and this
DocType's own column keeps only a mask of the same length, so the plaintext is
never written to the row and never sent back to the browser. An empty paste
leaves the stored key unchanged; paste a replacement key to rotate it.

## 2. The issuer CUIT comes from the company

The CUIT is **not typed**. On save the controller reads the company's **Tax ID**
field in ERPNext, normalizes it, and stores it in `cuit_representada`. An empty
or malformed `Tax ID` refuses the save with a message naming the company.

Consequences:

- the company must have a valid CUIT in `Tax ID` before ARCA can be used;
- there is no site-wide CUIT that could leak into another company's documents;
- a test or fixture CUIT can never become the configured one by accident.

## 3. The certificate must belong to that CUIT

Before anything reaches ARCA, `validate_certificate_cuit` compares the CUIT declared in
the certificate subject (`SERIALNUMBER=CUIT …`, separators tolerated) with the
configured one. A mismatch refuses the save:

```
El CUIT del certificado no corresponde al CUIT configurado.
(certificado=… configurado=…)
```

A certificate that declares no CUIT at all is refused too. The same check runs
inside `sign_cms`, so a client constructed with a mismatched pair fails before
signing.

## 4. Environment and service endpoints

The selected environment determines the WSAA, Padrón and WSFE endpoints inside
the backend. These are implementation details, not Settings fields. Legacy URL
columns remain hidden for compatibility, but their stored values are ignored.

| Ambiente | Internal endpoint set |
|---|---|
| `Homologación` | `wsaahomo.afip.gov.ar`, `awshomo.afip.gov.ar`, and `wswhomo.afip.gov.ar` |
| `Producción` | `wsaa.afip.gov.ar`, `aws.afip.gov.ar`, and `servicios1.afip.gov.ar` |
| anything else | refused: `Elegí el ambiente de ARCA: Homologación o Producción.` |

## 5. Ticket cache

The WSAA ticket lasts 12 hours and ARCA refuses a second login while one is
valid (`coe.alreadyAuthenticated`). The app persists it in

```
<site>/private/files/arca_ta_<hash>.json
```

where `<hash>` identifies the company and environment. Each file keeps separate
entries for the WSAA services. Do not delete that file while the ticket is
alive, and do not run two sites with the same certificate expecting independent
logins: the second one may be locked out until the ticket expires.

The fiscal service uses a **separate** ticket. One certificate, two WSAA slots,
never shared.

## 6. WSFE specifics

- `FEParamGetPtosVenta` answers `602` ("Sin Resultados") in homologación even
  when issuing from point of sale 1 works. **Never read that catalog as a
  health check**; only `FECAESolicitar` settles whether a point of sale is
  enabled (`10005` when it is not).
- WSFE answers **HTTP 200 even when it rejects**. The verdict is the body.
- `Resultado: P` means ARCA could not settle the request: the comprobante may
  exist. Reconcile with `FECompConsultar` before asking again.

## 7. Emitting a comprobante

Nothing is emitted by saving or submitting a Sales Invoice. The emission is a
separate document, prepared on demand and sent by hand.

### 7.1 Prepare

Open a **submitted** Sales Invoice and press **Comprobante ARCA**. The app
creates one `ARCA Comprobante` for that invoice and opens it. Pressing the
button again refreshes the same document; it never creates a second one.

The proposal carries:

| Field | Where it comes from |
|---|---|
| Ambiente, Fecha del comprobante | the company's default profile for a new voucher (or the existing voucher's saved environment), and the invoice's posting date |
| Punto de venta, Tipo de comprobante | suggested from that environment's `ARCA Settings`; confirm or correct them here |
| Condición frente al IVA | **chosen by you**. There is no default and it is required to emit |
| Tipo y número de documento | from the customer's `Tax ID`: a CUIT becomes `CUIT`, an empty one becomes `Sin identificar` with `0`. The number field is hidden when nothing can be typed |
| Importes | derived from the invoice through the account mappings, read-only |
| Alícuota detail | the rows behind the amounts, read-only, in **Detalle técnico** |

The form reads in words. The document keeps ARCA's codes, and the form shows
what each one means without rewriting it, so the engine and the documents
already written keep reading the same fields.

The class (`A` / `B`) follows the confirmed type, cannot be typed, and lives in
**Detalle técnico** together with the attempt counters and the exchanged XML. No
`Custom Field` is added to `Sales Invoice`, and the customer's profile defines
nothing by itself: the fiscal document records what you confirmed.

Everything ARCA answers stays out of the way until it is relevant: the result
appears once ARCA responds, and the assigned number appears as soon as it is
known, including while an attempt is `Solicitando` or `Incierto`, so it can be
used for reconciliation. The CAE and its expiry appear only after
authorization; rejection details appear for a rejection, and the stored error
detail remains visible for an uncertain attempt. **Detalle técnico** opens by
itself while the document is `Solicitando`, `Incierto` or `Rechazado` and stays
collapsed otherwise.

A refusal from the engine is stored instead of being thrown, and shown above the
form, so the document opens and says which account or field needs attention. A
missing mapping, an unmeasured tax rate, an unsupported currency, or a `Tax ID`
that is not a CUIT: each names the row or the field. Incomplete receiver
document details remain editable on the proposal and are validated when
**Emitir** is pressed.

### 7.2 Emit

Press **Emitir**. This is the only action that consumes a number, and it asks
for confirmation first. The amounts are re-derived from the invoice at that
moment, so a mapping you fixed is the one that travels.

Three things happen before anything is sent:

- **The invoice must still be submitted.** The status is checked again when
  **Emitir** is pressed; a draft or cancelled invoice is refused before WSAA
  or WSFE is called. An invoice linked to an ARCA emission attempt cannot be
  deleted, so its fiscal evidence stays traceable.
- **The saved environment selects the profile.** Emission loads the profile for
  the voucher's saved environment, not whichever profile is currently marked
  default. Changing the default does not move an existing voucher. If that
  company/environment profile is missing, nothing is sent; restore or recreate
  it before trying again.
- **One emission at a time per company.** A second simultaneous emission is
  refused with the expiry of the lock that is holding the first one, and nothing
  is sent. If the earlier process died, its comprobante is left in
  `Solicitando`: reconcile it before emitting again. If this process outlives
  its own lock while reading the number, it also refuses to send: nothing was
  consumed and the document goes back to `Borrador`.
- **The number is stored before the request.** The number ARCA reports is written
  and saved before `FECAESolicitar` leaves, so an attempt that is interrupted can
  be reconciled against that exact number.

| Result | What the document says | What to do |
|---|---|---|
| `Autorizado` | the CAE, its expiry, the number, and the exact bytes exchanged | print it. The document is now read-only and cannot be sent again |
| `Rechazado` | **Observaciones de ARCA** with its message and code | correct the invoice, then press Emitir again. The rejected number is not authorized |
| `Incierto` | ARCA did not confirm and did not reject | do **not** send it again. Press **Conciliar** |

A transport failure is `Incierto`, never `Rechazado`: it cannot prove that
nothing arrived. An attempt that failed *before* a number was assigned (the
ticket, the numbering read, a request that could not be built) returns the
document to `Borrador` and does not count as an attempt, because no number was
consumed. An attempt that failed *after* the number was stored is `Incierto`,
never `Borrador`.

### 7.3 Reconcile

**Conciliar** asks ARCA whether that number exists. It only reads.

- ARCA has it: the document becomes `Autorizado` with the CAE it reports.
- ARCA answers `602` (no matching record at query time): the document stays
  `Incierto`. A recently authorized comprobante may not yet be visible, so this
  answer does not release the number. Do not re-send; reconcile again or verify
  the number with ARCA.
- ARCA still cannot settle it: the document stays `Incierto`. Review the number
  in ARCA's own tools, or wait and reconcile again. Never re-send.

Reconciliation is also available in `Solicitando`: a request whose process died
left the document there, and only ARCA can say whether it arrived. A
`Solicitando` document with no stored number is different: the number is saved
before the request leaves, so no number proves nothing was sent. It returns to
`Borrador` without asking ARCA and without counting the attempt.

Reconciliation loads the profile for the comprobante's saved environment. If
that profile is missing, it cannot query ARCA; restore or recreate the profile
for that environment before reconciling.

### 7.4 What the states mean

| Estado | Meaning | Editable |
|---|---|---|
| `Borrador` | a proposal; nothing was sent | yes |
| `Solicitando` | an attempt is in flight, or its process died | no. Reconcile |
| `Autorizado` | ARCA returned a CAE. Final | no, and it cannot be deleted |
| `Rechazado` | ARCA refused; no comprobante exists | yes, to correct and retry |
| `Incierto` | ARCA may have received it | no. Reconcile |

A document with attempts cannot be deleted: it is the record of what ARCA
received for that invoice.

## 8. TLS and the CA bundle

Every HTTPS call the app makes goes through one function,
`core/client.py::_default_post`, and it leaves certificate verification at the
`requests` default: the `certifi` bundle of the interpreter that runs bench.
The app never passes `verify=False`, and nothing outside ARCA's own calls is
affected by anything below.

Some ARCA endpoints do not send their intermediate certificate. Measured
against homologación on 2026-11-13:

| endpoint | certificates sent | verification |
|---|---|---|
| `awshomo.afip.gov.ar`, `wsaahomo.afip.gov.ar`, `wswhomo.afip.gov.ar` | 4 | OK |
| `awshomo.arca.gob.ar` | 1 (leaf only) | fails: `unable to get local issuer certificate` |

When a site points at an endpoint with an incomplete chain, give this app its
own bundle with `ARCA_CA_BUNDLE`. It applies **only** to ARCA's calls.

```bash
# 1. Validate the intermediate against the default roots before trusting it.
#    Use the Python that runs bench (the pipx venv, not the system one).
BUNDLE=$(python3 -c 'import certifi; print(certifi.where())')

openssl verify -CAfile "$BUNDLE" sectigo-intermediate.pem
sha256sum sectigo-intermediate.pem   # record issuer, validity and source next to it

# 2. Build a bundle: the default roots plus that intermediate
cat "$BUNDLE" sectigo-intermediate.pem > arca-ca-bundle.pem

# 3. Point this app at it, in the environment of the bench process
export ARCA_CA_BUNDLE=/path/to/arca-ca-bundle.pem
```

A path that does not exist is refused before any request, with
`ARCA_CA_BUNDLE no apunta a un archivo de certificados.`

Do not set `verify=False`, do not patch the installed `certifi`, and do not add
the certificate to the system trust store. All three trade a real verification
failure for a symptom that goes away, and none of them survives the next
upgrade of the environment it patched.

## 9. Moving to production

Each company moves on its own. Keep the existing Homologación profile and create
a separate `Producción` profile with the production certificate and key. The
three URLs are selected automatically. Mark the Production profile as default
when new proposals and padrón lookups should use Production; existing vouchers
continue using their saved environment.

`ws_sr_constancia_inscripcion` (and `wsfe`, if used) must be authorized in WSASS
for the production certificate. Nothing in the code changes.

## 10. What is deliberately not configured

- No default CUIT, company, certificate, key or credential.
- No point of sale or comprobante type is required; the defaults are optional
  and vouchers can override them.
- No environment is inferred: an operator chooses one when creating a profile.
- No Psilotron IP, domain, site name or path anywhere in the shipped code.
