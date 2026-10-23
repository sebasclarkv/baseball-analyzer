# ⚾ Diamond IQ · Baseball Analyzer 2.0

**Del historial al siguiente turno.** Diamond IQ convierte datos de Statcast en proyecciones de bateo explicables, con contexto de forma reciente y matchup.

Funciona en la web, iPhone y Android desde una sola interfaz adaptable. El servidor puede quedarse en tu computadora y, en navegadores compatibles, la web se instala como PWA. No requiere cuenta, claves de API ni servicios de pago.

[Inicio rápido](#empezar-en-una-computadora) · [Uso](#uso-del-analizador) · [Modelo estadístico](#cómo-razona-la-proyección) · [Acceso móvil](#abrirlo-en-iphone-o-android-dentro-de-la-misma-red) · [Contribuir](#contribuciones)

**Tecnología:** Python · FastAPI · Uvicorn · HTML/CSS/JavaScript · pybaseball · pandas · NumPy. Sin Node.js, base de datos ni bibliotecas de interfaz desde CDN.

## En el diamante

- Búsqueda de jugadores MLB por nombre y apellido en ese orden (por ejemplo, `Aaron Judge`) o por apellido; selecciona la coincidencia correcta en la lista.
- Historial de hasta tres años separado de la forma reciente, tendencias por juego y detalle de apariciones al plato.
- Matchup opcional contra un pitcher rival, con split por mano y tasas permitidas de hits, HR, BB, K y outs.
- Proyección de hits, HR, BB, ponches y apariciones para el siguiente juego.
- Tasas ajustadas, incertidumbre, tamaño efectivo de muestra y una explicación legible del modelo.
- Validación temporal por jugador contra juegos posteriores no usados por el pronóstico, comparada con una referencia de tasa reciente.
- xBA, velocidad de salida, contacto duro, ángulo de lanzamiento y barrel rate cuando Statcast los informa.
- Reportes recientes guardados en el navegador del dispositivo para volver a abrirlos, incluso si la red se cae.
- Interfaz PWA instalable y archivos de la aplicación disponibles sin conexión después de la primera carga HTTPS.
- FastAPI con documentación interactiva en `/docs`.

## Empezar en una computadora

Se requiere Python 3.10 o posterior. Clona el repositorio y prepara el entorno:

```sh
git clone https://github.com/sebasclarkv/baseball-analyzer.git
cd baseball-analyzer
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python server.py
```

En Windows PowerShell, cambia las dos rutas `.venv/bin/python` por `.venv\Scripts\python.exe`.

Abre `http://127.0.0.1:8000`. El servidor escucha solo en tu computadora con esta configuración.

## Abrirlo en iPhone o Android dentro de la misma red

1. Conecta la computadora y el móvil a la misma red Wi-Fi.
2. Inicia el servidor para aceptar conexiones de esa red:

   ```sh
   .venv/bin/python server.py --host 0.0.0.0
   ```

   En Windows usa `.venv\Scripts\python.exe server.py --host 0.0.0.0`.
3. Averigua la dirección IP privada de la computadora. En macOS puedes usar `ipconfig getifaddr en0`; en Linux, `hostname -I`; en Windows, `ipconfig` y buscar la dirección IPv4 del adaptador Wi-Fi.
4. En Safari o Chrome del móvil, abre `http://IP-DE-LA-COMPUTADORA:8000`, por ejemplo `http://192.168.1.24:8000`.
5. Si el sistema lo pregunta, permite conexiones de Python en la red privada. No publiques el puerto en el router.

El modo LAN sirve la web y la API desde tu computadora; las consultas nuevas siguen necesitando que esa computadora tenga Internet para descargar Statcast. El móvil no se conecta directamente a Baseball Savant. El servidor no tiene inicio de sesión: limita el acceso a una red privada y confiable.

Fuera de esa Wi-Fi, conecta ambos dispositivos a la misma VPN privada (por ejemplo, Tailscale). Inicia el servidor enlazándolo a la IP VPN de la computadora, con `.venv/bin/python server.py --host IP-TAILSCALE`, y abre `http://IP-TAILSCALE:8000` en el móvil. No abras el puerto en Internet ni lo publiques en el router. La computadora debe permanecer encendida con el servidor activo.

### Instalar como app

La interfaz se adapta a móviles aunque se abra en el navegador. Para instalarla y usar la caché PWA sin conexión, el navegador requiere una conexión HTTPS. `localhost` se considera segura en la computadora, pero una dirección LAN como `http://192.168.x.x:8000` no. En iOS, abre el sitio con Safari y usa **Compartir → Añadir a pantalla de inicio**. En Android, usa el menú de Chrome y elige **Instalar aplicación** o **Añadir a pantalla de inicio**.

La instalación no incluye un backend en el teléfono: el servidor Python debe seguir ejecutándose en la computadora. En una URL LAN HTTP puedes usar la web normalmente, aunque el modo PWA offline no se habilite.

## Uso del analizador

1. Escribe nombre y apellido en ese orden (por ejemplo, `Aaron Judge`), o busca solo por apellido. Selecciona al bateador correcto de la lista; escribir el nombre sin elegir una coincidencia no inicia el análisis.
2. Opcionalmente selecciona el pitcher rival confirmado o probable; sin él se calcula el perfil del bateador.
3. Elige cuántos juegos usar para la forma reciente y un historial de hasta tres años.
4. Inicia el análisis. La consulta inicial puede tardar mientras Statcast descarga los datos del bateador y, si elegiste un rival, los del pitcher. pybaseball conserva la descarga en caché local.
5. Lee la proyección, los factores de matchup, la tendencia por juego, las métricas de contacto y la validación histórica del modelo base.
6. Copia el reporte o vuelve a abrir uno de los reportes guardados en ese navegador.

Los reportes se guardan en el almacenamiento local del navegador, no se sincronizan entre dispositivos y pueden quedar desactualizados. Con PWA instalada desde HTTPS, la caché puede abrir la aplicación y los reportes guardados sin conexión. En la URL LAN HTTP la web requiere que el servidor esté activo. Ninguna opción genera datos actuales sin conexión a Internet y a Statcast.

## Cómo razona la proyección

El selector permite consultar hasta tres años de Statcast y hasta 150 juegos para estimar la forma. El historial largo estima el nivel del jugador; cada aparición pesa menos cuanto más antigua es, con vida media de 365 días. Excluye los juegos reservados para la lectura de forma reciente, para no contar dos veces las mismas apariciones. Un prior MLB de 45 PA estabiliza las tasas cuando el historial es corto o incompleto. El prior de hits es 0.220 por aparición al plato (no el promedio de bateo por turno), de modo que su denominador coincide con H/PA. Así se aprovecha más carrera sin tratar una temporada vieja como si describiera exactamente al jugador de hoy.

La forma reciente se calcula aparte sobre los juegos seleccionados, con vida media de 45 días. Se suaviza primero hacia el nivel de largo plazo y luego su influencia crece solo con el tamaño efectivo de la muestra, hasta un máximo de 45%: una racha reciente puede mover la estimación, pero no reemplaza años de evidencia por unos pocos juegos. Los indicadores de contacto (xBA, EV, contacto duro, ángulo y barrel) se resumen con mayor peso reciente; xBA sí entra directamente en H/PA para bolas bateadas rastreadas. EV, ángulo y barrel se muestran como evidencia de contexto, sin sumarlos otra vez como términos independientes.

Si seleccionas el pitcher rival, el modelo consulta su historial Statcast dentro de la misma ventana. Ajusta hits, HR, BB, K y outs permitidos hacia el promedio MLB con un prior de 120 PA. También toma la mano dominante del pitcher y estima los splits históricos del bateador contra lanzadores zurdos o derechos; ese split se contrae hacia la tasa general con un prior de 80 PA. Finalmente combina la capacidad del bateador y lo que permite el pitcher en escala log5 (odds del bateador × odds permitidas por el pitcher ÷ odds MLB). De esta forma un rival débil o fuerte mueve la expectativa de manera explícita, y muestras cortas tienen menos efecto.

El pronóstico de hits esperados multiplica la tasa ajustada por la distribución reciente de apariciones al plato. La probabilidad de 1+ hit y HR mezcla esa distribución por juego e integra la incertidumbre de la tasa con una predicción beta-binomial; no trata la tasa estimada como una constante exacta. La banda es aproximada y combina incertidumbre tipo Wilson al 90% con la variación de PA. La tabla año por año permite revisar H/PA observado frente a H/PA ajustado por xBA, además de HR, K y velocidad de salida. La tasa de outs incluye ponches; K se muestra aparte como subconjunto, así que esas tasas no se deben sumar.

El panel “Qué mueve la proyección” separa el nivel de largo plazo, el cambio de forma, el split por mano y el aporte del pitcher en puntos porcentuales de H/PA. Si no eliges rival o Baseball Savant no devuelve sus apariciones, el analizador conserva la estimación del bateador y lo indica.

La validación temporal vuelve a pronosticar hasta 25 juegos pasados usando únicamente datos anteriores, con el historial completo disponible en cada fecha. Compara el MAE de hits con la tasa simple de los últimos juegos, y muestra Brier, error de PA y calibración. Evalúa el modelo base del bateador; no aplica al abridor que selecciones para un juego futuro. Es una evaluación por jugador, no demuestra precisión general ni garantiza el resultado de un partido.

El modelo no obtiene una alineación probable ni condiciones futuras de parque, clima, lesión, bullpen o uso del lanzador. El pitcher debe seleccionarse manualmente y puede cambiar antes del juego. Statcast puede entregar xBA incompleto; el informe muestra cuántos contactos contribuyeron. Todos los resultados son estimaciones estadísticas y no asesoría de apuestas.

## Acceso y dependencias

- La aplicación y el almacenamiento de reportes se quedan en la computadora y el navegador elegidos.
- Se necesita Internet para instalar dependencias, descargar el registro de jugadores y consultar Statcast.
- Las fuentes de datos públicas son el registro de Chadwick Bureau y Baseball Savant/Statcast. No tienen SLA de este proyecto y pueden limitar solicitudes.
- No se usan bibliotecas de interfaz desde CDN, cuentas pagadas ni credenciales secretas.
- Para cambiar puerto: `.venv/bin/python server.py --port 8080`. Para desarrollo: añade `--reload`.

## Desarrollo y versión anterior

La web usa HTML, CSS, JavaScript, FastAPI y Uvicorn; los cálculos usan pybaseball, pandas y NumPy. No requiere Node.js ni una base de datos. Para recarga automática:

```sh
.venv/bin/python server.py --reload
```

La aplicación de escritorio Tkinter original sigue en `baseball_analyzer.py`; `server.py` inicia Diamond IQ 2.0.

## Autor

**Sebastian Clark**

- GitHub: [@sebasclarkv](https://github.com/sebasclarkv)
- X: [@sebasclarkv](https://x.com/sebasclarkv)

## Contribuciones

Las ideas, mejoras del modelo y reportes de errores son bienvenidos. Para proponer un cambio:

1. Haz un fork del proyecto.
2. Crea una rama: `git checkout -b feature/mi-mejora`.
3. Guarda tus cambios: `git commit -m "Describe la mejora"`.
4. Sube la rama: `git push origin feature/mi-mejora`.
5. Abre un Pull Request con el motivo del cambio y los detalles relevantes.

## Agradecimientos

- Baseball Savant/Statcast y Chadwick Bureau por los datos públicos.
- La comunidad de `pybaseball` por las herramientas de acceso a Statcast.
- Quienes prueban, usan y contribuyen a este proyecto.

## Privacidad y licencia

No hay cuenta ni analítica de uso. Las búsquedas y resultados se procesan en el servidor local. Los reportes quedan en el almacenamiento local del navegador y puedes borrarlos limpiando los datos del sitio. Las marcas y datos de MLB pertenecen a sus titulares. Este proyecto conserva la licencia MIT; consulta `LICENSE`.
