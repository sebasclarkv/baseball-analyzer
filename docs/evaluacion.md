# Evaluación local de Diamond IQ

## Desde la web

Abre **Detalles técnicos y fuentes → Medir precisión histórica**. La API permite hasta 500 juegos por jugador, con progreso en segundo plano. Cada predicción usa únicamente las apariciones anteriores a la fecha del partido. Esta evaluación mide el perfil del bateador.

El error medio absoluto (MAE) se mide en hits. Brier, log loss y RPS miden el error de las probabilidades. Un valor menor es mejor. El RPS tiene en cuenta que 0, 1 y 2+ hits son resultados ordenados. La tabla de calibración compara el porcentaje pronosticado con la frecuencia observada. La cobertura indica en cuántos juegos el resultado quedó dentro del rango predictivo.

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

La selección usa los totales MLB de una temporada anterior a la prueba. Esta configuración elige 40 bateadores con semilla fija, repartidos entre regulares de 500+ apariciones al plato, debutantes de 50+ y jugadores establecidos de 50 a 199. El informe reservado se calcula sobre los juegos elegibles de 2025, con historial desde 2023 y hasta 40 predicciones espaciadas por bateador y temporada. [Manifiesto de jugadores usado para esta evaluación](benchmarks/cohorte-2025.json)

```sh
.venv/bin/python -m baseball_analyzer_v2.research download \
  --cohort docs/benchmarks/cohorte-2025.json \
  --start 2023-03-01 --end 2025-11-02
.venv/bin/python -m baseball_analyzer_v2.research evaluate \
  --cohort docs/benchmarks/cohorte-2025.json --game-type regular \
  --lookback-days 1095 --games 20 --max-games 40
```

Para generar un manifiesto nuevo, ejecuta `python -m baseball_analyzer_v2.research cohort --selection-season 2024 --size 40 --seed 42` desde el entorno virtual. Puedes elegir año y semilla. El año siguiente queda reservado para la prueba.

Puedes evaluar exclusivamente `postseason` o `spring` con `--game-type`. Si un CSV carece de `game_type`, se admite para evaluación regular bajo el supuesto de que el archivo contiene esa fase. Conserva el campo de tipo de juego para separar correctamente orígenes mezclados.

Para evaluar archivos específicos, usa `--input`:

```sh
.venv/bin/python -m baseball_analyzer_v2.research evaluate \
  --input .cache/diamond/statcast/batter-592450.parquet \
  --input .cache/diamond/statcast/batter-660271.parquet \
  --input .cache/diamond/statcast/batter-665742.parquet \
  --lookback-days 3650 --games 40 --max-games 500
```

Puedes repetir `--input` y usar `--holdout-season` con tus propios archivos. La evaluación genera `observations.parquet` y `latest.json` en `.cache/diamond/evaluation/`, con métricas por temporada, jugador y grupo. `/api/evaluation` sirve el último reporte local. `--output` permite guardar otro experimento por separado.

El reporte se marca como limitado por debajo de 1000 observaciones o 10 jugadores en el periodo evaluado. Revisa el desglose por grupo para detectar diferencias de rendimiento en muestras pequeñas.

### Resultado de la temporada reservada 2025

Se descargaron 149,475 lanzamientos de 40 bateadores entre 2023 y 2025. Con la configuración anterior se obtuvieron 3,087 predicciones de temporada regular y quedaron **1,094 en 2025**, con **35 bateadores** que cumplieron los requisitos. El RPS también fue menor que el de la referencia en cada uno de los tres grupos seleccionados.

| Métrica en 2025 | Diamond IQ 2.2 | Referencia de tasa reciente |
|---|---:|---:|
| RPS de 0, 1 y 2+ hits | 0.19325 | 0.19845 |
| Brier de 1+ hit | 0.23800 | 0.24500 |
| Log loss de 0, 1 y 2+ hits | 1.02436 | 1.04316 |
| Error medio absoluto de hits | 0.66236 | 0.68185 |

