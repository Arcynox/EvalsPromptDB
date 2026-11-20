# Historial de cambios

## 1.1.0

- Puntuaciones por caso, cobertura y comparación sobre los casos compartidos por la versión base y la candidata.
- `compare --min-coverage` y `compare --max-regression` para controlar cobertura y regresión en CI.
- Identificadores de corrida en `run_tests()` y `add_eval()`, con filtro `run_id` en la API y `--run-id` en la CLI.
- Migración transaccional al esquema SQLite 2. Los snapshots JSON de formato 2 conservan los identificadores de corrida y pueden importar el formato 1.
- Ejemplo ejecutable con Ollama, OpenAI, Anthropic y un generador determinista sin conexión.

## 1.0.0

- La distribución pasa a llamarse `evals-promptdb`. El módulo y el comando se llaman `promptdb`.
- `export_json()` y `import_json()` guardan y restauran un snapshot JSON reproducible con los identificadores, las fechas y la secuencia de versiones. La CLI ofrece `export` e `import`.
- `run_tests()` ejecuta los casos de una versión con un generador y un evaluador configurables. Los resultados se guardan en una sola transacción.
- `compare --min-score` devuelve el código 3 si la última versión comparada queda por debajo del umbral o carece de evaluaciones que coincidan con los filtros.
- Documentación completa en inglés y español y nuevo icono.
- Compatibilidad de lectura y escritura con los archivos SQLite de 0.1.0 y 0.2.0.

## 0.2.0

- Copias consistentes de SQLite con `PromptDB.backup()` y `promptdb backup`.
- `promptdb tag` permite reemplazar o quitar las etiquetas de una versión.
- `promptdb eval --created-at` acepta la fecha de una evaluación existente.
- Los comandos de la CLI requieren una base existente, excepto `save`, que puede crearla.
- Las lecturas pueden abrir una base mientras otro proceso tiene una escritura reservada. La espera ante bloqueos se puede configurar desde Python.
- La distribución pasa a llamarse `promptdb-local`. El módulo y el comando se llaman `promptdb`. Se pueden abrir las bases creadas con 0.1.0.
- Pruebas de persistencia, concurrencia, recuperación ante fallos, evaluaciones y CLI.

## 0.1.0

- Versionado de plantillas SQLite, variables, etiquetas, casos de prueba y evaluaciones.
- Comparación de versiones y rollback por etiqueta desde Python y la CLI.
