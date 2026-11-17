# Cómo calcula Diamond IQ 2.1

## Evidencia histórica

El modelo conserva la estimación bayesiana de la versión anterior: vida media de 365 días para historial largo y 45 para forma reciente, con muestras recientes separadas del historial de base. Usa xBA para contactos rastreados y resultados observados para las demás apariciones. H/PA tiene un prior de 0.220 por aparición; no es el promedio de bateo por turno. Los priors fijos de liga y las vidas medias son supuestos por evaluar, no parámetros óptimos demostrados.

La incertidumbre limita la evidencia efectiva al menor valor entre suma de pesos y tamaño efectivo de muestra. Así, cientos de apariciones muy antiguas no generan artificialmente una banda estrecha.

Las consultas permiten hasta 5000 días en la API (el selector ofrece hasta diez años) y se limitan a la disponibilidad Statcast desde 2015. El historial termina antes de la fecha del pronóstico; se excluye el día completo, incluso en dobles juegos. Así se evita incorporar resultados parciales o finales del partido que se pretende proyectar.

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

| Factor | Estado en 2.1 |
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

La evaluación retrospectiva actual cubre el perfil del bateador. No reutiliza las alineaciones o rosters actuales para fingir el contexto de partidos antiguos. Reconstruir el modelo completo exige snapshots históricos anteriores al inicio. La API archiva los pronósticos nuevos con su contexto para permitir ese trabajo prospectivo.

El candidato CatBoost usa exclusivamente características históricas del bateador. Entrenamiento, calibración logística y prueba se separan por fechas completas; las predicciones del conjunto de prueba no ajustan parámetros. El candidato se guarda para investigación y no reemplaza automáticamente el modelo servido. Consulta [Evaluación histórica](evaluacion.md).
