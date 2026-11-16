# Ualett Payroll — App Streamlit

Interfaz web local para generar la nómina desde Odoo, elegir el periodo, ver la tabla y descargar los archivos.

## Cómo abrirla

- **Doble clic** en `Iniciar_Payroll_App.bat`, **o**
- en una terminal, dentro de esta carpeta: `python -m streamlit run app.py`

Se abre en el navegador en `http://localhost:8501`. Para cerrarla, cierra la ventana negra de la terminal.

> La app solo es accesible desde tu computadora (`address = "localhost"` en `.streamlit/config.toml`). Así nadie más en la red puede ver la nómina ni usar tus credenciales de Odoo.

## Requisitos

- Python con `streamlit`, `pandas` y `openpyxl` (ya están instalados en este equipo).
- `../config.json` con las credenciales de Odoo (el mismo de siempre).
- La carpeta hermana `../Attendance_Report_Horario_Odoo`. De ahí salen las reglas de cálculo de cada día. **No mover ni renombrar.**

## Uso

1. En la barra lateral, elige el **periodo** con el calendario o con los atajos (`1–15` del mes actual / `16–fin` del mes anterior). Por defecto aparece la última quincena completa.
2. Presiona **🚀 Generar nómina**. Tarda entre 30 y 60 segundos: lee Odoo y los Google Sheets.
3. Revisa los indicadores y el aviso de alertas.
4. Descarga:

| Botón | Para quién | Contenido |
|---|---|---|
| **⬇️ Descargar para Finanzas** | Finanzas | `Payroll_<inicio>_to_<fin>.xlsx`: solo las columnas `LOB` → `Total (decimal)` |
| **🧑‍💻 Reporte del analista** | Analista | `Payroll_<inicio>_to_<fin>_ANALISTA.xlsx`: todas las columnas (días trabajados, No Show, tardanzas, alertas, email, horario de Odoo) + hojas `Revisar`, `Detalle` y `Feriado Clock In` |

### Columnas del archivo de Finanzas
`LOB, Full Name, Regular_Hours, Vacation_Hours, holiday_Hours, Total Worked Hours, Regular (decimal), Vacation (decimal), Holiday (decimal), Total (decimal)`

### Pestañas

| Pestaña | Contenido |
|---|---|
| 💰 Nómina | Tabla con buscador por nombre, filtro de LOB y vista Finanzas / Analista |
| ⚠️ Revisar | Alertas filtrables por severidad, con un resumen por tipo |
| 📋 Detalle por empleado | Cada día de la persona: horario, marcación real, marcación usada para pagar y horas pagadas |
| 🎉 Feriado Clock In | Quién marcó en los feriados del periodo |

### Configuración avanzada (barra lateral)
LOBs incluidos, nombres excluidos y modo de feriados. Los valores por defecto son los mismos del notebook. Los cambios hechos aquí **no se guardan**: para cambiarlos de forma permanente, edita `DEFAULT_LOBS` y `DEFAULT_EXCLUIDOS` en `payroll_core.py`.

**🔄 Refrescar datos de Odoo:** los resultados se guardan en memoria 15 minutos. Si corriges marcaciones o horarios en Odoo, presiona este botón y vuelve a generar.

## Archivos

| Archivo | Qué es |
|---|---|
| `app.py` | La interfaz |
| `payroll_core.py` | La lógica de nómina (igual que `Payroll_Mejorado.ipynb`) y la generación del Excel |
| `.streamlit/config.toml` | Tema visual y seguridad (solo localhost) |
| `Iniciar_Payroll_App.bat` | Acceso directo para abrir la app |

## Reglas de negocio

Son las mismas del notebook `../Payroll_Nomina_Mejorado/Payroll_Mejorado.ipynb`; ver su `CAMBIOS.md`. En resumen:
- La entrada cuenta como máximo 5 min antes del horario.
- La salida se topa al horario.
- Se descuenta el almuerzo mayor entre el real y el programado.
- Las vacaciones pagan las horas programadas del día.
- Feriado: 8h si no trabajó. Con licencia médica no se paga. Una vacación en feriado cuenta solo como feriado.
- Redondeo a partir de 40 segundos.

## Prueba realizada (2026-11-05)

Periodo 1–15 oct. 2026: 55 empleados, total **4,316:10 h**, el mismo resultado que el notebook. Se comprobó lo siguiente:
- El Excel de Finanzas trae exactamente las 10 columnas.
- La vista Analista muestra las 16 columnas.
- Los atajos de fecha funcionan.
- El servidor arranca sin errores.

**Actualización 2026-11-06:** el horario de cada día ahora sale del **Active Schedule** de la marcación en Odoo (si no marcó, del horario de la ficha). Octubre queda en **4,486.57 h**, sin alertas Alta. Para cambiar el horario de un solo día, edita el Active Schedule de esa marcación en Asistencias.
