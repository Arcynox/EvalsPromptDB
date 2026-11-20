<img src="assets/evals-promptdb.png" width="112" height="112" alt="Icono de Evals PromptDB">

# Evals PromptDB

[Read in English](README.md)

Evals PromptDB es una base de datos SQLite embebida para versionar prompts de LLM y guardar sus evaluaciones. Reúne plantillas, casos de prueba, salidas y puntuaciones en un archivo local. Usá la biblioteca Python en tu aplicación o la CLI en scripts y CI. El núcleo usa la biblioteca estándar de Python.

- Guardá versiones inmutables y elegí la activa por etiqueta. Hacé rollback moviendo la etiqueta a una versión anterior.
- Ejecutá los mismos casos sobre distintas versiones con el generador y la función de evaluación que elijas.
- Compará cambios en el texto, las variables y las puntuaciones. Definí una puntuación mínima en CI.
- Exportá JSON determinista para Git o transferí una copia de seguridad consistente de SQLite.

## Instalación

Usá Python 3.10 o posterior. Instalá el paquete desde este directorio.

```bash
python -m pip install .
promptdb --version
```

La distribución se llama `evals-promptdb`. Importá `PromptDB` desde `promptdb` y ejecutá `promptdb` o `python -m promptdb`. También podés ejecutar `python -m promptdb` desde este directorio sin instalar el paquete.

Si instalaste PromptDB 0.1.0 o 0.2.0 desde este proyecto, desinstalá la distribución anterior (`promptdb` o `promptdb-local`, respectivamente) en ese entorno antes de instalar 1.0.0. La versión 1.0.0 abre directamente los archivos SQLite existentes.

## Inicio rápido en Python

Ejecutá el ejemplo en un directorio sin un archivo `prompts.db`. Las funciones de ejemplo simulan las llamadas a un modelo para que puedas probarlo sin conexión.

```python
from promptdb import PromptDB

with PromptDB("prompts.db") as db:
    v1 = db.save(
        "pais",
        "Indicá el país al que pertenece {{ciudad}}.",
        tags=["produccion"],
        metadata={"equipo": "ejemplos"},
    )
    v2 = db.save(
        "pais",
        "Devolvé solo el nombre del país al que pertenece {{ciudad}}.",
        tags=["experimento"],
    )
    caso = db.add_test("pais", input={"ciudad": "Madrid"}, expected="España")

    actual = db.get("pais", tag="produccion")
    print(actual.render(caso.input))

    db.run_tests("pais", version=v1.version, model="demo",
                 generate=lambda texto: "Madrid está en España.")
    db.run_tests("pais", version=v2.version, model="demo",
                 generate=lambda texto: "España")

    resultado = db.compare("pais", [v1.version, v2.version], model="demo")
    print(resultado.versions[1].diff)
    print(resultado.best.prompt.version)  # 2

    db.promote("pais", v2.version)
    db.promote("pais", v1.version)  # Vuelve a producción la primera versión
```

El mismo caso se aplica a las dos versiones. Por defecto, `run_tests()` puntúa por coincidencia exacta tras quitar el espacio en blanco de los extremos. Una función `scorer(output, expected)` puede devolver cualquier puntuación numérica de 0 a 1 y es obligatoria si el valor esperado tiene un tipo distinto de texto.

## Inicio rápido en la terminal

Los comandos usan una base nueva llamada `cli.db`.

```bash
promptdb --db cli.db save pais --template 'Indicá el país al que pertenece {{ciudad}}.' --tag produccion
promptdb --db cli.db save pais --template 'Devolvé solo el país de {{ciudad}}.' --tag experimento
promptdb --db cli.db get pais --tag produccion --input '{"ciudad":"Madrid"}'
promptdb --db cli.db add-test pais --input '{"ciudad":"Madrid"}' --expected '"España"'
promptdb --db cli.db eval pais --version 2 --model demo --test-id 1 --score 0.87
promptdb --db cli.db compare pais 1 2 --model demo --min-score 0.85
promptdb --db cli.db promote pais 2
```

Usá `save --from-file plantilla.txt` para leer una plantilla de varias líneas en UTF-8 y `--from-file -` para leer de la entrada estándar. `get --json` devuelve el registro completo, `get --input` aplica los valores a la plantilla y `compare --json` devuelve la comparación en un formato apto para scripts. Ejecutá `promptdb --help` para ver todos los comandos.

`--min-score` evalúa la última versión incluida en la comparación. El comando devuelve el código `3` si el promedio está por debajo del umbral o si ninguna evaluación coincide con los filtros. Devuelve `0` si supera o iguala el umbral, `1` si ocurre un error de ejecución y `2` si hay un error de sintaxis en la CLI.

## API de Python

