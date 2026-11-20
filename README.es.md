<div align="center">
  <img src="assets/evals-promptdb.png" width="128" height="128" alt="Icono de Evals PromptDB">
  <h1>Evals PromptDB</h1>
  <p><strong>Prompts de LLM versionados y evaluaciones en SQLite local.</strong></p>
  <p>
    <a href="https://github.com/Arcynox/EvalsPromptDB/actions/workflows/ci.yml"><img src="https://github.com/Arcynox/EvalsPromptDB/actions/workflows/ci.yml/badge.svg" alt="Estado de CI en GitHub Actions"></a>
    <a href="#instalación"><img src="https://img.shields.io/badge/Python-3.10%2B-6d4aff" alt="Python 3.10 o posterior"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Licencia-MIT-27896d" alt="Licencia MIT"></a>
  </p>
  <p>
    <a href="README.md">English</a> · <a href="#instalación">Instalación</a> · <a href="#inicio-rápido-en-python">Inicio en Python</a> · <a href="#inicio-rápido-en-la-terminal">Inicio en la CLI</a> · <a href="#ejemplos-de-proveedores">Proveedores</a>
  </p>
</div>

Guardá plantillas, casos, salidas y puntuaciones en un archivo `.db`. Usá la API de Python en tu aplicación o la CLI en scripts y CI. El núcleo usa la biblioteca estándar de Python.

## Flujo de trabajo

| Paso | Qué podés hacer |
| --- | --- |
| **Versionar** | Guardá plantillas inmutables y elegí la versión activa por etiqueta. |
| **Evaluar** | Ejecutá los mismos casos sobre varias versiones con el generador y la función de puntuación que elijas. |
| **Comparar y promover** | Revisá diffs, puntuaciones y cobertura. Aplicá controles en CI y hacé rollback moviendo la etiqueta. |
| **Compartir** | Versioná JSON determinista en Git o transferí una copia consistente de SQLite. |

## Instalación

Usá Python 3.10 o posterior. Instalá el paquete desde este directorio.

```bash
python -m pip install .
promptdb --version
```

La distribución se llama `evals-promptdb`. Importá `PromptDB` desde `promptdb` y ejecutá `promptdb` o `python -m promptdb`. También podés ejecutar `python -m promptdb` desde este directorio sin instalar el paquete.

Si instalaste PromptDB 0.1.0 o 0.2.0 desde este proyecto, desinstalá la distribución anterior (`promptdb` o `promptdb-local`, respectivamente) en ese entorno antes de instalar `evals-promptdb`. Una conexión de escritura migra los archivos SQLite anteriores en una sola transacción. Usá el paquete actualizado para abrir los archivos migrados.

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
                 generate=lambda texto: "Madrid está en España.", run_id="inicio-rapido")
    db.run_tests("pais", version=v2.version, model="demo",
                 generate=lambda texto: "España", run_id="inicio-rapido")

    resultado = db.compare("pais", [v1.version, v2.version], model="demo", run_id="inicio-rapido")
    print(resultado.versions[1].diff)
    print(resultado.versions[1].coverage)  # 1.0
    print(resultado.best.prompt.version)  # 2

    db.promote("pais", v2.version)
    db.promote("pais", v1.version)  # Vuelve a producción la primera versión
```

El mismo caso se aplica a las dos versiones. Por defecto, `run_tests()` puntúa por coincidencia exacta tras quitar el espacio en blanco de los extremos. Una función `scorer(output, expected)` puede devolver cualquier puntuación numérica de 0 a 1 y es obligatoria si el valor esperado tiene un tipo distinto de texto.

## Prompts de chat

Guardá mensajes de texto ordenados cuando el modelo necesite distinguir las instrucciones de los turnos de conversación. Podés usar variables en cada mensaje. Los roles admitidos son `system`, `developer`, `user` y `assistant`.

```python
from promptdb import PromptDB

with PromptDB(":memory:") as db:
    prompt = db.save("traducir", messages=[
        {"role": "system", "content": "Traducí el texto del usuario a {{idioma}}."},
        {"role": "user", "content": "{{texto}}"},
    ])
    entrada = {"idioma": "español", "texto": "Good morning"}
    mensajes = prompt.render_messages(entrada)
    db.add_test("traducir", input=entrada, expected="Buenos días")
    db.run_tests("traducir", model="demo", generate_messages=lambda conversación: "Buenos días")
