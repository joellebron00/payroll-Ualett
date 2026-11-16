"""
Ualett Payroll — interfaz Streamlit
Ejecutar:  streamlit run app.py      (o doble clic en Iniciar_Payroll_App.bat)
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import payroll_core as core

st.set_page_config(page_title='Ualett Payroll', page_icon='💼', layout='wide',
                   initial_sidebar_state='expanded')

# ══════════════ ESTILO ══════════════
st.markdown("""
<style>
.block-container {padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1400px;}
.hero {
    background: linear-gradient(120deg, #1E3A8A 0%, #2563EB 55%, #0EA5E9 100%);
    border-radius: 18px; padding: 26px 32px; color: #fff; margin-bottom: 22px;
    box-shadow: 0 10px 30px rgba(30,58,138,.25);
}
.hero h1 {color:#fff; font-size: 2rem; margin: 0 0 4px 0; font-weight: 800; letter-spacing:-.5px;}
.hero p  {color: rgba(255,255,255,.88); margin: 0; font-size: 1rem;}
.hero .pill {display:inline-block; background: rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.35);
             padding: 4px 12px; border-radius: 999px; font-size: .85rem; margin-top: 12px; margin-right: 6px;}
[data-testid="stMetric"] {background: var(--secondary-background-color); border-radius: 14px; padding: 14px 18px;}
[data-testid="stMetricValue"] {font-size: 1.7rem; font-weight: 700;}
.section-title {font-size: 1.15rem; font-weight: 700; margin: 6px 0 2px 0;}
.muted {color: #64748B; font-size: .9rem;}
.empty {text-align:center; padding: 70px 20px; border: 2px dashed #CBD5E1; border-radius: 18px; color:#64748B;}
.empty h3 {color:#1E3A8A; margin-bottom: 6px;}
div.stDownloadButton > button {border-radius: 10px; font-weight: 600; padding: .55rem 1rem;}
section[data-testid="stSidebar"] .stButton > button {border-radius: 10px; font-weight: 700; height: 3rem;}
</style>
""", unsafe_allow_html=True)


# ══════════════ FUNCIONES ══════════════
def ultima_quincena(hoy: date):
    """Última quincena completa: si hoy es 1-15 -> 16 a fin del mes pasado; si no -> 1 a 15 del mes actual."""
    if hoy.day <= 15:
        fin = hoy.replace(day=1) - timedelta(days=1)
        return fin.replace(day=16), fin
    return hoy.replace(day=1), hoy.replace(day=15)


@st.cache_data(ttl=900, show_spinner=False)
def generar(start, end, lobs, excluidos, holiday_mode):
    return core.compute_payroll(start, end, list(lobs), list(excluidos), holiday_mode)


def hhmm_total(horas_decimal):
    minutos = int(round(horas_decimal * 60))
    return f'{minutos // 60:,}:{minutos % 60:02d}'


# ══════════════ SIDEBAR ══════════════
with st.sidebar:
    st.markdown('## 💼 Ualett Payroll')
    st.caption('Nómina calculada directamente desde Odoo')
    st.divider()

    if 'rango' not in st.session_state:
        st.session_state['rango'] = ultima_quincena(date.today())

    def _set_rango(r):
        st.session_state['rango'] = r

    hoy = date.today()
    fin_mes_ant = hoy.replace(day=1) - timedelta(days=1)
    st.caption('Atajos')
    c1, c2 = st.columns(2)
    c1.button(f'1–15 {hoy:%b}', width='stretch', on_click=_set_rango,
              args=((hoy.replace(day=1), hoy.replace(day=15)),), help='Primera quincena del mes actual')
    c2.button(f'16–{fin_mes_ant.day} {fin_mes_ant:%b}', width='stretch', on_click=_set_rango,
              args=((fin_mes_ant.replace(day=16), fin_mes_ant),), help='Segunda quincena del mes anterior')

    rango = st.date_input('📅 Periodo de nómina', key='rango', format='MM/DD/YYYY',
                          help='Selecciona la fecha de inicio y la fecha de fin (ambas incluidas).')

    with st.expander('⚙️ Configuración avanzada'):
        lobs = st.multiselect('LOBs incluidos', options=sorted(set(core.DEFAULT_LOBS)),
                              default=core.DEFAULT_LOBS, accept_new_options=True)
        excl_txt = st.text_area('Nombres excluidos (uno por línea)', value='\n'.join(core.DEFAULT_EXCLUIDOS), height=180)
        holiday_mode = st.selectbox('Feriados', ['auto', 'yes', 'no'],
                                    format_func={'auto': 'Automático (Odoo)', 'yes': 'Todo el periodo es feriado',
                                                 'no': 'Ignorar feriados'}.get)

    st.write('')
    generar_btn = st.button('🚀 Generar nómina', type='primary', width='stretch')
    if st.button('🔄 Refrescar datos de Odoo', width='stretch',
                 help='Borra la memoria temporal y vuelve a leer Odoo (útil si se corrigieron marcaciones).'):
        generar.clear()
        st.toast('Memoria limpia. Vuelve a generar la nómina.', icon='🔄')

    st.divider()
    st.caption('Reglas: entrada máx. 5 min antes · salida topada al horario · almuerzo mayor entre real y programado · '
               'feriado 8h si no trabajó · redondeo ≥ 40 s.')


# ══════════════ ENCABEZADO ══════════════
if isinstance(rango, (tuple, list)) and len(rango) == 2:
    start, end = rango
else:
    start, end = None, None

periodo_txt = f'{start:%d %b %Y} → {end:%d %b %Y}' if start else 'Selecciona un rango de fechas'
st.markdown(f"""
<div class="hero">
  <h1>Payroll · Nómina</h1>
  <p>Horas Regulares, Vacaciones y Feriados por empleado, calculadas desde Odoo.</p>
  <span class="pill">📅 {periodo_txt}</span>
  <span class="pill">🏢 {len(lobs)} LOBs</span>
</div>
""", unsafe_allow_html=True)


# ══════════════ GENERAR ══════════════
if generar_btn:
    if not start:
        st.error('Selecciona la fecha de inicio **y** la fecha de fin.')
    elif end < start:
        st.error('La fecha de fin es anterior a la de inicio.')
    elif not lobs:
        st.error('Selecciona al menos un LOB.')
    else:
        excluidos = tuple(n.strip() for n in excl_txt.splitlines() if n.strip())
        with st.status('Generando nómina…', expanded=True) as status:
            st.write('🔐 Conectando con Odoo y leyendo marcaciones, horarios, permisos y feriados…')
            try:
                res = generar(start, end, tuple(lobs), excluidos, holiday_mode)
                st.session_state['res'] = res
                st.session_state['params'] = (start, end)
                status.update(label='Nómina generada ✅', state='complete', expanded=False)
            except Exception as e:  # noqa: BLE001
                status.update(label='No se pudo generar la nómina', state='error')
                st.error(f'❌ {e}')

res = st.session_state.get('res')
if res is None:
    st.markdown("""
    <div class="empty">
      <h3>Elige el periodo y presiona <b>🚀 Generar nómina</b></h3>
      <p>La tabla aparecerá aquí, con los botones para descargar el archivo de Finanzas y el del analista.</p>
    </div>""", unsafe_allow_html=True)
    st.stop()

p_start, p_end = st.session_state['params']
if (start, end) != (p_start, p_end):
    st.info(f'Mostrando la nómina de **{p_start:%m/%d/%Y} → {p_end:%m/%d/%Y}**. '
            'Cambiaste el periodo: presiona **Generar nómina** para actualizar.')

payroll = res['payroll']
revisar = res['revisar']
n_alta = int((revisar['Severidad'] == 'Alta').sum())

# ══════════════ KPIs ══════════════
k = st.columns(5)
k[0].metric('👥 Empleados', f"{len(payroll):,}", border=True)
k[1].metric('⏱️ Total horas', hhmm_total(payroll['Total (decimal)'].sum()), border=True)
k[2].metric('🗓️ Regulares', hhmm_total(payroll['Regular (decimal)'].sum()), border=True)
k[3].metric('🏖️ Vacaciones', hhmm_total(payroll['Vacation (decimal)'].sum()), border=True)
k[4].metric('🎉 Feriado', hhmm_total(payroll['Holiday (decimal)'].sum()), border=True)

if res['feriados']:
    st.caption('Feriados en el periodo: ' + ' · '.join(f'**{d:%m/%d}** {n}' for d, n in res['feriados']))

if n_alta:
    st.warning(f'**{n_alta} alertas de severidad ALTA.** Revísalas en la pestaña **⚠️ Revisar** antes de enviar a Finanzas.',
               icon='⚠️')
else:
    st.success('Sin alertas de severidad alta.', icon='✅')

# ══════════════ DESCARGAS ══════════════
nombre_base = f'Payroll_{p_start:%Y%m%d}_to_{p_end:%Y%m%d}'
dec_cols = ['Regular (decimal)', 'Vacation (decimal)', 'Holiday (decimal)', 'Total (decimal)']

finanzas_xlsx = core.to_excel_bytes({'Payroll': payroll[core.FINANCE_COLUMNS]}, money_cols=dec_cols)
analista_sheets = {'Payroll': payroll, 'Revisar': revisar, 'Detalle': res['detalle']}
if len(res['clock_in_feriado']):
    analista_sheets['Feriado Clock In'] = res['clock_in_feriado']
analista_xlsx = core.to_excel_bytes(analista_sheets, money_cols=dec_cols)

b1, b2, _ = st.columns([1.2, 1.2, 2])
b1.download_button('⬇️ Descargar para Finanzas', data=finanzas_xlsx, file_name=f'{nombre_base}.xlsx',
                   mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                   type='primary', width='stretch',
                   help='Columnas desde LOB hasta Total (decimal).')
b2.download_button('🧑‍💻 Reporte del analista', data=analista_xlsx, file_name=f'{nombre_base}_ANALISTA.xlsx',
                   mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                   width='stretch', help='Todas las columnas + hojas Revisar, Detalle y Feriado Clock In.')

st.write('')

# ══════════════ PESTAÑAS ══════════════
tabs = st.tabs(['💰 Nómina', f'⚠️ Revisar ({len(revisar)})', '📋 Detalle por empleado', '🎉 Feriado Clock In'])

money_cfg = {c: st.column_config.NumberColumn(c, format='%.2f') for c in dec_cols}

with tabs[0]:
    f1, f2, f3 = st.columns([2, 2, 1.3])
    buscar = f1.text_input('🔎 Buscar empleado', placeholder='Nombre…')
    lob_f = f2.multiselect('Filtrar LOB', sorted(payroll['LOB'].unique()))
    vista = f3.radio('Vista', ['Finanzas', 'Analista'], horizontal=True,
                     help='Finanzas = lo que se descarga para Finanzas. Analista = todas las columnas.')
    t = payroll.copy()
    if buscar:
        t = t[t['Full Name'].str.contains(buscar.strip().upper(), regex=False)]
    if lob_f:
        t = t[t['LOB'].isin(lob_f)]
    if vista == 'Finanzas':
        t = t[core.FINANCE_COLUMNS]
    cfg = dict(money_cfg)
    if 'Alertas' in t:
        cfg['Alertas'] = st.column_config.NumberColumn('Alertas', help='Alertas Alta/Media de esta persona', format='%d ⚠️')
    st.dataframe(t, hide_index=True, width='stretch', height=520, column_config=cfg)
    st.caption(f'{len(t)} de {len(payroll)} empleados · Total filtrado: '
               f'**{hhmm_total(t["Total (decimal)"].sum())} h** ({t["Total (decimal)"].sum():,.2f})')

with tabs[1]:
    if revisar.empty:
        st.success('No hay alertas. 🎉')
    else:
        sev = st.segmented_control('Severidad', ['Alta', 'Media', 'Baja'], selection_mode='multi',
                                   default=['Alta', 'Media'])
        r = revisar[revisar['Severidad'].isin(sev or [])]
        resumen = r.groupby(['Severidad', 'Tipo'], sort=False).size().reset_index(name='Cantidad')
        cA, cB = st.columns([1, 2.4])
        cA.markdown('<div class="section-title">Resumen</div>', unsafe_allow_html=True)
        cA.dataframe(resumen, hide_index=True, width='stretch')
        cB.markdown('<div class="section-title">Alertas</div>', unsafe_allow_html=True)
        cB.dataframe(r, hide_index=True, width='stretch', height=460,
                     column_config={'Detalle': st.column_config.TextColumn(width='large')})

with tabs[2]:
    persona = st.selectbox('Empleado', sorted(payroll['Full Name']), index=None, placeholder='Selecciona un empleado…')
    if persona:
        fila = payroll[payroll['Full Name'] == persona].iloc[0]
        m = st.columns(5)
        m[0].metric('Regular', fila['Regular_Hours'])
        m[1].metric('Vacaciones', fila['Vacation_Hours'])
        m[2].metric('Feriado', fila['holiday_Hours'])
        m[3].metric('Total', fila['Total Worked Hours'])
        m[4].metric('Horario Odoo', fila['Odoo Schedule'] or '—')
        st.dataframe(res['detalle'][res['detalle']['Full Name'] == persona], hide_index=True, width='stretch')
        alert_p = revisar[revisar['Full Name'] == persona]
        if len(alert_p):
            st.markdown('<div class="section-title">Alertas de esta persona</div>', unsafe_allow_html=True)
            st.dataframe(alert_p, hide_index=True, width='stretch')
    else:
        st.markdown('<p class="muted">Muestra cada día: horario, marcación real, marcación usada para pagar y horas pagadas.</p>',
                    unsafe_allow_html=True)

with tabs[3]:
    cf = res['clock_in_feriado']
    if cf.empty:
        st.info('Nadie marcó en feriado durante este periodo (o no hubo feriados).')
    else:
        st.dataframe(cf, hide_index=True, width='stretch')
