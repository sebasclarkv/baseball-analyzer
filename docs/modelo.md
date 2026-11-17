# Cómo calcula Diamond IQ 2.2

## Evidencia histórica

El modelo conserva la estimación bayesiana de la versión anterior: vida media de 365 días para historial largo y 45 para forma reciente, con muestras recientes separadas del historial de base. Usa xBA para contactos rastreados y resultados observados para las demás apariciones. H/PA tiene un prior de 0.220 por aparición; no es el promedio de bateo por turno. Los priors fijos de liga y las vidas medias son supuestos por evaluar, no parámetros óptimos demostrados.

La incertidumbre limita la evidencia efectiva al menor valor entre suma de pesos y tamaño efectivo de muestra. Así, cientos de apariciones muy antiguas no generan artificialmente una banda estrecha.

Las consultas permiten hasta 5000 días en la API (el selector ofrece hasta diez años) y se limitan a la disponibilidad Statcast desde 2015. El historial termina antes de la fecha del pronóstico. Se excluye el día completo, incluso en dobles juegos, para proteger el corte de información.

El perfil sin partido usa juegos de temporada regular. En una previa se intenta utilizar historial de la fase del juego: regular, postemporada o pretemporada. Si faltan apariciones completas de la fase, el perfil de un juego de pretemporada o playoffs puede usar historial regular y lo indica. Los insumos MLB de equipo y pitchers se consultan para la fase del partido.

## Oportunidades y rivales

Con alineación publicada e historial del equipo, las apariciones se derivan de las últimas 40 distribuciones de PA del equipo y del puesto del bateador. Para un total de equipo T y puesto s: `floor(T/9) + (s <= T mod 9)`. Supone que el jugador inicia y permanece en el juego; no modela sustituciones individuales. Sin esa evidencia se usa la distribución de sus últimos diez juegos.

La exposición al abridor usa sus últimos quince inicios con bateadores enfrentados y el puesto al bate. Si no hay puesto confirmado se promedian los nueve lugares. Sin historial de duración se declara un escenario de dos apariciones ante el abridor. Las oportunidades restantes corresponden a relevistas.

El duelo con el abridor conserva el ajuste de mano y log5 sobre tasas estabilizadas. El bullpen reúne actuaciones como relevista de los pitchers en el roster contrario, dentro de 180 días, con vida media de 60 días y prior de 200 PA. La carga de lanzamientos de los últimos tres días se muestra, pero no se traduce en una probabilidad arbitraria de disponibilidad. No se conoce de antemano qué relevista entrará. Sin historial de bullpen se usa el nivel del bateador frente a una referencia neutral de liga.

Los datos históricos de MLB usados para oportunidades y carga también excluyen la fecha objetivo. Los ajustes de hits, HR, BB y K se muestran por aparición. HR es subconjunto de hits; K es subconjunto de outs. Estas tasas no son categorías disjuntas y no deben sumarse.

## Distribución de resultados

Para cada cantidad posible de apariciones se calcula una beta-binomial aproximada contra el abridor y otra contra el bullpen. Se convolucionan ambos resultados y se mezclan sobre las oportunidades posibles. Se supone independencia entre segmentos condicionada a sus parámetros; esta aproximación necesita evaluación.

Los HR se calculan como subconjunto condicional de los hits de cada segmento, por lo que su conteo y probabilidad de 1+ no pueden superar los de hits. La incertidumbre de esa proporción sigue siendo aproximada.

La distribución completa produce:

- Probabilidades de 0, exactamente 1 y al menos 2 hits, normalizadas a 1.
- Probabilidad de al menos un hit y promedio de hits esperados.
- Cuantiles discretos 5%–95% como rango predictivo aproximado del 90%.

El rango describe resultados enteros del partido. No es un intervalo de confianza de la media. Por discreción puede cubrir más del 90%; el evaluador informa su cobertura y amplitud reales. Los intervalos de tasas usados internamente siguen siendo aproximaciones basadas en Wilson, y no un posterior jerárquico completo del duelo.

## Qué se aplica y qué se muestra

