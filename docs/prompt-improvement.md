# De un resumen que pierde datos a un prompt listo para producción

Un equipo usa un resumen para registrar incidentes. La primera versión pide una sola oración, pero omite datos operativos. En este tutorial probamos una segunda versión que pide conservar la hora y los sistemas afectados.

El ejemplo usa dos incidentes ficticios y respuestas de modelo simuladas. Podés reproducirlo sin cuenta ni API key. La comparación, los resultados guardados y el cambio de etiqueta sí se ejecutan en Evals PromptDB.

## Guardá las versiones y evaluá los mismos casos

Instalá `evals-promptdb` y ejecutá este script en un directorio nuevo.

```python
from promptdb import PromptDB

cases = [
    ("El equipo abrió un incidente a las 09:12 porque fallaban los pagos. "
     "El servicio se recuperó cuatro minutos después y se cerró el aviso.",
     "A las 09:12 fallaron los pagos durante cuatro minutos.",
     "Hubo un problema con los pagos."),
    ("El equipo recibió una alerta a las 14:30. La sincronización del catálogo "
     "se había detenido y todavía no se sabía cuánto duraría la interrupción.",
     "A las 14:30 se detuvo la sincronización del catálogo.",
     "Hubo un problema con el catálogo."),
]
name = "resumen_incidentes"

with PromptDB("tutorial.db") as db:
    v1 = db.save(name, "Resume este incidente en una oración: {{incidente}}", tags=["produccion"])
    v2 = db.save(name, "Conserva la hora, el sistema y la duración si se conoce. "
                       "Resume en una oración: {{incidente}}")
    for incident, expected, _ in cases:
        db.add_test(name, input={"incidente": incident}, expected=expected)

    def demo_model(rendered):
        for incident, expected, baseline in cases:
            if rendered.endswith(incident):
                return expected if rendered.startswith("Conserva") else baseline
        raise ValueError("Caso inesperado")

    for prompt in (v1, v2):
        db.run_tests(name, version=prompt.version, model="demo",
                     run_id="resumen-demo", generate=demo_model)
```

La v1 obtiene `0.000` porque sus respuestas pierden datos requeridos. La v2 obtiene `1.000` con cobertura de `2/2`. El criterio de este ejemplo es coincidencia exacta con una referencia. Para resúmenes abiertos, una función de puntuación propia puede reconocer paráfrasis y comprobar los datos requeridos.

## Compará y promové la candidata

```bash
promptdb --db tutorial.db compare resumen_incidentes 1 2 --model demo --run-id resumen-demo --min-score 1 --min-coverage 1 --max-regression 0 &&
promptdb --db tutorial.db promote resumen_incidentes 2 --tag produccion &&
promptdb --db tutorial.db get resumen_incidentes --tag produccion --json
```

La candidata tiene que alcanzar la puntuación mínima, cubrir los dos casos y evitar una caída frente a la base. El encadenamiento promueve la v2 solo si la comparación aprueba. El último comando muestra que la etiqueta `produccion` apunta a la versión 2.

Si un caso nuevo descubre una regresión, podés devolver la etiqueta a la versión 1 con `promptdb --db tutorial.db promote resumen_incidentes 1 --tag produccion`. El historial y las evaluaciones siguen disponibles.
