# Pendientes del refactor de idioma y organización

## Checkpoint actual

- Rama: `refactor/english-internals-spanish-ui`.
- El núcleo de mapeos, alícuotas, importes de factura, numeración y WSAA usa
  identificadores internos en inglés, conservando `CUIT`, `CAE`, `comprobante`
  y otros términos fiscales oficiales.
- Se renombraron helpers internos de configuración ARCA y se corrigieron varios
  textos visibles para usar español; los fieldnames de ERPNext, como `tax_id`,
  se conservan únicamente como contratos técnicos.
- Se eliminó la migración de perfiles históricos y los campos de endpoints y
  condición del emisor marcados como legados, porque la instalación será desde
  cero.
- Se restringió el listado de perfiles a documentos `ARCA Settings` visibles
  para el usuario y se acotó el manejo de excepciones al firmar con WSAA.
- Se descartó el filtro personalizado de facturas de venta en `ARCA
  Comprobante`, porque impedía mostrar cualquier factura; el campo volvió al
  comportamiento estándar de Frappe mientras se define un enfoque validado.
- La suite offline actual pasa: `430 passed, 13 deselected`. La diferencia con
  el checkpoint anterior corresponde a la eliminación del filtro y sus pruebas,
  además de la migración histórica y los campos legados.
- El refactor y la eliminación del filtro están separados en commits sobre esta
  rama; no se publicó ningún cambio en `main`.

## Pendientes

- [ ] Inventariar y renombrar a inglés los identificadores internos restantes en
  `core/`, controladores Frappe, API adapters, scripts de bench y JavaScript.
  Pendientes principales: helpers del padrón y métodos del cliente WSFE que no
  sean nombres oficiales del servicio.
- [ ] Revisar clases y objetos de dominio que aún tienen nombres españoles;
  mantener los términos fiscales oficiales cuando traducirlos haga menos claro
  el concepto.
- [ ] Auditar los textos visibles en formularios, diálogos, botones, mensajes y
  metadatos de DocTypes para que todo texto dirigido al usuario esté en español;
  revisar especialmente las apariciones de `Tax ID` y `ARCA Settings`.
- [ ] Organizar los módulos por responsabilidades sin mezclar en un mismo cambio
  renombres de identificadores y cambios de interfaz. El siguiente candidato es
  separar `api.py` en adaptadores de padrón, comprobantes, bloqueos y
  serialización.
- [x] Confirmar la política de instalación desde cero: no se mantienen
  migraciones históricas ni campos legados que ya no participan en el modelo.
- [ ] Revisar los contratos que sí deben permanecer estables: nombres de
  DocTypes, fieldnames de ERPNext, XML de ARCA y métodos `@frappe.whitelist()`.
- [ ] Actualizar pruebas y documentación junto con cada grupo de cambios, y
  ejecutar la suite offline completa después de cada etapa.
- [ ] Revisar el diff final, confirmar que no quedan referencias antiguas,
  ejecutar la suite offline completa, integrar el refactor en una rama de
  trabajo y publicarlo cuando esté completo.