| Factor | Estado en 2.2 |
|---|---|
| Trayectoria, forma, xBA, mano y tasas del abridor | Participan en las tasas |
| Orden al bate, PA del equipo y duración del abridor | Participan cuando hay datos; alternativa declarada cuando faltan |
| Tasas agrupadas del bullpen | Participan cuando hay historial de relevistas |
| Temperatura, viento, techo y estadio | Contexto; sin ajuste numérico |
| Repertorio, velocidad del pitcher, edad y días desde último juego | Contexto; sin ajuste numérico |
| Lanzamientos recientes de los relevistas | Contexto; sin afirmar disponibilidad ni aplicar penalización de fatiga |
| Lesiones, sustituciones, defensa y cambios tácticos futuros | No modelados |

Open-Meteo devuelve viento respecto al norte geográfico; no se interpreta como viento hacia las gradas sin conocer orientación y techo. Para un techo retráctil se conserva la incertidumbre sobre su estado. Los pronósticos fuera del horizonte meteorológico disponible se marcan como ausentes.

## Evaluación y candidatos

La evaluación retrospectiva mide el perfil del bateador con cortes anteriores a cada fecha y separa la temporada de prueba. La evaluación de partidos usa pronósticos archivados antes del inicio real y sus correspondientes box scores. Cuando hay datos suficientes, el archivo incluye variantes calculadas con las mismas entradas, retirando cada factor por separado para comparar orden al bate, abridor y bullpen.

El candidato CatBoost usa exclusivamente características históricas del bateador. Entrenamiento, calibración logística y prueba se separan por fechas completas; las predicciones del conjunto de prueba no ajustan parámetros. El candidato se guarda para investigación y no reemplaza automáticamente el modelo servido. Consulta [Evaluación histórica](evaluacion.md).

## Modo en vivo

El tablero toma entrada, outs, corredores, cuenta, bateador, lanzador y resultados ya registrados del feed del partido. La **probabilidad de victoria** viene de MLB y se vincula a la última aparición completada que tenga ese dato. La hora y el cambio en puntos porcentuales corresponden a esa jugada. Entre jugadas, el marcador o la cuenta pueden avanzar antes de que MLB publique otro porcentaje.

La **estimación de hit** usa hasta 730 días de Statcast y separa temporada regular, playoffs y pretemporada. El corte excluye el día del partido. Primero estima H/PA ajustada al pitcher mostrado si hay muestra suficiente. Para un turno activo con una cuenta distinta de 0–0, compara los resultados del bateador en apariciones que alcanzaron esa cuenta con su referencia de 0–0. Cada aparición cuenta una sola vez, aunque una falta repita la cuenta. El ajuste se suaviza cuando hay poca evidencia.

Si la primera aparición tiene probabilidad `p` y las siguientes tienen probabilidad al inicio `q`, la estimación de un hit adicional en `n` apariciones es `1 − (1 − p) × (1 − q)^(n − 1)`. Los escenarios de 1, 2 y 3 apariciones suponen un enfrentamiento estable e independencia de resultados. El equipo podría terminar el juego antes de que el bateador vuelva al plato. Los hits que ya consiguió se muestran aparte.

La comprobación retrospectiva evaluó **6,562 apariciones de 34 bateadores** en temporada regular de 2025, con el perfil fijado al inicio de ese año. Se eligió de antemano una sola foto entre el segundo y el sexto lanzamiento por aparición y se conservaron los turnos que llegaron a esa cuenta. El Brier bajó de **0.14269 a 0.14048** (un menor error). La probabilidad media ajustada fue 19.42% y la frecuencia observada fue 16.87%. La prueba aísla la contribución de la cuenta usando el perfil del bateador. La calibración del enfrentamiento completo en vivo queda por medir.

Para reproducir esta prueba, [descarga la cohorte histórica](evaluacion.md#evaluar-una-cohorte) y ejecuta `.venv/bin/python scripts/check_live_count.py`. El reporte incluye el intervalo del 95% de la diferencia de Brier, calculado al remuestrear fechas de juego completas.

El ejemplo histórico usa fotos del feed con corte explícito. La primera muestra a Toronto abajo 2–1 en la cuarta entrada y la segunda muestra el 4–2 tras el jonrón de Andrés Giménez. MLB pasó de 42.1% a 75.9% de probabilidad de victoria para Toronto. El porcentaje describe la jugada consultada y puede diferir del de un proveedor de apuestas.
