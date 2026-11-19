# Installation

## 1. Get the app onto the bench

Any transport works; the only requirement is that the folder ends up named
`arca_integration`, because that must match the project name in
`pyproject.toml`.

```bash
# from a clone, if the bench can reach the repository
git clone <repository-url> ~/frappe-bench/apps/arca_integration

# or as a tarball, from wherever the checkout lives
tar --exclude='.venv' --exclude='.git' -czf arca_integration.tar.gz "$(basename "$PWD")"
scp arca_integration.tar.gz <user>@<bench-host>:/tmp/
```

Then, on the bench host:

```bash
cd ~/frappe-bench
bench get-app /path/to/arca_integration
bench --site <site> install-app arca_integration
bench restart
```

`cryptography` and `requests` ship with Frappe; no extra dependency is needed.

Installing creates no company, assigns no CUIT and loads no credentials. The
app arrives with empty DocTypes.

## 2. Prepare the credentials

ARCA issues the certificate from the WSASS portal (homologación environment
while testing). You get `erp.crt` and `erp.key`.

`erp.key` normally arrives in OpenSSH format. The app accepts PEM and OpenSSH,
but converting makes the key portable:

```bash
openssl pkey -in erp.key -out erp.pem
```

Keep both files out of the repository. `tmp/` is already git-ignored.

For the fiscal service the certificate must additionally be authorized for
`wsfe - Facturación Electrónica` in WSASS. The lookup service needs
`ws_sr_constancia_inscripcion`.

## 3. Verify the install

Create an `ARCA CUIT Query`, choose the company and the ARCA environment
(Homologación or Producción), type a CUIT, save, then press **Consultar**. The
query uses only the profile for that company and environment; it never falls
back to the company's default profile.

Homologación test data:

| CUIT | Expected |
|---|---|
| 30500001735 | JURIDICA, ACTIVO, `BANCO PRUEBA 02` |
| 27222222228 | FISICA, ACTIVO |
| 20222222223 | error: constancia blocked (missing biometric data) |
| 30711111111 | error: incorporation pending |
| the certificate's CUIT | not present in homologación |

If the company has no profile for the selected environment, the query is
recorded as an error and reports that configuration is missing — that is a
configuration step, not a bug. Continue in [configuration.md](configuration.md).

From a Customer or Sales Invoice, use **Consultar CUIT en ARCA** to start a
lookup without retyping the CUIT. From an invoice, the company is fixed to that
invoice; select the ARCA environment explicitly. Each lookup is stored as a
separate history entry and shows its company, environment, and time. On a
Customer, applying ARCA's registered name is an explicit, unchecked-by-default
choice; the lookup does not determine the receiver's WSFE VAT condition.

## 4. Migrating ARCA Settings

When upgrading from the company-based version, the migration preserves each
existing profile's name, company and credentials, keeps its selected
environment, sets it as the company's default, and adds a uniqueness
constraint for `(company, environment)`. It does not create a second environment
profile: add a Production or Homologación profile separately when needed.
Existing vouchers and their saved environments are not changed. Legacy endpoint
columns are left intact but hidden and ignored; the backend selects endpoints
from each profile's environment.

The migration does not convert the much older `Single` DocType schema. If
upgrading directly from that version, complete the previous Single-to-company
conversion first.

## 5. Troubleshooting

| Symptom | Cause |
|---|---|
| `WSAA rechazó la autenticación` with `coe.alreadyAuthenticated` | a valid ticket exists but the cache file was lost. Wait for it to expire, or restore the cache file for that company and environment. |
| `cone.notAuthorized` | the certificate is not authorized for the service in WSASS. |
| `El CUIT del certificado no corresponde al CUIT configurado` | the certificate belongs to another taxpayer. Attach the right certificate, or fix the company's Tax ID. |
| `La clave privada ARCA no está en un formato soportado` | the key is encrypted, or in a container format. Convert it to PEM. |
| `No existe persona con ese Id` | the CUIT is not in the padrón (normal in homologación). |
| The company has no default ARCA profile | create an `ARCA Settings` profile and mark the intended environment as default. |
