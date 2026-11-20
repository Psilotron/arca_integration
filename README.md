# ARCA Integration

Facturación electrónica de ARCA (ex AFIP) para ERPNext, mediante el web service WSMTXCA (factura con detalle de ítems).

> En desarrollo. Todavía no emite comprobantes.

## Requisitos

- Frappe v16 y ERPNext v16.

## Instalación

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/Psilotron/arca_integration --branch main
bench --site <sitio> install-app arca_integration
```

## Desarrollo

La app usa `pre-commit` para el formato y el lint (ruff, eslint y prettier):

```bash
cd apps/arca_integration
pre-commit install
```

Tests:

```bash
bench --site <sitio> set-config allow_tests true
bench --site <sitio> run-tests --app arca_integration
```

## CI

- **CI**: instala la app sobre Frappe y ERPNext v16 y corre los tests en cada PR y en cada push a `main`.
- **Linters**: pre-commit, [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) y [pip-audit](https://pypi.org/project/pip-audit/) en cada PR.

## Licencia

MIT
