# Evaluación histórica local

## Desde la web

Abre los detalles del análisis y ejecuta la evaluación. La API permite hasta 500 juegos por jugador, con progreso en segundo plano. El historial debe contener juegos anteriores suficientes para construir cada predicción. El proceso evalúa el perfil del bateador; no reconstruye el matchup futuro seleccionado ni consulta alineaciones actuales para partidos pasados.

Se informa MAE de hits, Brier, log loss, calibración por intervalos de probabilidad y cobertura del rango predictivo. Un resultado favorable en un jugador o periodo corto no demuestra superioridad general.

## Descargar una vez y reutilizar

Antes de una descarga grande, valida el acceso real a los proveedores con `.venv/bin/python scripts/check_connections.py`. Consulta el resultado de Statcast en `.cache/connection-report.json`: la comprobación descarga una muestra sin reutilizar las cachés y ejecuta el cálculo. Puede pasar aunque otras fuentes estén bloqueadas. Esta prueba verifica la integración, no la precisión deportiva; el historial temporal del diagnóstico no reemplaza las descargas siguientes.

Ejemplo con IDs MLB de Aaron Judge, Shohei Ohtani y Juan Soto:

```sh
.venv/bin/python -m baseball_analyzer_v2.research download \
  --players 592450,660271,665742 \
  --start 2023-03-01 --end 2025-11-02
```

Necesita acceso a Baseball Savant. Los historiales quedan como archivos Parquet en `.cache/diamond/statcast/`. Las solicitudes siguientes extienden la cobertura existente. Si una descarga falla, no se atribuye a un archivo parcial la cobertura que falta.

También puedes usar exportaciones CSV de Statcast con `batter`, `game_date` y `events`. Conserva `game_pk`, `at_bat_number` y `pitch_number` para identificar lanzamientos y evitar duplicados. No mezcles archivos de varios jugadores sin sus IDs.

## Evaluar una cohorte

```sh
.venv/bin/python -m baseball_analyzer_v2.research evaluate \
  --input .cache/diamond/statcast/batter-592450.parquet \
  --input .cache/diamond/statcast/batter-660271.parquet \
  --input .cache/diamond/statcast/batter-665742.parquet \
  --lookback-days 3650 --games 40 --max-games 500
```

Puedes repetir `--input` para más archivos y aumentar `--max-games` en la consola. La evaluación genera `observations.parquet` y `latest.json` en `.cache/diamond/evaluation/`, con métricas globales, por temporada y por jugador. `/api/evaluation` sirve el último reporte de esa ruta. `--output` permite guardar otro experimento sin reemplazarlo.

Los ejemplos con tres jugadores ilustran el proceso; para evaluar generalización se requiere una cohorte más amplia y representativa. El reporte se marca como limitado por debajo de 1000 observaciones o 10 jugadores. Superar ese umbral no implica por sí solo que el modelo sea preciso.

## Comparar CatBoost

Instala las dependencias opcionales y agrega `--candidate` al comando de evaluación:

```sh
.venv/bin/python -m pip install -r requirements-research.txt
.venv/bin/python -m baseball_analyzer_v2.research evaluate \
  --input .cache/diamond/statcast/batter-592450.parquet \
  --input .cache/diamond/statcast/batter-660271.parquet \
  --input .cache/diamond/statcast/batter-665742.parquet \
  --lookback-days 3650 --games 40 --max-games 1000 --candidate
```

El corte usa el primer 60% de fechas para entrenar, el siguiente 20% para calibrar y el último 20% para prueba. Ninguna fecha se divide entre esos conjuntos. Se exige al menos 100 observaciones por conjunto y dos clases en entrenamiento/calibración. Se entrenan un clasificador de 1+ hit y un regresor de hits esperados; sus modelos `.cbm` y calibración se guardan localmente.

Para marcar un candidato como elegible para revisión se exige:

- Al menos 1000 observaciones y 10 jugadores en el conjunto final de prueba.
- Mejor Brier y un intervalo del 95% de su diferencia bajo cero, estimado remuestreando fechas completas.
- No empeorar MAE de hits ni log loss frente al modelo base en esas mismas fechas.

Es un criterio inicial de revisión, no una promoción automática. También deben revisarse estabilidad por temporada y subgrupos y la representatividad de los datos. Los candidatos no tienen un intervalo predictivo de conteos calibrado; el reporte no les atribuye la cobertura del modelo base.

## Reproducibilidad y alcance

Los datos del propio día objetivo se excluyen completamente. La referencia usa la tasa reciente y las mismas oportunidades proyectadas, sin conocer las PA reales del juego. La evaluación conserva las predicciones y resultados para inspección. Las características CatBoost se calculan antes de cada objetivo: tasas históricas/recientes, oportunidades estimadas, HR, K, muestra efectiva, velocidad de salida y xBA.

No se han demostrado mejoras de precisión con datos reales en esta entrega. Las pruebas automatizadas usan datos sintéticos identificados como tales para comprobar invariantes matemáticos, fechas, caché, contratos API y flujos de interfaz. No constituyen una evaluación deportiva. Las fuentes externas pueden requerir habilitar estos dominios en el entorno cloud: `statsapi.mlb.com`, `baseballsavant.mlb.com`, `api.open-meteo.com`, además de GitHub para el registro de jugadores.