| Operación | Comportamiento |
| --- | --- |
| `save(name, template, ...)` | Crea una versión. Admite `version`, `variables`, `model`, `temperature`, `metadata` y `tags`. |
| `get(name, version="latest", tag=None)` | Devuelve la versión de mayor número que coincide con los filtros. Si pasás un número y una etiqueta, deben coincidir ambos. Si falta el registro, lanza `KeyError`. |
| `Prompt.render(values)` | Reemplaza los marcadores detectados una sola vez. Admite valores adicionales. Una variable faltante produce `KeyError`. |
| `list(tags=())` y `history(name)` | Listan versiones. El filtro exige todas las etiquetas solicitadas. El historial va de la versión más antigua a la más reciente. |
| `set_tags(name, version, tags)` | Reemplaza las etiquetas de una versión. El equivalente en CLI es `tag`. |
| `promote(name, version, tag="produccion")` | Mueve una etiqueta a una sola versión del prompt en una transacción. |
| `add_test(name, input=..., expected=...)` y `list_tests(name)` | Guardan y consultan casos compartidos por todas las versiones de un prompt. |
| `add_eval(name, version=..., score=..., model=..., ...)` | Registra una puntuación y admite `test_id`, `output`, `metadata` y la fecha original `created_at`. |
| `run_tests(name, model=..., generate=..., ...)` | Aplica las variables, llama a `generate(texto)` y registra todos los resultados en una transacción. Admite `version`, `scorer` y `metadata`. |
| `compare(name, versions=None, ...)` y `list_evals(name, ...)` | Consultan evaluaciones guardadas con filtros por modelo, caso y fecha. |
| `delete(name, version)` | Elimina una versión, sus etiquetas y sus evaluaciones. Los casos quedan asociados al nombre del prompt. |

Los marcadores como `{{ciudad}}` y `{{ ciudad }}` se detectan en orden de aparición. Si pasás `variables` de forma explícita, usá el mismo conjunto que en la plantilla. Los números aumentan por nombre, incluso después de borrar una versión. Para editar una plantilla o su configuración, guardá una versión nueva.

Usá un `datetime` con zona horaria para `created_at`, `since` y `until`. La CLI acepta fechas ISO 8601 con desplazamiento, como `2026-11-12T10:00:00-03:00`. Los intervalos incluyen `since` y excluyen `until`. La primera versión solicitada es la base del diff. `resultado.best` devuelve la de mayor promedio y, en caso de empate, respeta el orden de comparación. Las versiones sin evaluaciones coincidentes tienen promedio `None`. Usá el mismo modelo y un conjunto consistente de casos cuando la puntuación decida un paso de CI.

Una tanda toma la versión y los casos visibles al inicio. Las llamadas al modelo y la puntuación ocurren fuera de la transacción de SQLite. Si falla la sustitución de variables, el generador, la evaluación o la escritura, la tanda no deja resultados parciales.

## Snapshots JSON y copias de seguridad SQLite

Usá JSON para revisar cambios en Git, transferir los datos o cargar una base nueva.

```bash
promptdb --db prompts.db export > promptdb.json
promptdb --db restaurada.db import promptdb.json
promptdb --db restaurada.db history pais
```

El snapshot incluye plantillas, metadata, etiquetas, entradas, valores esperados, salidas, fechas, identificadores y contadores de versión. El mismo estado produce el mismo JSON en UTF-8. La importación requiere una base nueva o sin uso y valida el snapshot completo en una transacción. Usá `db.export_json()` para obtener el texto y `db.import_json(payload)` para restaurarlo desde Python.

El formato JSON se identifica con `format: "evals-promptdb"` y `version: 1`. La versión del formato es independiente de la versión del paquete. Podés guardar el snapshot en Git e importarlo a un archivo SQLite nuevo en CI.

Usá una copia de seguridad SQLite si necesitás un archivo de base de datos listo para abrir. Otras conexiones pueden seguir usando el archivo original. Elegí un archivo nuevo en un directorio existente cuyo sistema de archivos admita enlaces duros.

```bash
promptdb --db prompts.db backup prompts-copia.db
promptdb --db prompts-copia.db history pais
```

La API equivalente es `db.backup("prompts-copia.db")`. Abrí la copia directamente para usarla. Usá una instancia de `PromptDB` por hilo. Configurá el tiempo de espera ante bloqueos con `PromptDB("prompts.db", timeout=30)` en segundos. Pasá `create=False` para exigir una base existente o `PromptDB(":memory:")` para usar una base temporal.

## Integración con OpenAI

Corré primero el inicio rápido en Python. Instalá el SDK opcional con `python -m pip install openai` y configurá `OPENAI_API_KEY`. El generador envía la plantilla con las variables sustituidas a la API Responses. Evals PromptDB guarda localmente el texto devuelto y la puntuación. [Referencia de la API Responses](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

```python
from openai import OpenAI
from promptdb import PromptDB

client = OpenAI()
modelo = "gpt-4o-mini"

def generar(texto):
    return client.responses.create(
        model=modelo,
        input=texto,
        store=False,
    ).output_text

with PromptDB("prompts.db", create=False) as db:
    evaluaciones = db.run_tests("pais", version=2, model=modelo, generate=generar)
    for evaluacion in evaluaciones:
        print(evaluacion.test_id, evaluacion.score, evaluacion.output)
```

## Desarrollo

Corré las pruebas con la biblioteca estándar.

```bash
python -m unittest discover -s tests -v
```

GitHub Actions define una matriz para Python 3.10 a 3.14 en Linux y Python 3.14 en macOS y Windows. Para construir y revisar el código fuente empaquetado y el wheel, usá estas herramientas de empaquetado.

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
```

[Historial de cambios](CHANGELOG.md) · [Licencia MIT](LICENSE)
