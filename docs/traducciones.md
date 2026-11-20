# Traducciones

La app usa el sistema de traducciones de Frappe (gettext). El código fuente está en inglés y la interfaz se ve en español gracias a `arca_integration/locale/es.po`.

## Reglas

- Todo texto visible para el usuario se escribe en **inglés** y pasa por la función de traducción:
  - Python: `_("Voucher {0} was authorized").format(name)`. Nunca uses f-strings ni concatenación dentro de `_()`.
  - JavaScript: `__("Voucher {0} was authorized", [name])`.
  - Jinja: `{{ _("Voucher") }}`.
- Los labels, opciones y descripciones de los DocTypes (JSON) también van en inglés. Frappe los extrae solo.
- Los términos fiscales oficiales que no tienen equivalente quedan igual en los dos idiomas: `CUIT`, `CAE`, `CAEA`, `MTXCA`, `WSAA`. Igual se agregan al `.po` con la misma traducción.
- Los mensajes que devuelve ARCA (errores, observaciones y eventos) se muestran tal como llegan. No se traducen.

## Flujo para agregar o cambiar textos

Desde la carpeta del bench:

```bash
bench generate-pot-file --app arca_integration   # 1. extrae las cadenas a locale/main.pot
bench update-po-files --app arca_integration     # 2. agrega las cadenas nuevas a locale/es.po
# 3. completá los msgstr vacíos en locale/es.po
bench compile-po-to-mo --app arca_integration    # 4. compila el .mo para ver los cambios en el sitio
bench --site <sitio> clear-cache
```

En el commit van `main.pot` y `es.po`. El `.mo` se genera en `sites/assets/locale/` y no se versiona.

## Controles automáticos

- **Test** `arca_integration/tests/test_translations.py`: falla si alguna cadena de `main.pot` no está traducida en `es.po`, o si su traducción está vacía o marcada como *fuzzy*.
- **CI**: después de los tests, regenera `main.pot` y `es.po` y falla si hay diferencias con lo commiteado (sin contar fechas ni comentarios). Si falla, corré los pasos 1 a 3 y commiteá.

## Idioma del sitio y de los usuarios

Para ver la interfaz en español, configurá el idioma `es` en **System Settings** o en cada usuario. Si un usuario tiene `es-AR`, Frappe usa `es` como respaldo.
