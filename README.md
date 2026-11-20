# ARCA Integration

Facturación electrónica de ARCA (ex AFIP) para ERPNext, mediante el web service WSMTXCA (factura con detalle de ítems).

> En desarrollo. Emite facturas A y B en homologación.

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

### Sitio de prueba con datos (homologación)

Con un certificado de homologación habilitado para `wsmtxca` (y, si se quiere completar clientes, para `ws_sr_constancia_inscripcion`):

```bash
bench --site <sitio> arca-demo --cert <ruta>/certificado.crt --key <ruta>/clave.key
```

En un sitio nuevo completa el asistente (Argentina, ARS, español, zona horaria de Buenos Aires). Después crea la empresa "Demo ARCA" con la CUIT del certificado, las cuentas y plantillas de IVA, cuatro productos y clientes con las CUIT de prueba del padrón de ARCA. Verifica la conexión y deja tres facturas en borrador: una A, una B de servicios y una B con un ítem exento. Se puede correr varias veces: completa lo que falta. No corre en producción.

ARCA rechaza un login nuevo mientras siga vigente el ticket anterior (hasta 12 h). Para reinstalar el sitio sin quedar bloqueado:

```bash
bench --site <sitio> arca-tickets export tickets.json
bench --site <sitio> reinstall
bench --site <sitio> arca-demo --cert ... --key ... --tickets tickets.json
rm tickets.json   # contiene credenciales vigentes
```

Antes de abrir un PR, corré `pre-commit run --all-files` y los tests. El repositorio no tiene CI por ahora.

## Licencia

MIT
