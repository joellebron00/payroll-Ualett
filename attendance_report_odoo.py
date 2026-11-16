"""
Attendance Report — horarios tomados de Odoo
=============================================

Versión nueva de `Con_shift_start_merged.py`. El original NO se modifica.

Diferencia principal: el horario de cada empleado (entrada, salida, almuerzo y
horas programadas) se toma del "Horario de trabajo" que tiene asignado en Odoo
(hr.employee.resource_calendar_id -> resource.calendar, p. ej. "MF9-6(40h)",
"MF10-7/SAT9-1(44h)", "MF8-5/SAT9-1(44h)"...), en vez del Excel/Google Sheet del roster.

Uso:
    python attendance_report_odoo.py                      (pregunta las fechas)
    python attendance_report_odoo.py --start 2026-10-26 --end 2026-10-30
    python attendance_report_odoo.py --start 2026-10-26 --end 2026-10-30 --holiday no
    python attendance_report_odoo.py --start 2026-10-26 --end 2026-10-30 --exclude 2026-10-28

Ver CAMBIOS.md (misma carpeta) para el detalle de cambios.
"""

import argparse
import json
import os
import sys
import unicodedata
import xmlrpc.client
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

TZ_LOCAL = 'America/Santo_Domingo'

# Roster: YA NO se usa para horarios. Solo aporta LOB / Full Name / Status
# (Odoo no tiene el LOB tal como lo usan los reportes).
ROSTER_SHEET_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vS0HaWItYoSrWOqFq8Mmm0NnSDo7zok8lzqbBpmjuyeRpNVLwZZ8wEumtoNh-t3ucHEQ0I_qCrXAF39/pub?gid=220893030&single=true&output=csv'

# Cambios de horario puntuales (se aplican ENCIMA del horario de Odoo).
FECHA_EXPLICITA_SHEET_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQB0xwMOqVQ2_zUEK8UgT54l0f5lbPw-SeG2hN2qLmCbRW9MMEWX2115wdnttkFtdHei1efVx7lPfUq/pub?gid=1413327553&single=true&output=csv'
DIAS_SEMANA_SHEET_URL     = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTNhDUyoboW2pPqfXFy0F3WNkS2vv8EmJA6aWhc8bLFEz9tEF5vCmjiuGpMS9WCoBSATpGYGSq3KkFy/pub?gid=2015714283&single=true&output=csv'
APPLY_SHEET_OVERRIDES = True

# ¿Quiénes se consideran "esperados" (para No Show)?
#   'roster' -> solo los empleados que están en el roster (mismo universo que el código original)
#   'odoo'   -> todos los empleados activos de Odoo con horario asignado
EMPLOYEE_SCOPE = 'roster'

# Fechas a saltar (acepta 'YYYYMMDD' o 'YYYY-MM-DD'). También se pueden pasar con --exclude.
FECHAS_A_EXCLUIR = []

# Feriados extra que NO estén cargados en Odoo (Odoo > Tiempo personal > Días festivos).
EXTRA_HOLIDAYS = []

# Reglas de puntualidad / cálculo (mismos valores que el código original)
LATE_TOLERANCE_MIN = 6             # Tarde si Clock In > Schedule In + 6 min
EARLY_CLOCK_IN_ALLOWANCE_MIN = 5   # Se cuentan como máximo 5 min antes de la hora de entrada
EXCEPTIONS_THAT_EXCUSE_LATE = {'Called Out', 'Late In', 'Medical Appointment'}
SATURDAY_ONLY_PRESENT = True       # Sábado: solo se reportan quienes marcaron (igual que el original)
DEFAULT_LUNCH_HOURS = 1.0          # Solo si un override crea un horario en un día que Odoo no programa

# Nombres de permisos (hr.leave.type) -> nombre en el reporte
ISSUE_TYPE_MAP = {
    'Vacations': 'Vacation',
    'Sick Leave': 'Medical License',
    'Late in': 'Late In',
    'Bereavement': 'Duelo',
    'Marriage Leave': 'Marriage',
    'Maternity/Paternity Leave': 'Maternity/paternity License',
    # Nombres en español que existían antes en Odoo
    'Permiso de ausencia': 'Leave of Absence',
    'Cita médica': 'Medical Appointment',
    'Matrimonio': 'Marriage',
    'Salida temprana': 'Early Out',
    'Tardanza': 'Late In',
    'Licencia por maternidad/paternidad': 'Maternity/paternity License',
}

DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

