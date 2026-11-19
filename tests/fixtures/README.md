# Fixtures

Two kinds of file live here, and the difference matters:

- **Captured** — a verbatim answer from ARCA's homologación services, or from
  its padrón. These are the real rejection codes and shapes.
- **Constructed** — written by hand because no such answer exists yet. Each one
  says `CONSTRUCTED` in its own header comment: `wsfe/cae_aprobado.xml`,
  `wsfe/cae_parcial.xml`, `wsfe/ptos_venta.xml`.

A constructed fixture proves the parser handles a shape; it never proves ARCA
sends that shape.

## Sanitization

The captured answers in `wsaa_response.xml` and `wsfe/cae_*.xml` carried the
issuer's CUIT in the response header. Before publication that CUIT was replaced
by the placeholder `20000000001`. Nothing else was altered: codes, messages,
dates, points of sale and CAE values are as received.

Homologación CAEs have no fiscal validity and are not secret. The CUIT is the
only field that was removed.
