"""
Lógica de nómina (sin interfaz). Misma lógica que Payroll_Nomina_Mejorado/Payroll_Mejorado.ipynb.

Las horas de cada día se calculan con attendance_report_odoo.load_period() (carpeta hermana
Attendance_Report_Horario_Odoo), así nómina y Attendance Report usan las mismas reglas de Ualett.
"""

import io
import os
import sys

import numpy as np
import pandas as pd

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ATT_DIR = os.path.join(os.path.dirname(APP_DIR), 'Attendance_Report_Horario_Odoo')
if ATT_DIR not in sys.path:
    sys.path.insert(0, ATT_DIR)
import attendance_report_odoo as att  # noqa: E402

# ── Valores por defecto (los mismos del notebook) ────────────────────────────
DEFAULT_LOBS = ['MULTIFUNCTIONS', 'ONBOARDING', 'RELATIONSHIP MANAGEMENT',
                'ACCOUNT MANAGEMENT', 'INBOUND & CHAT', 'SALES']

DEFAULT_EXCLUIDOS = ['JUAN MIGUEL MENDEZ',
                     'KIMBERLY MILDRED NUÑEZ GAUTREAUX', 'ADISON LUIS DUVERGE HERNANDEZ',
                     'DARIANA LUCIANO CARRASCO', 'STARLING RAFAEL HEREDIA INOA',
                     'YAEL JOHANNY CARO MARTINEZ', 'SAMUEL ENRIQUE SOLIS OZORIA',
                     'ELAINI ENCARNACION MAYNERD', 'JOHNANGEL RAMIREZ GUTIERREZ',
                     'PALMIRA MIGUELINA ECHAVARRIA VARGAS', 'INGRID GONZALEZ ROMERO',
                     'ADRIAN PEÑA PAULINO', 'NEFTALY AGUSTIN MADERA CORONADO', 'CELUMIEL ODILET BALBUENA JAVIER',
                     'EMMANUEL DAVID MARTE DIAZ', 'VICTOR JOEL CONTRERAS MALDONADO', 'ASHLIE GABRIELA VASQUEZ SANTIAGO',
                     'FABIOLA ISABEL CAPARROSA GARCIA', 'ONORIO DE JESUS ALVAREZ DE LEON', 'DARLENE REYNOSO GUTIERREZ']

# ── Reglas de negocio de nómina (Ualett) ─────────────────────────────────────
HORAS_FERIADO = 8
SEGUNDOS_REDONDEO = 40
MAX_HORAS_DIA = 14
UMBRAL_HORARIO_MIN = 30

# Columnas que van a Finanzas (hasta "Total (decimal)"). El resto es para el analista.
FINANCE_COLUMNS = ['LOB', 'Full Name', 'Regular_Hours', 'Vacation_Hours', 'holiday_Hours', 'Total Worked Hours',
                   'Regular (decimal)', 'Vacation (decimal)', 'Holiday (decimal)', 'Total (decimal)']

ZERO = pd.Timedelta(0)


def redondear(td):
    total = int(td.total_seconds())
    resto = total % 60
    total = total + (60 - resto) if resto >= SEGUNDOS_REDONDEO else total - resto
    return pd.Timedelta(seconds=total)