El RPS fue **2.62% menor** que el de la referencia en esta cohorte. La diferencia absoluta fue −0.00520, con intervalo del 95% de −0.00844 a −0.00212 al remuestrear 5,000 veces las 182 fechas completas de prueba con semilla 42. Para 0, 1 y 2+ hits, las probabilidades medias fueron 41.31%, 38.78% y 19.91%, frente a frecuencias observadas de 43.05%, 38.21% y 18.74%. El rango nominal del 90% cubrió el 97.71% de resultados y tuvo una amplitud media de 2.28 hits.

La referencia usa la tasa reciente con la misma distribución de apariciones proyectadas. Estos resultados corresponden al perfil del bateador en la cohorte descrita. La precisión del contexto de juego se mide con el seguimiento prospectivo. El candidato CatBoost del experimento obtuvo Brier de 0.24209 y MAE de 0.67947 y quedó fuera del criterio de adopción.

## Contrastar los pronósticos guardados

Usa **Detalles técnicos y fuentes → Cruzar resultados MLB** o ejecuta:

```sh
.venv/bin/python -m baseball_analyzer_v2.forecast_validation \
  --lead-minutes 30 --max-age-hours 24
```

Esto compara los pronósticos del archivo local con el box score final. Por cada juego, bateador y versión se elige el pronóstico más reciente que se emitió entre 30 minutos y 24 horas antes del primer lanzamiento real. El reporte indica por qué quedan fuera los casos sin resultado, fuera de la ventana o sin apariciones del bateador. El archivo original y `evaluation/prospective.json` quedan en `.cache/diamond/`, o en el directorio configurado con `DIAMOND_DATA_DIR`.

Los pronósticos archivados con 2.2 guardan variantes que retiran de una en una las contribuciones del orden al bate, el abridor y el bullpen, cuando el factor tiene datos. Se comparan los pares del mismo partido y bateador. Los archivos previos de 2.1 pueden puntuar con sus proyecciones originales. La muestra crecerá al archivar previas de juegos nuevos. Consulta los detalles en `/api/evaluation/prospective`.

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

Con una temporada reservada, el candidato usa las temporadas anteriores para entrenar y calibrar. Sin temporada reservada, el corte usa los primeros 60% de fechas para entrenar, los siguientes 20% para calibrar y los últimos 20% para prueba. Una fecha completa pertenece a un solo conjunto. Se exigen al menos 100 observaciones por conjunto y dos clases en entrenamiento y calibración. Se entrenan un clasificador de 1+ hit y un regresor de hits esperados. Sus modelos `.cbm` y calibración se guardan localmente.

Para marcar un candidato como elegible para revisión se exige:

- Al menos 1000 observaciones y 10 jugadores en el conjunto final de prueba.
- Mejor Brier y un intervalo del 95% de su diferencia bajo cero, estimado remuestreando fechas completas.
- No empeorar MAE de hits ni log loss frente al modelo base en esas mismas fechas.

Es un criterio inicial de revisión, no una promoción automática. También deben revisarse estabilidad por temporada y subgrupos y la representatividad de los datos. Los candidatos no tienen un intervalo predictivo de conteos calibrado; el reporte no les atribuye la cobertura del modelo base.

## Reproducibilidad y alcance

Los datos del propio día objetivo se excluyen completamente. La referencia usa la tasa reciente y las mismas oportunidades proyectadas, sin conocer las PA reales del juego. La evaluación conserva las predicciones y resultados para inspección. Las características CatBoost se calculan antes de cada objetivo: tasas históricas/recientes, oportunidades estimadas, HR, K, muestra efectiva, velocidad de salida y xBA.

Las pruebas automatizadas usan datos sintéticos identificados como tales para comprobar invariantes matemáticos, fechas, caché, contratos API y flujos de interfaz. El diagnóstico de fuentes prueba el acceso a `statsapi.mlb.com`, `baseballsavant.mlb.com`, `api.open-meteo.com` y GitHub por separado.