```

`Prompt.kind` indica si el prompt es `text` o `chat`. `render_messages()` también admite prompts de texto y devuelve un único mensaje de usuario. `render()` devuelve texto JSON para prompts de chat. Usá `generate_messages` para pasar los mensajes directamente al cliente del modelo.

```bash
promptdb --db chat.db save traducir --messages - <<'JSON'
[{"role":"system","content":"Traducí a {{idioma}}."},{"role":"user","content":"{{texto}}"}]
JSON
promptdb --db chat.db get traducir --input '{"idioma":"español","texto":"Good morning"}'
```

## Inicio rápido en la terminal

Los comandos usan una base nueva llamada `cli.db`.

```bash
promptdb --db cli.db save pais --template 'Indicá el país al que pertenece {{ciudad}}.' --tag produccion
promptdb --db cli.db save pais --template 'Devolvé solo el país de {{ciudad}}.' --tag experimento
promptdb --db cli.db get pais --tag produccion --input '{"ciudad":"Madrid"}'
promptdb --db cli.db add-test pais --input '{"ciudad":"Madrid"}' --expected '"España"'
promptdb --db cli.db eval pais --version 1 --model demo --run-id demo-1 --test-id 1 --score 0.83
promptdb --db cli.db eval pais --version 2 --model demo --run-id demo-1 --test-id 1 --score 0.87
promptdb --db cli.db compare pais 1 2 --model demo --run-id demo-1 --min-score 0.85 --min-coverage 1 --max-regression 0.02
promptdb --db cli.db promote pais 2
```

<details>
<summary>Ejemplo de salida de la comparación</summary>

```text
Version  Variables  Added  Removed  Evals  Avg score  Cases  Case avg  Paired delta
1        ciudad     -      -        1      0.830      1/1    0.830     -
2        ciudad     -      -        1      0.870      1/1    0.870     +0.040 (1)
```

</details>

Usá `save --from-file plantilla.txt` para leer una plantilla de varias líneas en UTF-8 y `--from-file -` para leer de la entrada estándar. `get --json` devuelve el registro completo, `get --input` aplica los valores a la plantilla y `compare --json` devuelve la comparación en un formato apto para scripts. Ejecutá `promptdb --help` para ver todos los comandos.

### Controles de calidad en CI

La última versión de la comparación es la candidata. La primera es la base. Pasá el mismo `--model` y `--run-id` de las puntuaciones registradas para evaluar un solo modelo y una sola corrida.

| Parámetro | Condición para aprobar |
| --- | --- |
| `--min-score 0.85` | La candidata alcanza la puntuación mínima. Con cobertura o regresión, cada caso evaluado pesa lo mismo y solo cuentan puntuaciones asociadas a un caso. Por sí solo, `--min-score` usa el promedio de todos los registros de evaluación que coinciden. |
| `--min-coverage 1` | La candidata tiene puntuaciones para el 100% de los casos elegibles. Si omitís `--test-id`, son elegibles todos los casos del prompt. Las repeticiones de un caso cuentan una vez para la cobertura. |
| `--max-regression 0.02` | El promedio de la candidata cae como máximo 0.02 puntos frente a la base en los casos compartidos. La cobertura compartida debe alcanzar `--min-coverage`, o el 100% si omitís ese parámetro. |

Un control fallido devuelve `3`, incluso cuando faltan puntuaciones o casos requeridos. Devuelve `0` si aprueba, `1` ante errores de ejecución y `2` ante errores de sintaxis. El informe JSON incluye `case_scores`, `coverage`, `case_average_score` y `paired` por versión, además del promedio de registros.

## API de Python

| Operación | Comportamiento |
| --- | --- |
| `save(name, template, ...)` | Crea una versión de texto o admite `messages=` para chat. También acepta `version`, `variables`, `model`, `temperature`, `metadata` y `tags`. |
| `get(name, version="latest", tag=None)` | Devuelve la versión de mayor número que coincide con los filtros. Si pasás un número y una etiqueta, deben coincidir ambos. Si falta el registro, lanza `KeyError`. |
| `Prompt.render(values)` | Reemplaza los marcadores detectados una sola vez. Admite valores adicionales. Una variable faltante produce `KeyError`. |
| `list(tags=())` y `history(name)` | Listan versiones. El filtro exige todas las etiquetas solicitadas. El historial va de la versión más antigua a la más reciente. |
| `set_tags(name, version, tags)` | Reemplaza las etiquetas de una versión. El equivalente en CLI es `tag`. |
| `promote(name, version, tag="produccion")` | Mueve una etiqueta a una sola versión del prompt en una transacción. |
| `add_test(name, input=..., expected=...)` y `list_tests(name)` | Guardan casos compartidos por todas las versiones de un prompt y listan los casos activos. |
| `get_test(name, test_id)` | Lee un caso por su identificador, incluidos los casos archivados. |
| `set_test_archived(name, test_id, archived=True)` y `replace_test(...)` | Permiten actualizar la suite activa y seguir consultando el caso anterior y sus evaluaciones. |
| `add_eval(name, version=..., score=..., model=..., ...)` | Registra una puntuación y admite `test_id`, `run_id`, `output`, `metadata` y la fecha original `created_at`. |
| `run_tests(name, model=..., generate=..., ...)` | Aplica las variables a los casos activos, llama al generador y registra los resultados en una transacción. Elegí una versión o etiqueta y pasá `test_ids` para evaluar un subconjunto. |
| `await arun_tests(name, model=..., generate=..., ...)` | Espera un generador de texto o mensajes y admite un evaluador síncrono o asíncrono. |
| `compare(name, versions=None, ...)` y `list_evals(name, ...)` | Consultan evaluaciones guardadas con filtros por corrida, modelo, caso y fecha. |
| `delete(name, version)` | Elimina una versión, sus etiquetas y sus evaluaciones. Los casos quedan asociados al nombre del prompt. |

Los marcadores como `{{ciudad}}` y `{{ ciudad }}` se detectan en orden de aparición. Si pasás `variables` de forma explícita, usá el mismo conjunto que en la plantilla. Los números aumentan por nombre, incluso después de borrar una versión. Para editar una plantilla o su configuración, guardá una versión nueva.

Usá un `datetime` con zona horaria para `created_at`, `since` y `until`. La CLI acepta fechas ISO 8601 con desplazamiento, como `2026-11-12T10:00:00-03:00`. Los intervalos incluyen `since` y excluyen `until`. La primera versión solicitada es la base del diff. `resultado.best` devuelve la versión con mayor `average_score` de los registros que coinciden y, en caso de empate, respeta el orden de comparación. Las versiones sin evaluaciones coincidentes tienen promedio `None`. Usá el mismo modelo y un conjunto consistente de casos cuando la puntuación decida un paso de CI.

Una tanda toma la versión y los casos visibles al inicio. Las llamadas al modelo y la puntuación ocurren fuera de la transacción de SQLite. Si falla la sustitución de variables, el generador, la evaluación o la escritura, la tanda no deja resultados parciales. `run_tests()` asigna un identificador por defecto. Pasá el mismo `run_id` a la base y la candidata cuando las evalúes juntas.

Usá `arun_tests()` con un cliente asíncrono. Las transacciones de SQLite quedan fuera de las esperas al proveedor. Los dos métodos admiten `tag` para seleccionar la versión desplegada y `test_ids` para ejecutar casos activos en el orden solicitado. La cobertura se calcula sobre los casos elegibles de cada comparación.

### Mantenimiento de casos de prueba

Usá `archive-test` para retirar un caso o `restore-test` para volver a activarlo. Con `replace-test NOMBRE ID --input ... --expected ...`, archivás el caso anterior y creás uno nuevo con su propio identificador en una transacción. `get-test` y `evals` siguen mostrando el caso original y sus puntuaciones. `run_tests`, `tests` y `compare` usan los casos activos por defecto. Para una auditoría, usá `include_archived=True` en `list_tests()` o `compare()`, o `--include-archived` en `tests` o `compare`.

### Evaluar salidas estructuradas

Importá `json_match` con `from promptdb.scorers import json_match` y pasá `scorer=json_match` a `run_tests()` para evaluar JSON. El orden de los campos y los espacios irrelevantes se ignoran. Se respeta el orden de los arrays y se distingue entre números y booleanos. El JSON malformado recibe cero y su salida se guarda en la evaluación.

Para puntuar la salida de otra herramienta, usá `eval --test-id ID --scorer json --output-file respuesta.json` junto al nombre del prompt, la versión y el modelo. Pasá `--output-file -` para leer de la entrada estándar. El ejemplo de proveedores también admite `--scorer json`.

## Snapshots JSON y copias de seguridad SQLite

Usá JSON para revisar cambios en Git, transferir los datos o cargar una base nueva.

```bash
promptdb --db prompts.db export > promptdb.json
promptdb --db restaurada.db import promptdb.json
promptdb --db restaurada.db history pais
```

El snapshot incluye plantillas, metadata, etiquetas, entradas, valores esperados, salidas, fechas, identificadores y contadores de versión. El mismo estado produce el mismo JSON en UTF-8. La importación requiere una base nueva o sin uso y valida el snapshot completo en una transacción. Usá `db.export_json()` para obtener el texto y `db.import_json(payload)` para restaurarlo desde Python.

El formato JSON se identifica con `format: "evals-promptdb"` y `version: 4`. Guarda los mensajes de chat como arrays JSON y conserva el estado de los casos archivados. La importación también admite los formatos 1 a 3. Podés guardar el snapshot en Git e importarlo a un archivo SQLite nuevo en CI.

Usá una copia de seguridad SQLite si necesitás un archivo de base de datos listo para abrir. Otras conexiones pueden seguir usando el archivo original. Elegí un archivo nuevo en un directorio existente cuyo sistema de archivos admita enlaces duros.

```bash
promptdb --db prompts.db backup prompts-copia.db
promptdb --db prompts-copia.db history pais
```

La API equivalente es `db.backup("prompts-copia.db")`. Abrí la copia directamente para usarla. Usá una instancia de `PromptDB` por hilo. Configurá el tiempo de espera ante bloqueos con `PromptDB("prompts.db", timeout=30)` en segundos. Pasá `create=False` para exigir una base existente o `PromptDB(":memory:")` para usar una base temporal.

Usá `PromptDB("prompts.db", read_only=True)` o `promptdb --read-only` para abrir una base existente con el esquema actual en solo lectura. Esa conexión permite consultar, comparar, exportar y respaldar. `db.check()` informa problemas de integridad o de claves foráneas.

```bash
promptdb --db vacia.db init
promptdb --db prompts.db --read-only check
promptdb --db prompts.db --read-only export --output snapshot.json
```

`export --output` crea un archivo UTF-8 nuevo y conserva cualquier archivo existente en esa ruta. La CLI usa UTF-8 para prompts y JSON en pipes, e `import` admite snapshots UTF-8 con BOM.

## Ejemplos de proveedores

Corré primero el inicio rápido en Python. Desde el directorio del proyecto, `python -m examples.evaluate` ejecuta casos guardados sobre varias versiones con un identificador de corrida compartido. Admite Ollama, OpenAI, Anthropic, servidores con Chat Completions compatible con OpenAI y una función determinista de mayúsculas para pruebas sin conexión.

```bash
python -m examples.evaluate ollama pais 1 2 --model qwen2.5-coder:7b --run-id ollama-smoke
promptdb compare pais 1 2 --model ollama/qwen2.5-coder:7b --run-id ollama-smoke --min-coverage 1
```

Ollama usa su API HTTP local y un modelo instalado. Pasá `--ollama-host` si querés elegir otro servidor. OpenAI usa el SDK opcional `openai` y `OPENAI_API_KEY`. Anthropic usa el SDK opcional `anthropic` y `ANTHROPIC_API_KEY`. Indicá el identificador del modelo con `--model`. El script muestra la clave del modelo y el identificador necesarios para `promptdb compare`. [API de Ollama Chat](https://docs.ollama.com/api/chat) · [API de Anthropic Messages](https://platform.claude.com/docs/en/api/messages)

Usá `--timeout` para dar más tiempo al primer arranque, `--max-tokens` para limitar la salida o `--ollama-context` para configurar el tamaño de contexto local.

Usá `openai-compatible --api-base` con una URL base como `http://127.0.0.1:8000/v1` para un servidor que exponga Chat Completions. Configurá `PROMPTDB_ENDPOINT_API_KEY` si ese servidor requiere un token. El ejemplo envía los mensajes `developer` como instrucciones `system` a Ollama y a los servidores compatibles. Con Anthropic, ubicá las instrucciones `system` y `developer` antes del primer turno de conversación. [Documentación del servidor compatible de vLLM](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)

### OpenAI desde Python

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
