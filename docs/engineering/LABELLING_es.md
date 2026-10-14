# Guía de etiquetado de resoluciones

Gracias por ayudar. Esta guía explica qué hacer con las hojas de esta carpeta. No hace falta instalar nada:
basta con un editor de texto para leer y Excel (o LibreOffice) para rellenar `respuestas.csv`.

## Para qué sirve

Licita usa un modelo de lenguaje para leer resoluciones sobre recursos contra pliegos y anotar los motivos
del recurso y lo que el tribunal decidió sobre cada uno. Para saber si lo hace bien necesitamos una
referencia escrita por una persona que conozca la contratación pública. Sus respuestas son esa referencia.
No se publican: solo se publican las cifras de acierto del modelo.

## Qué hacer con cada hoja

Cada archivo `NN.txt` es una resolución. Arriba está la **parte dispositiva** (el fallo); debajo, el texto
completo. Para cada hoja:

1. Lea la resolución completa.
2. **Resultado**: según la parte dispositiva, no el resumen ni la portada.
3. **Motivos**: anote solo los motivos que el tribunal **resuelve** (con razonamiento y conclusión
   propios). Uno por fila. Si un motivo toca dos categorías, elija aquella en la que se apoya el
   razonamiento del tribunal.
4. **Decisión** sobre cada motivo: `estimado` si el tribunal da la razón al recurrente en ese motivo,
   `desestimado` si no, `no_resuelto` si lo menciona pero no entra a resolverlo.
5. Escriba en `notas` cualquier duda o decisión difícil. Es muy útil.

## Cómo rellenar `respuestas.csv`

Hay una fila por hoja con `hoja` y `resolucion` ya rellenas. No cambie esas dos columnas ni la cabecera.

| Columna | Qué poner |
|---|---|
| `resultado` | `estimado`, `estimado_parcial`, `desestimado`, `inadmitido` (incluye archivo, pérdida sobrevenida del objeto, desistimiento) u `otro` |
| `motivo` | 1, 2, 3… (número de orden del motivo) |
| `categoria` | una de la tabla siguiente |
| `decision` | `estimado`, `desestimado` o `no_resuelto` |
| `notas` | texto libre |

**Varios motivos:** copie la fila (misma `hoja`, `resolucion` y `resultado`) y cambie `motivo`,
`categoria` y `decision`. **Ningún motivo resuelto** (por ejemplo, una inadmisión): rellene solo
`resultado` y deje `categoria` y `decision` vacías.

## Categorías

| `categoria` | Cuándo |
|---|---|
| `brand_or_tailored_specs` | Prescripciones técnicas que citan una marca o encajan con un solo fabricante (art. 126 LCSP) |
| `disproportionate_solvency` | Solvencia económica o técnica desproporcionada (arts. 74–90) |
| `award_criteria` | Criterios de adjudicación sin motivar, indefinidos o sin vínculo con el objeto (arts. 145–146) |
| `lots_division` | No división en lotes sin justificar (art. 99) |
| `envelope_contamination` | Información del precio en sobres de criterios sujetos a juicio de valor |
| `budget_or_price` | Presupuesto, valor estimado o sistema de precios |
| `special_conditions_or_other_clause` | Condiciones especiales de ejecución u otra cláusula del pliego |
| `exclusion_or_admission` | Exclusión o admisión de licitadores (no es sobre el pliego en sí) |
| `procedure_or_other` | Procedimiento u otra cuestión |

## Reglas importantes

- **No consulte respuestas de ningún programa de IA** antes de etiquetar una resolución, y no comente sus
  respuestas con la otra persona que etiqueta: medimos el acuerdo entre ustedes dos.
- No hace falta hacerlo todo de una vez. Unas 30 resoluciones ya bastan; 40 es mejor.
- Calcule unos 10–15 minutos por resolución.
- Su nombre no aparece en ningún sitio: solo una letra (A o B).

Cuando termine, devuelva `respuestas.csv`. Se comprueba automáticamente y, si hay errores de escritura,
recibirá la lista de filas a corregir.