def hhmm(td):
    total_min = int(td.total_seconds() // 60)
    return f'{total_min // 60:02d}:{total_min % 60:02d}'


def _upper(s):
    return s.fillna('').astype(str).str.strip().str.replace(r'\s+', ' ', regex=True).str.upper()


def compute_payroll(start, end, lobs=DEFAULT_LOBS, excluidos=DEFAULT_EXCLUIDOS,
                    holiday_mode='auto', fechas_excluir=()):
    """
    Devuelve un dict con: payroll, revisar, detalle, clock_in_feriado, feriados (DataFrames / dict).
    Lanza RuntimeError si no hay datos o falla la conexión con Odoo.
    """
    issues = []

    def add_issue(sev, tipo, detalle, r=None, accion=''):
        r = r if r is not None else {}
        fecha = r.get('Date', None)
        issues.append({
            'Severidad': sev, 'Tipo': tipo,
            'Fecha': fecha.strftime('%m/%d/%Y') if isinstance(fecha, pd.Timestamp) else '',
            'Full Name': r.get('full_name', ''), 'LOB': r.get('LOB', ''), 'Status': r.get('Status', ''),
            'Detalle': detalle, 'Acción tomada': accion,
        })

    excluir = set(fechas_excluir)
    full, _employees, _roster, _cal, holidays = att.load_period(start, end, holiday_mode, excluir, APP_DIR)

    full['full_name'] = _upper(full['full_name'])
    full['LOB'] = _upper(full['LOB'])
    lobs = {l.strip().upper() for l in lobs}
    excluidos = {n.strip().upper() for n in excluidos if n.strip()}

    for n in sorted(excluidos - set(full['full_name'])):
        add_issue('Baja', 'Nombre excluido no encontrado', f'"{n}" no aparece en el periodo.')

    data = full[full['LOB'].isin(lobs) & ~full['full_name'].isin(excluidos)].copy()
    if data.empty:
        raise RuntimeError('No hay empleados de los LOBs seleccionados en ese periodo.')

    # ── Clasificar horas ─────────────────────────────────────────────────────
    data['worked'] = pd.to_timedelta(data['worked']).fillna(ZERO)
    data['Tipo de hora'] = np.select(
        [data['is_holiday'] & (data['Exception'] == 'Medical License'),
         data['is_holiday'],
         (data['Exception'] == 'Vacation') & ~data['present']],
        ['No paga (Licencia médica en feriado)', 'Feriado', 'Vacaciones'],
        default='Regular')
    data['Horas pago'] = data['worked']
    data.loc[(data['Tipo de hora'] == 'Feriado') & (data['worked'] == ZERO), 'Horas pago'] = pd.Timedelta(hours=HORAS_FERIADO)
    data.loc[data['Tipo de hora'].str.startswith('No paga'), 'Horas pago'] = ZERO

    # ── Validaciones ─────────────────────────────────────────────────────────
    raw_hours = data['clock_out'] - data['clock_in']
    for _, r in data[data['present'] & data['clock_out'].isna()].iterrows():
        add_issue('Alta', 'Sin check-out', f"Entró {att.fmt_clock(r['clock_in_td'])} y no tiene salida: el día se paga 0h",
                  r, 'Corregir la marcación en Odoo')
    for _, r in data[data['present'] & (raw_hours > pd.Timedelta(hours=MAX_HORAS_DIA))].iterrows():
        add_issue('Alta', f'Marcación de más de {MAX_HORAS_DIA} h',
                  f"{att.fmt_clock(r['clock_in_td'])} → {att.fmt_clock(r['clock_out_td'])}", r,
                  f"Se pagó {att.fmt_duration(r['Horas pago'])} (topado al horario)")
    for _, g in data[data['calendar_id'].isna()].groupby('full_name'):
        add_issue('Alta', 'Sin horario en Odoo', f'{len(g)} días sin topes de horario', g.iloc[0],
                  'Asignar horario de trabajo en Odoo')

    c = data[data['present'] & data['scheduled'] & data['clock_out'].notna()].copy()
    if not c.empty:
        c['antes'] = (c['sched_in'] - c['clock_in_td']) > pd.Timedelta(minutes=UMBRAL_HORARIO_MIN)
        c['sale_antes'] = (c['sched_out'] - c['clock_out_td']) > pd.Timedelta(minutes=UMBRAL_HORARIO_MIN)
        c['bruto'] = (c['clock_out_td'] - c['clock_in_td'] - c['eff_lunch']).clip(lower=ZERO)
        c['no_pagado'] = (np.minimum(c['bruto'], c['sched_hours']) - c['worked']).clip(lower=ZERO)
        for _, g in c.groupby('employee_id'):
            patron = (g['antes'] & g['sale_antes']).mean()
            perdidas = g['no_pagado'].sum()
            if patron >= 0.5 and perdidas > pd.Timedelta(hours=1):
                r = g.iloc[0]
                add_issue('Alta', 'Horario de Odoo no coincide con sus marcaciones',
                          f"Odoo: {r['calendar_name']} ({att.fmt_clock(r['sched_in'])}-{att.fmt_clock(r['sched_out'])}). "
                          f"Marca en promedio {att.fmt_clock(g['clock_in_td'].mean())}-{att.fmt_clock(g['clock_out_td'].mean())} "
                          f"en {patron:.0%} de los días. Horas que NO se están pagando: {hhmm(perdidas)}",
                          r, 'Se pagó según Odoo. Corregir el horario en Odoo y volver a generar')

    for _, r in data[(data['Status'] == 'Not Scheduled') & data['calendar_id'].notna()].iterrows():
        add_issue('Media', 'Trabajó en día no programado',
                  f"Su horario ({r['calendar_name']}) no incluye este día. Se pagan las horas reales.", r,
                  f"Se pagó {att.fmt_duration(r['Horas pago'])}")
    for _, r in data[data['present'] & (data['Exception'] == 'Vacation')].iterrows():
        add_issue('Media', 'Marcó estando de vacaciones', 'Vacación aprobada en Odoo y también marcó.', r,
                  'Se pagó lo trabajado como Regular')
    for _, r in data[data['present'] & (data['sessions'] > 1)].iterrows():
        add_issue('Baja', 'Varias sesiones', f"{int(r['sessions'])} marcaciones ese día (se unieron)", r)
    for _, r in data[data['override']].iterrows():
        add_issue('Baja', 'Horario cambiado por Schedule Exception',
                  f"{att.fmt_clock(r['sched_in'])} - {att.fmt_clock(r['sched_out'])}", r)

    presentes = data[data['present']].groupby('Date').size()
    for d in pd.bdate_range(start, end):
        if d.date() in excluir or data.loc[data['Date'] == d, 'is_holiday'].any():
            continue
        if presentes.get(d, 0) == 0:
            add_issue('Alta', 'Día laborable sin marcaciones', f'{d:%A %m/%d/%Y}: nadie marcó en Odoo.', {'Date': d})

    orden = {'Alta': 0, 'Media': 1, 'Baja': 2}
    revisar = pd.DataFrame(issues, columns=['Severidad', 'Tipo', 'Fecha', 'Full Name', 'LOB', 'Status', 'Detalle', 'Acción tomada'])
    revisar = revisar.sort_values(['Severidad', 'Tipo', 'Full Name'],
                                  key=lambda s: s.map(orden) if s.name == 'Severidad' else s).reset_index(drop=True)

    # ── Nómina por empleado ──────────────────────────────────────────────────
    pivot = data.pivot_table(index='employee_id', columns='Tipo de hora', values='Horas pago',
                             aggfunc='sum', fill_value=ZERO)
    for col in ('Regular', 'Vacaciones', 'Feriado'):
        if col not in pivot:
            pivot[col] = ZERO
    info = data.sort_values('Date').groupby('employee_id').agg(
        LOB=('LOB', 'last'), full_name=('full_name', 'last'), email=('email', 'last'),
        calendar=('base_calendar_name', 'last'), dias_trab=('present', 'sum'),
        no_show=('Status', lambda s: (s == 'No Show').sum()),
        tardanzas=('Status', lambda s: (s == 'Late').sum()),
    )
    alertas = revisar[revisar['Severidad'].isin(['Alta', 'Media'])]['Full Name'].value_counts()

    reg = pivot['Regular'].reindex(info.index).fillna(ZERO).apply(redondear)
    vac = pivot['Vacaciones'].reindex(info.index).fillna(ZERO).apply(redondear)
    hol = pivot['Feriado'].reindex(info.index).fillna(ZERO).apply(redondear)
    tot = reg + vac + hol
    dec = lambda s: (s.dt.total_seconds() / 3600).round(2)

    payroll = pd.DataFrame({
        'LOB': info['LOB'], 'Full Name': info['full_name'],
        'Regular_Hours': reg.apply(hhmm), 'Vacation_Hours': vac.apply(hhmm),
        'holiday_Hours': hol.apply(hhmm), 'Total Worked Hours': tot.apply(hhmm),
        'Regular (decimal)': dec(reg), 'Vacation (decimal)': dec(vac),
        'Holiday (decimal)': dec(hol), 'Total (decimal)': dec(tot),
        'Días trabajados': info['dias_trab'].astype(int),
        'No Show': info['no_show'].astype(int),
        'Tardanzas': info['tardanzas'].astype(int),
        'Alertas': info['full_name'].map(alertas).fillna(0).astype(int),
        'Work Email': info['email'], 'Odoo Schedule': info['calendar'],
    }).sort_values(['LOB', 'Full Name']).reset_index(drop=True)

    # ── Detalle y feriados ───────────────────────────────────────────────────
    d = data.sort_values(['full_name', 'Date'])
    detalle = pd.DataFrame({
        'Fecha': d['Date'].dt.strftime('%m/%d/%Y'), 'Full Name': d['full_name'], 'LOB': d['LOB'],
        'Status': d['Status'], 'Exception': d['Exception'], 'Odoo Schedule': d['calendar_name'],
        'Schedule In': d['sched_in'].apply(att.fmt_clock), 'Schedule Out': d['sched_out'].apply(att.fmt_clock),
        'Real Clock In': d['clock_in_td'].apply(att.fmt_clock), 'Real Clock Out': d['clock_out_td'].apply(att.fmt_clock),
        'Clock In (pago)': d['adj_in'].apply(att.fmt_clock), 'Clock Out (pago)': d['adj_out'].apply(att.fmt_clock),
        'Lunch': d['eff_lunch'].apply(lambda x: att.fmt_duration(x, True)),
        'Away': d['away'].apply(lambda x: att.fmt_duration(x, True)),
        'Feriado': np.where(d['is_holiday'], 'Yes', 'No'),
        'Tipo de hora': d['Tipo de hora'], 'Horas pago': d['Horas pago'].apply(att.fmt_duration), 'Note': d['Note'],
    }).reset_index(drop=True)

    fer = data[data['is_holiday'] & data['present']]
    clock_in_feriado = pd.DataFrame({
        'datestamp': fer['Date'].dt.strftime('%m/%d/%Y'), 'Full Name': fer['full_name'], 'LOB': fer['LOB'],
        'Status': fer['Status'], 'Schedule In': fer['sched_in'].apply(att.fmt_clock),
        'Schedule Out': fer['sched_out'].apply(att.fmt_clock),
        'Clock in time': fer['clock_in_td'].apply(att.fmt_clock),
        'Lunch': fer['lunch'].apply(lambda x: att.fmt_duration(x, True)),
        'Clock out time': fer['clock_out_td'].apply(att.fmt_clock),
        'Total work time': fer['worked'].apply(lambda x: hhmm(redondear(x))),
    }).reset_index(drop=True)

    feriados = sorted({(k[0], v) for k, v in holidays.items() if start <= k[0] <= end})
    return {'payroll': payroll, 'revisar': revisar, 'detalle': detalle,
            'clock_in_feriado': clock_in_feriado, 'feriados': feriados}


# ── Excel en memoria (para los botones de descarga) ──────────────────────────
def to_excel_bytes(sheets, money_cols=()):
    from openpyxl.styles import Alignment, Font, PatternFill

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True, color='FFFFFF')
                cell.fill = PatternFill('solid', fgColor='1E3A8A')
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            for i, col in enumerate(df.columns, start=1):
                letter = ws.cell(row=1, column=i).column_letter
                w = max([len(str(col))] + [len(str(v)) for v in df[col].head(300)]) + 2
                ws.column_dimensions[letter].width = min(w, 60)
                if col in money_cols:
                    for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                        row[0].number_format = '0.00'
    return buf.getvalue()
