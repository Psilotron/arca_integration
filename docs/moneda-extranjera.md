# Facturar en dólares u otra moneda

## Preparar una sola vez

Hacé estos pasos la primera vez que facturás en una moneda nueva.

### Paso 1: crear una cuenta a cobrar en dólares

1. Andá a **Plan de cuentas**.
2. Dentro de **Cuentas por cobrar**, creá la cuenta **Deudores USD**.
3. En **Tipo de cuenta**, elegí **A cobrar**. En **Moneda**, elegí **USD**.

### Paso 2: preparar el cliente

¿El cliente ya tiene facturas en pesos?

- **No:** abrí el cliente y seguí con los puntos de abajo.
- **Sí:** creá otro cliente para los dólares, por ejemplo **ACME SA (USD)**. ERPNext no deja usar el mismo cliente en pesos y en dólares. Copiá el CUIT y tocá **Completar desde ARCA**.

En el cliente:

- En **Moneda de facturación**, elegí **USD**.
- En la tabla **Cuentas**, agregá tu empresa con la cuenta **Deudores USD**.

## Hacer la factura

1. Elegí el cliente en dólares.
2. Si el cliente te paga en dólares, tildá **Se paga en la misma moneda extranjera**. Si te paga en pesos, dejala sin tildar.
3. Guardá.
4. Si el tipo de cambio no es el que acepta ARCA, aparece un aviso. Tocá **Usar cotización de ARCA**.
5. Validá como siempre.

## Si aparece un error

**"Cliente … tiene asientos contables en la moneda ARS…"**
El cliente ya tiene facturas en pesos. Hacé el paso 2 con un cliente nuevo.

**"La moneda … no tiene moneda de ARCA."**
Abrí la moneda (por ejemplo **USD**) y completá **Moneda de ARCA**.
