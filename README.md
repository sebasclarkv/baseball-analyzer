# ⚾ Analizador de Probabilidades de Bateo

<div align="center">

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![MLB](https://img.shields.io/badge/MLB-Statcast_API-red.svg)

**Herramienta profesional con interfaz gráfica para analizar probabilidades de bateo de jugadores de MLB**

Utiliza datos reales de Statcast para generar predicciones basadas en estadísticas descriptivas

</div>

---

## 🌟 Características

- 📊 **Análisis Completo**: Probabilidades de HIT, HOME RUN, BB, K, OUT y OUT sin swing
- 🔮 **Predicciones Inteligentes**: Estimaciones para el próximo juego basadas en datos históricos
- 🎯 **Datos Reales**: Integración directa con la API de Statcast de MLB
- 🔧 **Personalizable**: Ajusta la cantidad de juegos a analizar (1-162)
- 📋 **Exportable**: Copia los resultados del análisis al portapapeles
- 🎨 **Diseño Moderno**: Interfaz elegante con colores suaves y sombreados
- 🖥️ **Fácil de Usar**: Interfaz gráfica intuitiva y amigable

## 📸 Vista Previa

![Screenshot](screenshot.png)
*Interfaz moderna y profesional del analizador*

---

## 🚀 Instalación

### Requisitos Previos
- Python 3.8 o superior
- pip (gestor de paquetes de Python)
- Conexión a Internet (para obtener datos de MLB)

### Pasos de Instalación

1. **Clona este repositorio**
```bash
git clone https://github.com/sebastianclarkv/baseball-analyzer.git
cd baseball-analyzer
```

2. **Instala las dependencias**
```bash
pip install -r requirements.txt
```

3. **Ejecuta la aplicación**
```bash
python baseball_analyzer.py
```

---

## 📖 Guía de Uso

### Inicio Rápido

1. **Ingresa el nombre del bateador**
   - Ejemplo: "Aaron Judge", "Shohei Ohtani", "Fernando Tatis Jr"
   - Usa el formato: Nombre Apellido

2. **Selecciona la cantidad de juegos**
   - Usa el selector numérico para elegir cuántos juegos recientes analizar
   - Rango: 1-162 juegos (por defecto: 20)

3. **Analiza**
   - Haz clic en el botón "🔍 Analizar"
   - La aplicación obtendrá los datos de Statcast
   - Espera mientras se procesan las estadísticas

4. **Revisa los resultados**
   - Probabilidades por aparición al plato
   - Predicción para el próximo juego
   - Estadísticas históricas detalladas
   - Métricas avanzadas (velocidad de salida, ángulo de lanzamiento)

5. **Exporta los resultados**
   - Haz clic en "📋 Copiar Resultados"
   - Los datos se copiarán al portapapeles
   - Pégalos donde los necesites

---

## 📊 Información que Proporciona

### 🎯 Probabilidades por Aparición al Plato
| Métrica | Descripción |
|---------|-------------|
| **HIT** | Probabilidad de conectar un hit (single, doble, triple o HR) |
| **HOME RUN** | Probabilidad específica de conectar un jonrón |
| **BB** | Probabilidad de recibir base por bolas |
| **K** | Probabilidad de ponche |
| **OUT** | Probabilidad de hacer out |
| **OUT sin swing** | Probabilidad de ponche observando |

### 🔮 Predicción para el Próximo Juego
- **Apariciones esperadas**: Basado en el promedio histórico del jugador
- **Hits esperados**: Estimación de hits en el próximo partido
- **Home Runs esperados**: Estimación de jonrones probables
- **Bases por Bolas esperadas**: BB probables
- **Ponches esperados**: K probables
- **Outs esperados**: Outs probables

### 📋 Estadísticas Históricas
- Total de apariciones en el plato (período seleccionado)
- Conteos detallados por tipo de evento
- Ponches observando
- Promedio de apariciones por juego

### ⚙️ Métricas Avanzadas
- **Exit Velocity**: Velocidad promedio de salida de la bola (mph)
- **Launch Angle**: Ángulo de lanzamiento promedio (grados)

---

## 🛠️ Tecnologías Utilizadas

- **Python 3.8+**: Lenguaje de programación principal
- **tkinter**: Interfaz gráfica de usuario
- **pybaseball**: API para datos de MLB Statcast
- **pandas**: Análisis y manipulación de datos
- **numpy**: Cálculos numéricos

---

## ⚠️ Notas Importantes

- 📡 Los datos provienen de la API oficial de Statcast de MLB
- ⏱️ La primera ejecución puede tardar un momento (descarga de caché)
- 🔄 Los datos se actualizan diariamente
- ✍️ Los reportes incluyen la firma @sebastianclarkv

---

## 🐛 Solución de Problemas

### Error: ModuleNotFoundError
```bash
pip install -r requirements.txt
```

### Error con tkinter en macOS
```bash
brew install python-tk
```

### Error con tkinter en Linux
```bash
sudo apt-get install python3-tk
```

### No se encuentran datos del jugador
- ✅ Verifica la ortografía del nombre
- ✅ Usa el formato "Nombre Apellido"
- ✅ Verifica tu conexión a Internet
- ✅ Asegúrate de que el jugador esté activo en MLB

---

## 📝 Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo [LICENSE](LICENSE) para más detalles.

---

## 👤 Autor

**Sebastian Clark**
- GitHub: [@sebastianclarkv](https://github.com/sebastianclarkv)
- Twitter: [@sebastianclarkv](https://twitter.com/sebastianclarkv)

---

## 🤝 Contribuciones

Las contribuciones son bienvenidas! Si tienes alguna idea o encuentras un bug:

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/AmazingFeature`)
3. Commit tus cambios (`git commit -m 'Add some AmazingFeature'`)
4. Push a la rama (`git push origin feature/AmazingFeature`)
5. Abre un Pull Request

---

## ⭐ Agradecimientos

- MLB Statcast por proporcionar los datos
- Comunidad de pybaseball
- Todos los contribuidores y usuarios

---

<div align="center">

**Si te gusta este proyecto, dale una ⭐!**

 Made with ❤️ by Sebastian Clark

</div>