REPORT_COLUMNS = [
    # Mismas columnas y orden que el reporte original
    'Date', 'Full Name', 'LOB', 'Status', 'Schedule In', 'Schedule Out', 'Schedule Hours',
    'Clock In', 'Away', 'Lunch', 'Clock Out', 'Worked Hours', 'Holiday', 'Active', 'Exception',
    # Columnas nuevas (al final, para no romper lo que ya consume el reporte)
    'Work Email', 'Odoo Schedule', 'Real Clock In', 'Real Clock Out', 'Late Minutes', 'Note',
]


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════════════════════════

def log(msg):
    print(msg, flush=True)


def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_config(base_dir):
    """Busca config.json en la carpeta del script y, si no está, en la carpeta padre."""
    for folder in (base_dir, os.path.dirname(base_dir)):
        path = os.path.join(folder, 'config.json')
        if os.path.exists(path):
            return path
    raise FileNotFoundError('No se encontró config.json (ni en esta carpeta ni en la carpeta padre).')


def load_config(base_dir):
    """Credenciales de Odoo: config.json (PC) o, si no existe, los Secrets de Streamlit Cloud ([odoo])."""
    try:
        with open(find_config(base_dir), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        try:
            import streamlit as st
            return dict(st.secrets['odoo'])
        except Exception:  # noqa: BLE001
            raise FileNotFoundError(
                'No se encontró config.json ni la sección [odoo] en los Secrets de Streamlit.') from None


def strip_accents(text):
    text = unicodedata.normalize('NFKD', str(text))
    return ''.join(c for c in text if not unicodedata.combining(c)).strip().lower()


def norm_email(series):
    return series.fillna('').astype(str).str.strip().str.lower().replace({'false': '', 'nan': ''})


def m2o_id(v):
    return v[0] if v else None


def m2o_name(v):
    return v[1] if v else ''


def parse_date(text):
    text = str(text).strip()
    for fmt in ('%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Fecha inválida: {text!r}. Usa YYYY-MM-DD.')


def local_to_utc_str(d):
    """Medianoche local de la fecha d -> string UTC que entiende Odoo."""
    ts = pd.Timestamp(d).tz_localize(TZ_LOCAL).tz_convert('UTC')
    return ts.strftime('%Y-%m-%d %H:%M:%S')


def utc_to_local(series):
    s = series.where(series.astype(bool), None)  # Odoo devuelve False para vacío
    return pd.to_datetime(s, errors='coerce').dt.tz_localize('UTC').dt.tz_convert(TZ_LOCAL).dt.tz_localize(None)


def hours_to_td(h):
    return pd.to_timedelta(float(h), unit='h')


def parse_time_to_td(value):
    """'9:00:00 AM', '18:30:00', '8:00' -> Timedelta desde medianoche."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or str(value).strip() == '':
        return pd.NaT
    try:
        t = pd.to_datetime(str(value).strip(), format='mixed')
        return pd.Timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)
    except (ValueError, TypeError):
        return pd.NaT


def fmt_clock(td):
    """Timedelta desde medianoche -> '9:05:00 AM'."""
    if pd.isna(td):
        return ''
    total = int(round(td.total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    period = 'AM' if (h % 24) < 12 else 'PM'
    h12 = h % 12 or 12
    return f'{h12}:{m:02d}:{s:02d} {period}'


def fmt_duration(td, pad_hours=False):
    """Timedelta -> 'H:MM:SS' (o 'HH:MM:SS' con pad_hours)."""
    if pd.isna(td):
        return ''
    total = max(int(round(td.total_seconds())), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}' if pad_hours else f'{h}:{m:02d}:{s:02d}'


def td_from_midnight(ts):
    return ts - ts.dt.normalize()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRADAS
# ══════════════════════════════════════════════════════════════════════════════

def get_inputs():
    p = argparse.ArgumentParser(description='Attendance Report con horarios de Odoo')
    p.add_argument('--start', help='Fecha inicio YYYY-MM-DD')
    p.add_argument('--end', help='Fecha fin YYYY-MM-DD')
    p.add_argument('--holiday', choices=['auto', 'yes', 'no'],
                   help='auto = usa los feriados de Odoo (por defecto)')
    p.add_argument('--exclude', nargs='*', default=[], help='Fechas a excluir')
    args = p.parse_args()

    while True:
        try:
            start = parse_date(args.start or input('Insert start date (YYYY-MM-DD) : '))
            end = parse_date(args.end or input('Insert end date (YYYY-MM-DD) : '))
            if end < start:
                raise ValueError('La fecha fin es anterior a la fecha inicio.')
            break
        except ValueError as e:
            if args.start or args.end:
                sys.exit(f'❌ {e}')
            log(f'❌ {e} Intenta de nuevo.')

    holiday_mode = args.holiday
    if holiday_mode is None:
        ans = input('Is Holiday? (Enter = automático desde Odoo / Yes / No) : ').strip().lower()
        holiday_mode = {'yes': 'yes', 'y': 'yes', 'si': 'yes', 'sí': 'yes', 'no': 'no', 'n': 'no'}.get(ans, 'auto')

    excluded = {parse_date(d) for d in list(FECHAS_A_EXCLUIR) + list(args.exclude)}
    return start, end, holiday_mode, excluded


# ══════════════════════════════════════════════════════════════════════════════
# ODOO
# ══════════════════════════════════════════════════════════════════════════════

class Odoo:
    def __init__(self, config):
        self.url, self.db = config['url'], config['db']
        self.key = config['api_key']
        common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common', allow_none=True)
        self.uid = common.authenticate(self.db, config['username'], self.key, {})
        if not self.uid:
            raise RuntimeError('No se pudo autenticar en Odoo. Revisa config.json.')
        self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object', allow_none=True)
        log(f'✅ Conectado a Odoo (UID {self.uid})')

    def search_read(self, model, domain, fields, **kw):
        return self.models.execute_kw(self.db, self.uid, self.key, model, 'search_read',
                                      [domain], {'fields': fields, **kw})


def fetch_employees(odoo):
    raw = odoo.search_read('hr.employee', [], ['id', 'name', 'work_email', 'department_id', 'resource_calendar_id'])
    df = pd.DataFrame(raw)
    df = pd.DataFrame({
        'employee_id': df['id'],
        'odoo_name': df['name'],
        'email': norm_email(df['work_email']),
        'department': df['department_id'].apply(m2o_name),
        'calendar_id': df['resource_calendar_id'].apply(m2o_id),
        'calendar_name': df['resource_calendar_id'].apply(m2o_name),
    })
    log(f'✅ {len(df)} empleados activos en Odoo')
    return df


def fetch_calendar_lines(odoo, calendar_ids):
    ids = [int(c) for c in calendar_ids if pd.notna(c)]
    raw = odoo.search_read('resource.calendar.attendance', [['calendar_id', 'in', ids]],
                           ['calendar_id', 'dayofweek', 'hour_from', 'hour_to', 'day_period',
                            'week_type', 'date_from', 'date_to'])
    lines = pd.DataFrame(raw)
    lines['calendar_id'] = lines['calendar_id'].apply(m2o_id)
    lines['dayofweek'] = lines['dayofweek'].astype(int)
    for c in ('date_from', 'date_to'):
        lines[c] = pd.to_datetime(lines[c].where(lines[c].astype(bool), None), errors='coerce')
    return lines


def schedule_for_day(lines, day):
    """
    Horario programado por calendario para una fecha:
    sched_in / sched_out (Timedelta desde medianoche), sched_hours (horas de trabajo) y
    sched_lunch (hueco entre entrada y salida que no es trabajo: almuerzo/break).
    """
    ts = pd.Timestamp(day)
    week_type = str(int((day.toordinal() - 1) // 7 % 2))  # misma fórmula que Odoo para calendarios de 2 semanas
    l = lines[
        (lines['dayofweek'] == day.weekday())
        & (lines['date_from'].isna() | (lines['date_from'] <= ts))
        & (lines['date_to'].isna() | (lines['date_to'] >= ts))
        & (~lines['week_type'].astype(bool) | (lines['week_type'].astype(str) == week_type))
    ]
    work = l[l['day_period'] != 'lunch'].copy()
    if work.empty:
        return pd.DataFrame(columns=['calendar_id', 'sched_in', 'sched_out', 'sched_hours', 'sched_lunch'])
    work['dur'] = work['hour_to'] - work['hour_from']
    g = work.groupby('calendar_id').agg(h_in=('hour_from', 'min'), h_out=('hour_to', 'max'), h_work=('dur', 'sum'))
    out = pd.DataFrame({
        'calendar_id': g.index,
        'sched_in': pd.to_timedelta(g['h_in'].values, unit='h'),
        'sched_out': pd.to_timedelta(g['h_out'].values, unit='h'),
        'sched_hours': pd.to_timedelta(g['h_work'].values, unit='h'),
    })
    out['sched_lunch'] = (out['sched_out'] - out['sched_in'] - out['sched_hours']).clip(lower=pd.Timedelta(0))
    return out


def fetch_holidays(odoo, start, end):
    """Feriados globales de Odoo (resource.calendar.leaves sin recurso)."""
    raw = odoo.search_read('resource.calendar.leaves',
                           [['resource_id', '=', False],
                            ['date_from', '<', local_to_utc_str(end + timedelta(days=1))],
                            ['date_to', '>=', local_to_utc_str(start)]],
                           ['name', 'date_from', 'date_to', 'calendar_id'])
    holidays = {}  # (date, calendar_id or None) -> nombre
    for h in raw:
        d_from = utc_to_local(pd.Series([h['date_from']])).iloc[0].date()
        d_to = utc_to_local(pd.Series([h['date_to']])).iloc[0].date()
        cal = m2o_id(h['calendar_id'])
        d = d_from
        while d <= d_to:
            holidays[(d, cal)] = h['name']
            d += timedelta(days=1)
    for x in EXTRA_HOLIDAYS:
        holidays[(parse_date(x), None)] = 'Feriado (manual)'
    return holidays


def fetch_leaves(odoo, start, end):
    """Permisos aprobados que tocan el rango."""
    raw = odoo.search_read('hr.leave',
                           [['state', '=', 'validate'],
                            ['date_from', '<', local_to_utc_str(end + timedelta(days=1))],
                            ['date_to', '>=', local_to_utc_str(start)]],
                           ['employee_id', 'holiday_status_id', 'date_from', 'date_to'])
    if not raw:
        return pd.DataFrame(columns=['employee_id', 'issue_type', 'start_date', 'end_date'])
    df = pd.DataFrame(raw)
    df = df[df['employee_id'].astype(bool)]
    out = pd.DataFrame({
        'employee_id': df['employee_id'].apply(m2o_id),
        'issue_type': df['holiday_status_id'].apply(m2o_name).replace(ISSUE_TYPE_MAP),
        'start_date': utc_to_local(df['date_from']).dt.normalize(),
        'end_date': utc_to_local(df['date_to']).dt.normalize(),
    })
    log(f'✅ {len(out)} permisos aprobados en el rango')
    return out


def fetch_attendance(odoo, start, end):
    """Todas las marcaciones del rango en UNA sola consulta (sin límite de registros)."""
    raw = odoo.search_read('hr.attendance',
                           [['check_in', '>=', local_to_utc_str(start)],
                            ['check_in', '<', local_to_utc_str(end + timedelta(days=1))]],
                           ['employee_id', 'check_in', 'check_out', 'worked_hours',
                            'away_hours', 'lunch_hours', 'meeting_hours', 'resource_calendar_id'])
    log(f'✅ {len(raw)} marcaciones encontradas')
    cols = ['employee_id', 'date', 'clock_in', 'clock_out', 'away', 'lunch', 'meeting', 'sessions', 'open_session',
            'att_calendar_id', 'att_calendar_name']
    if not raw:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(raw)
    df['employee_id'] = df['employee_id'].apply(m2o_id)
    df['clock_in'] = utc_to_local(df['check_in'])
    df['clock_out'] = utc_to_local(df['check_out'])
    df['date'] = df['clock_in'].dt.normalize()
    for c in ('away_hours', 'lunch_hours', 'meeting_hours'):
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    # "Active Schedule" de la marcación: horario de ese día en particular (puede diferir del de la ficha)
    df['att_calendar_id'] = pd.to_numeric(df['resource_calendar_id'].apply(m2o_id), errors='coerce')
    df['att_calendar_name'] = df['resource_calendar_id'].apply(m2o_name)

    # Consolidar varias sesiones del mismo empleado en el mismo día (antes se hacía por nombre).
    # Si las sesiones tienen Active Schedule distinto, manda el de la primera sesión del día.
    df = df.sort_values('clock_in')
    g = df.groupby(['employee_id', 'date'])
    out = g.agg(clock_in=('clock_in', 'min'), clock_out=('clock_out', 'max'),
                away=('away_hours', 'sum'), lunch=('lunch_hours', 'sum'),
                meeting=('meeting_hours', 'sum'), sessions=('clock_in', 'size'),
                att_calendar_id=('att_calendar_id', 'first'),
                att_calendar_name=('att_calendar_name', 'first')).reset_index()
    out['open_session'] = g['clock_out'].apply(lambda s: s.isna().any()).values
    out.loc[out['open_session'], 'clock_out'] = pd.NaT  # si una sesión sigue abierta, no hay salida final
    for c in ('away', 'lunch', 'meeting'):
        out[c] = pd.to_timedelta(out[c], unit='h').dt.round('s')
    return out[cols]


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS (roster para LOB + overrides de horario)
# ══════════════════════════════════════════════════════════════════════════════

def read_sheet(url, label):
    try:
        return pd.read_csv(url)
    except Exception as e:  # noqa: BLE001 — un sheet caído no debe tumbar el reporte
        log(f'⚠️  No se pudo leer {label}: {e}')
        return None


def load_roster():
    r = read_sheet(ROSTER_SHEET_URL, 'el roster')
    if r is None:
        return pd.DataFrame(columns=['email', 'roster_name', 'LOB', 'roster_status', 'roster_in', 'roster_out'])
    out = pd.DataFrame({
        'email': norm_email(r['Email']),
        'roster_name': r.get('Full Name', ''),
        'LOB': r.get('LOB', ''),
        'roster_status': r.get('Status', 'Active'),
        'roster_in': r.get('M/F In', '').apply(parse_time_to_td) if 'M/F In' in r else pd.NaT,
        'roster_out': r.get('M/F Out', '').apply(parse_time_to_td) if 'M/F Out' in r else pd.NaT,
    })
    out = out[out['email'] != ''].drop_duplicates('email')
    log(f'✅ Roster: {len(out)} empleados (solo para LOB / nombre)')
    return out


def load_overrides():
    if not APPLY_SHEET_OVERRIDES:
        return None, None
    fe = read_sheet(FECHA_EXPLICITA_SHEET_URL, 'Schedule Exceptions (fecha explícita)')
    ds = read_sheet(DIAS_SEMANA_SHEET_URL, 'Schedule Exceptions (días de la semana)')
    if fe is not None:
        fe = fe.dropna(subset=['email'])
        fe['email'] = norm_email(fe['email'])
        fe['Date'] = pd.to_datetime(fe['Date'], errors='coerce').dt.normalize()
    if ds is not None:
        ds = ds.dropna(subset=['email'])
        ds['email'] = norm_email(ds['email'])
        ds['day_key'] = ds['Day'].apply(strip_accents)
    return fe, ds


def overrides_for_day(day, fe, ds):
    """email -> (in, out). Prioridad: fecha explícita > día de la semana."""
    result = {}
    if ds is not None:
        for _, r in ds[ds['day_key'] == strip_accents(DIAS_ES[day.weekday()])].iterrows():
            result[r['email']] = (parse_time_to_td(r['Schedule In']), parse_time_to_td(r['Schedule Out']))
    if fe is not None:
        for _, r in fe[fe['Date'] == pd.Timestamp(day)].iterrows():
            result[r['email']] = (parse_time_to_td(r['Schedule In']), parse_time_to_td(r['Schedule Out']))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# LÓGICA DEL REPORTE
# ══════════════════════════════════════════════════════════════════════════════

def find_exception(leaves, day):
    """employee_id -> primer tipo de permiso aprobado que cubre la fecha."""
    ts = pd.Timestamp(day)
    l = leaves[(leaves['start_date'] <= ts) & (leaves['end_date'] >= ts)]
    return l.drop_duplicates('employee_id').set_index('employee_id')['issue_type'].to_dict()


def build_day(day, people, cal_lines, attendance, leaves, holidays, holiday_mode, fe, ds):
    ts = pd.Timestamp(day)
    is_saturday = day.weekday() == 5

    # 1) Marcaciones del día y horario de Odoo para ese día.
    #    Prioridad: Active Schedule de la marcación > horario de la ficha del empleado.
    att = attendance[attendance['date'] == ts].drop(columns='date')
    d = people.merge(att, on='employee_id', how='left')
    d['base_calendar_id'] = d['calendar_id']
    d['base_calendar_name'] = d['calendar_name']
    use_att = d['att_calendar_id'].notna()
    d['calendar_id'] = pd.to_numeric(d['att_calendar_id'].where(use_att, d['base_calendar_id']), errors='coerce')
    d['calendar_name'] = d['att_calendar_name'].where(use_att, d['base_calendar_name'])
    d['active_schedule_diff'] = use_att & (d['calendar_id'] != pd.to_numeric(d['base_calendar_id'], errors='coerce'))
    sched = schedule_for_day(cal_lines, day)
    sched['calendar_id'] = pd.to_numeric(sched['calendar_id'], errors='coerce')
    d = d.merge(sched, on='calendar_id', how='left')
    d['override'] = False

    # 2) Overrides puntuales de los Google Sheets
    ov = overrides_for_day(day, fe, ds)
    for email, (s_in, s_out) in ov.items():
        mask = d['email'] == email
        if not mask.any() or pd.isna(s_in) or pd.isna(s_out):
            continue
        lunch = d.loc[mask, 'sched_lunch'].iloc[0]
        if pd.isna(lunch):
            lunch = hours_to_td(DEFAULT_LUNCH_HOURS) if (s_out - s_in) > pd.Timedelta(hours=5) else pd.Timedelta(0)
        d.loc[mask, ['sched_in', 'sched_out', 'sched_lunch']] = [s_in, s_out, lunch]
        d.loc[mask, 'sched_hours'] = max(s_out - s_in - lunch, pd.Timedelta(0))
        d.loc[mask, 'override'] = True

    # 3) Presencia
    d['present'] = d['clock_in'].notna()
    d['scheduled'] = d['sched_in'].notna()

    # 4) Permisos y feriado
    exc = find_exception(leaves, day)
    d['Exception'] = d['employee_id'].map(exc).fillna('')
    if holiday_mode == 'yes':
        d['is_holiday'] = True
    elif holiday_mode == 'no':
        d['is_holiday'] = False
    else:
        global_h = (day, None) in holidays
        d['is_holiday'] = d['calendar_id'].apply(lambda c: global_h or (day, c) in holidays)

    # Solo nos interesan: quien marcó, o quien estaba programado (para ausencias)
    d = d[d['present'] | (d['scheduled'] & d['expected'])].copy()
    if d.empty:
        return d

    # 5) Cálculos para los presentes
    d['clock_in_td'] = td_from_midnight(d['clock_in'])
    d['clock_out_td'] = td_from_midnight(d['clock_out'])
    late_delta = d['clock_in_td'] - d['sched_in']
    d['is_late'] = d['present'] & d['scheduled'] & (late_delta > pd.Timedelta(minutes=LATE_TOLERANCE_MIN))
    d['late_minutes'] = np.where(d['is_late'], (late_delta.dt.total_seconds() / 60).round(0), 0)

    d['adj_in'] = d['clock_in_td']
    d['adj_out'] = d['clock_out_td']
    d['eff_lunch'] = d['lunch']
    s = d['scheduled'] & d['present']
    floor_in = d['sched_in'] - pd.Timedelta(minutes=EARLY_CLOCK_IN_ALLOWANCE_MIN)
    d.loc[s, 'adj_in'] = np.maximum(d.loc[s, 'clock_in_td'], floor_in[s])
    has_out = s & d['clock_out_td'].notna()
    d.loc[has_out, 'adj_out'] = np.minimum(d.loc[has_out, 'clock_out_td'], d.loc[has_out, 'sched_out'])
    d.loc[s, 'eff_lunch'] = np.maximum(d.loc[s, 'lunch'], d.loc[s, 'sched_lunch'])
    d['worked'] = (d['adj_out'] - d['adj_in'] - d['eff_lunch']).clip(lower=pd.Timedelta(0))

    # 6) Status
    has_exc = d['Exception'] != ''
    excuses = d['Exception'].isin(EXCEPTIONS_THAT_EXCUSE_LATE)
    absent_status = np.where(d['is_holiday'], 'Holiday at Home', 'No Show')
    d['Status'] = np.select(
        [
            d['present'] & ~d['scheduled'],                 # marcó en un día que Odoo no le programa
            d['present'] & has_exc & (~d['is_late'] | excuses),
            d['present'] & d['is_late'],
            d['present'],
            has_exc,                                        # ausente con permiso
        ],
        ['Not Scheduled', d['Exception'], 'Late', 'On Time', d['Exception']],
        default=absent_status,
    )

    # Ausentes con vacaciones: se pagan las horas programadas de ese día (antes era '8:00:00' fijo)
    vac = ~d['present'] & (d['Exception'] == 'Vacation')
    d.loc[vac, 'worked'] = d.loc[vac, 'sched_hours']

    # Notas
    notes = []
    for _, r in d.iterrows():
        n = []
        if r['present'] and pd.isna(r['clock_out']):
            n.append('Sin check-out')
        if r['present'] and r['sessions'] > 1:
            n.append(f"{int(r['sessions'])} sesiones")
        if r['active_schedule_diff'] and not r['override']:
            n.append(f"Active Schedule del día: {r['calendar_name']} (ficha: {r['base_calendar_name']})")
        if r['override']:
            n.append('Horario cambiado por Schedule Exception')
        if r['present'] and r['is_holiday']:
            n.append('Trabajó en feriado')
        if pd.isna(r['calendar_id']):
            n.append('Sin horario en Odoo')
        notes.append('; '.join(n))
    d['Note'] = notes

    if is_saturday and SATURDAY_ONLY_PRESENT:
        d = d[d['present']]

    d['Date'] = ts
    return d


def format_report(d):
    out = pd.DataFrame({
        'Date': d['Date'].dt.strftime('%m/%d/%Y'),
        'Full Name': d['full_name'],
        'LOB': d['LOB'],
        'Status': d['Status'],
        'Schedule In': d['sched_in'].apply(fmt_clock),
        'Schedule Out': d['sched_out'].apply(fmt_clock),
        'Schedule Hours': d['sched_hours'].apply(fmt_duration),
        'Clock In': d['adj_in'].apply(fmt_clock),
        'Away': d['away'].apply(lambda x: fmt_duration(x, True)),
        'Lunch': d['eff_lunch'].apply(lambda x: fmt_duration(x, True)),
        'Clock Out': d['adj_out'].apply(fmt_clock),
        'Worked Hours': d['worked'].apply(fmt_duration),
        'Holiday': np.where(d['is_holiday'], 'Yes', 'No'),
        'Active': d['Active'],
        'Exception': d['Exception'],
        'Work Email': d['email'],
        'Odoo Schedule': d['calendar_name'],
        'Real Clock In': d['clock_in_td'].apply(fmt_clock),
        'Real Clock Out': d['clock_out_td'].apply(fmt_clock),
        'Late Minutes': d['late_minutes'].astype(int),
        'Note': d['Note'],
    })
    return out[REPORT_COLUMNS]


def build_summary(d):
    """Resumen por empleado para todo el rango."""
    d = d.copy()
    d['worked_h'] = d['worked'].dt.total_seconds().fillna(0) / 3600
    d['sched_h'] = np.where(d['scheduled'], d['sched_hours'].dt.total_seconds().fillna(0) / 3600, 0)
    d['away_h'] = d['away'].dt.total_seconds().fillna(0) / 3600
    g = d.groupby(['email', 'full_name', 'LOB', 'base_calendar_name'], dropna=False)
    s = g.agg(
        Days_Reported=('Status', 'size'),
        Present=('present', 'sum'),
        On_Time=('Status', lambda x: (x == 'On Time').sum()),
        Late=('Status', lambda x: (x == 'Late').sum()),
        No_Show=('Status', lambda x: (x == 'No Show').sum()),
        With_Exception=('Exception', lambda x: (x != '').sum()),
        Late_Minutes=('late_minutes', 'sum'),
        Scheduled_Hours=('sched_h', 'sum'),
        Worked_Hours=('worked_h', 'sum'),
        Away_Hours=('away_h', 'sum'),
    ).reset_index()
    s['Punctuality %'] = np.where(s['Present'] > 0, (s['On_Time'] / s['Present'] * 100).round(1), np.nan)
    s['Worked vs Scheduled %'] = np.where(s['Scheduled_Hours'] > 0,
                                         (s['Worked_Hours'] / s['Scheduled_Hours'] * 100).round(1), np.nan)
    for c in ('Scheduled_Hours', 'Worked_Hours', 'Away_Hours'):
        s[c] = s[c].round(2)
    s = s.rename(columns={'email': 'Work Email', 'full_name': 'Full Name', 'base_calendar_name': 'Odoo Schedule'})
    s.columns = [c.replace('_', ' ') for c in s.columns]
    return s.sort_values(['LOB', 'Full Name'])


def build_discrepancies(employees, roster, cal_lines):
    """Compara el horario del roster (Excel) contra el de Odoo para detectar datos desactualizados."""
    rows = []
    monday = date(2026, 1, 5)  # cualquier lunes: comparamos el horario L-V
    mon = schedule_for_day(cal_lines, monday).set_index('calendar_id')
    merged = roster.merge(employees, on='email', how='outer', indicator=True)
    for _, r in merged.iterrows():
        name = r['roster_name'] if isinstance(r.get('roster_name'), str) else r.get('odoo_name', '')
        if r['_merge'] == 'left_only':
            rows.append([name, r['email'], r.get('LOB', ''), '', '', 'Está en el roster pero no en Odoo (o está archivado / correo distinto)'])
            continue
        if r['_merge'] == 'right_only':
            rows.append([name, r['email'], '', r['calendar_name'], '', 'Está en Odoo pero no en el roster'])
            continue
        if pd.isna(r['calendar_id']):
            rows.append([name, r['email'], r['LOB'], '', '', 'No tiene horario asignado en Odoo'])
            continue
        if r['calendar_id'] in mon.index and pd.notna(r['roster_in']):
            o_in, o_out = mon.loc[r['calendar_id'], 'sched_in'], mon.loc[r['calendar_id'], 'sched_out']
            if o_in != r['roster_in'] or (pd.notna(r['roster_out']) and o_out != r['roster_out']):
                rows.append([name, r['email'], r['LOB'], r['calendar_name'],
                             f"{fmt_clock(r['roster_in'])} - {fmt_clock(r['roster_out'])}",
                             'El horario L-V del roster no coincide con Odoo (se usa Odoo)'])
    return pd.DataFrame(rows, columns=['Full Name', 'Work Email', 'LOB', 'Odoo Schedule',
                                       'Roster Schedule (L-V)', 'Issue'])


def write_excel(path, sheets):
    try:
        writer = pd.ExcelWriter(path, engine='openpyxl')
    except PermissionError:
        path = path.replace('.xlsx', f"_{datetime.now():%H%M%S}.xlsx")
        writer = pd.ExcelWriter(path, engine='openpyxl')
        log('⚠️  El archivo estaba abierto en Excel; se guardó con otro nombre.')
    with writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for i, col in enumerate(df.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(500)]) + 2
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width, 60)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def load_period(start, end, holiday_mode='auto', excluded=(), base_dir=None):
    """
    Calcula el detalle diario (una fila por empleado y día) con las reglas de Ualett.
    Lo usan este reporte y el notebook de nómina (Payroll_Nomina_Mejorado), para que
    ambos apliquen exactamente las mismas reglas.
    Devuelve (full, employees, roster, cal_lines, holidays).
    """
    base_dir = base_dir or get_base_dir()
    config = load_config(base_dir)

    odoo = Odoo(config)
    employees = fetch_employees(odoo)
    holidays = fetch_holidays(odoo, start, end)
    leaves = fetch_leaves(odoo, start, end)
    attendance = fetch_attendance(odoo, start, end)
    cal_ids = set(employees['calendar_id'].dropna()) | set(attendance['att_calendar_id'].dropna())
    cal_lines = fetch_calendar_lines(odoo, cal_ids)
    roster = load_roster()
    fe, ds = load_overrides()

    # Personas: Odoo (horario) + roster (LOB / nombre / status)
    people = employees.merge(roster, on='email', how='left')
    people['full_name'] = people['roster_name'].where(people['roster_name'].notna(), people['odoo_name'])
    people['LOB'] = people['LOB'].where(people['LOB'].notna() & (people['LOB'] != ''), people['department'])
    people['Active'] = people['roster_status'].fillna('Active')
    if EMPLOYEE_SCOPE == 'roster' and not roster.empty:
        people['expected'] = people['email'].isin(roster['email'])
    else:
        people['expected'] = True

    days = []
    d = start
    while d <= end:
        if d in excluded:
            log(f'⏭️  {d} excluido')
        else:
            day_df = build_day(d, people, cal_lines, attendance, leaves, holidays, holiday_mode, fe, ds)
            if day_df.empty:
                log(f'ℹ️  {d} ({DIAS_ES[d.weekday()]}): nadie programado ni marcado, se omite')
            else:
                hol = ' — FERIADO' if day_df['is_holiday'].any() else ''
                log(f'✅ {d} ({DIAS_ES[d.weekday()]}): {len(day_df)} filas{hol}')
                days.append(day_df)
        d += timedelta(days=1)

    if not days:
        raise RuntimeError('No hay datos para el rango indicado.')

    full = pd.concat(days, ignore_index=True).sort_values(['Date', 'LOB', 'full_name'])
    return full, employees, roster, cal_lines, holidays


def main():
    start, end, holiday_mode, excluded = get_inputs()
    base_dir = get_base_dir()
    try:
        full, employees, roster, cal_lines, _ = load_period(start, end, holiday_mode, excluded, base_dir)
    except RuntimeError as e:
        sys.exit(f'❌ {e}')

    report = format_report(full)
    summary = build_summary(full)
    discrepancies = build_discrepancies(employees, roster, cal_lines) if not roster.empty else pd.DataFrame()

    output_dir = os.path.join(base_dir, 'final_results', 'Attendance')
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f'Attendance_Report_{start:%Y%m%d}_to_{end:%Y%m%d}.xlsx')
    sheets = {'Attendance': report, 'Resumen': summary}
    if not discrepancies.empty:
        sheets['Roster vs Odoo'] = discrepancies
    path = write_excel(path, sheets)

    log('\n── Resumen ──')
    log(report['Status'].value_counts().to_string())
    if not discrepancies.empty:
        log(f'⚠️  {len(discrepancies)} diferencias entre roster y Odoo (ver hoja "Roster vs Odoo")')
    log(f'\n✅ Reporte final: {path}')


if __name__ == '__main__':
    main()
