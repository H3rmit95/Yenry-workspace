"""La Melena de Yenry – CRM de barbería en un solo archivo.

Versión autocontenida: SQLite + Flask + Jinja2 todo en este fichero.
No necesita carpeta templates/ ni static/. Crea 'barberia.db' junto a
este archivo.

── Ejecutar localmente ────────────────────────────────────────────────────
    pip install flask
    python barberia_crm_app.py
    Abrir → http://localhost:5000

── Subir a PythonAnywhere ─────────────────────────────────────────────────
    1. Sube este archivo a tu carpeta home, por ejemplo:
           /home/TU_USUARIO/barberia_crm_app.py
    2. Dashboard → Web → Add a new web app → Flask → Python 3.x
    3. En "Code: WSGI configuration file" abre el editor y reemplaza
       TODO el contenido con esto:

           import sys
           sys.path.insert(0, '/home/TU_USUARIO')
           from barberia_crm_app import app as application

    4. Pulsa Reload → listo.
"""

import calendar
import csv
import io
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, flash, redirect, render_template, request, send_file, session, url_for
from functools import wraps
from jinja2 import DictLoader

# ===========================================================================
#  BASE DE DATOS (SQLite)
# ===========================================================================
DB_PATH = Path(__file__).parent / "barberia.db"

SERVICIOS_SEED = [
    ("Corte Clásico", 12, 30, "Corte tradicional a tijera y máquina, lavado incluido."),
    ("Fade / Degradado", 15, 40, "Degradado limpio a tu medida: low, mid o high fade."),
    ("Corte + Barba", 20, 50, "Corte completo más perfilado y arreglo de barba con toalla caliente."),
    ("Arreglo de Barba", 10, 25, "Perfilado, recorte y acabado con aceites y bálsamo."),
    ("Corte Niño", 9, 25, "Corte para los más pequeños, con paciencia y estilo."),
    ("Diseño / Líneas", 5, 15, "Detalles y líneas decorativas para personalizar tu corte."),
]

BARBEROS_SEED = [
    ("Carlos", "09:00", "19:00", "6", 5),
    ("Miguel", "10:00", "18:00", "0,6", 5),
    ("Andrés", "08:00", "16:00", "5,6", 5),
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fila(row):
    return dict(row) if row is not None else None


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            precio INTEGER NOT NULL,
            duracion INTEGER NOT NULL,
            desc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS barberos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            apertura TEXT NOT NULL DEFAULT '09:00',
            cierre TEXT NOT NULL DEFAULT '19:00',
            dias_cerrados TEXT NOT NULL DEFAULT '6',
            margen INTEGER NOT NULL DEFAULT 5
        );
        CREATE TABLE IF NOT EXISTS citas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente TEXT NOT NULL,
            telefono TEXT,
            servicio_id INTEGER NOT NULL REFERENCES servicios(id),
            barbero TEXT NOT NULL,
            fecha TEXT NOT NULL,
            hora TEXT NOT NULL,
            cliente_id INTEGER REFERENCES clientes(id),
            recordatorio_enviado INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            telefono TEXT,
            notas TEXT,
            creado TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
    """)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(barberos)").fetchall()}
    if "apertura" not in cols:
        conn.execute("ALTER TABLE barberos ADD COLUMN apertura TEXT NOT NULL DEFAULT '09:00'")
        conn.execute("ALTER TABLE barberos ADD COLUMN cierre TEXT NOT NULL DEFAULT '19:00'")
        conn.execute("ALTER TABLE barberos ADD COLUMN dias_cerrados TEXT NOT NULL DEFAULT '6'")
        for nombre, ap, ci, dc, mg in BARBEROS_SEED:
            conn.execute(
                "UPDATE barberos SET apertura=?, cierre=?, dias_cerrados=? WHERE nombre=?",
                (ap, ci, dc, nombre),
            )
    if "margen" not in cols:
        conn.execute("ALTER TABLE barberos ADD COLUMN margen INTEGER NOT NULL DEFAULT 5")

    if conn.execute("SELECT COUNT(*) FROM servicios").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO servicios (nombre, precio, duracion, desc) VALUES (?, ?, ?, ?)",
            SERVICIOS_SEED,
        )
    if conn.execute("SELECT COUNT(*) FROM barberos").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO barberos (nombre, apertura, cierre, dias_cerrados, margen) VALUES (?, ?, ?, ?, ?)",
            BARBEROS_SEED,
        )

    cols_citas = {r[1] for r in conn.execute("PRAGMA table_info(citas)").fetchall()}
    if "cliente_id" not in cols_citas:
        conn.execute("ALTER TABLE citas ADD COLUMN cliente_id INTEGER REFERENCES clientes(id)")
    if "recordatorio_enviado" not in cols_citas:
        conn.execute("ALTER TABLE citas ADD COLUMN recordatorio_enviado INTEGER NOT NULL DEFAULT 0")

    if conn.execute("SELECT COUNT(*) FROM citas").fetchone()[0] == 0:
        hoy = date.today().isoformat()
        conn.executemany(
            "INSERT INTO citas (cliente, telefono, servicio_id, barbero, fecha, hora) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("José Pérez", "809-555-1010", 2, "Carlos", hoy, "10:00"),
                ("Luis Gómez", "809-555-2020", 3, "Miguel", hoy, "11:30"),
            ],
        )

    faltan = conn.execute("SELECT DISTINCT cliente FROM citas WHERE cliente_id IS NULL").fetchall()
    for (nombre,) in faltan:
        tel_row = conn.execute(
            "SELECT telefono FROM citas WHERE cliente = ? AND telefono IS NOT NULL AND telefono != '' LIMIT 1",
            (nombre,),
        ).fetchone()
        telefono = tel_row[0] if tel_row else ""
        existente = conn.execute("SELECT id FROM clientes WHERE nombre = ?", (nombre,)).fetchone()
        if existente:
            cid = existente[0]
        else:
            cur = conn.execute(
                "INSERT INTO clientes (nombre, telefono, notas, creado) VALUES (?, ?, '', ?)",
                (nombre, telefono, date.today().isoformat()),
            )
            cid = cur.lastrowid
        conn.execute(
            "UPDATE citas SET cliente_id = ? WHERE cliente = ? AND cliente_id IS NULL",
            (cid, nombre),
        )

    conn.commit()
    conn.close()


# ---------- Servicios ----------

def listar_servicios():
    conn = get_conn()
    filas = conn.execute("SELECT * FROM servicios ORDER BY id").fetchall()
    conn.close()
    return [dict(f) for f in filas]


def servicio_por_id(sid):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM servicios WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return _fila(fila)


def agregar_servicio(nombre, precio, duracion, desc):
    conn = get_conn()
    conn.execute(
        "INSERT INTO servicios (nombre, precio, duracion, desc) VALUES (?, ?, ?, ?)",
        (nombre, precio, duracion, desc),
    )
    conn.commit()
    conn.close()


def actualizar_servicio(sid, nombre, precio, duracion, desc):
    conn = get_conn()
    conn.execute(
        "UPDATE servicios SET nombre=?, precio=?, duracion=?, desc=? WHERE id=?",
        (nombre, precio, duracion, desc, sid),
    )
    conn.commit()
    conn.close()


def eliminar_servicio(sid):
    conn = get_conn()
    conn.execute("DELETE FROM servicios WHERE id = ?", (sid,))
    conn.commit()
    conn.close()


def citas_de_servicio(sid):
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM citas WHERE servicio_id = ?", (sid,)).fetchone()[0]
    conn.close()
    return n


# ---------- Barberos ----------

def _dias_set(texto):
    return {int(x) for x in texto.split(",") if x.strip() != ""}


def _barbero_dict(fila):
    d = dict(fila)
    d["dias_cerrados_set"] = _dias_set(d["dias_cerrados"])
    return d


def listar_barberos():
    conn = get_conn()
    filas = conn.execute("SELECT * FROM barberos ORDER BY id").fetchall()
    conn.close()
    return [_barbero_dict(f) for f in filas]


def barbero_por_nombre(nombre):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM barberos WHERE nombre = ?", (nombre,)).fetchone()
    conn.close()
    return _barbero_dict(fila) if fila is not None else None


def barbero_por_id(bid):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM barberos WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return _barbero_dict(fila) if fila is not None else None


def agregar_barbero(nombre, apertura, cierre, dias_cerrados, margen=5):
    conn = get_conn()
    conn.execute(
        "INSERT INTO barberos (nombre, apertura, cierre, dias_cerrados, margen) VALUES (?, ?, ?, ?, ?)",
        (nombre, apertura, cierre, dias_cerrados, margen),
    )
    conn.commit()
    conn.close()


def actualizar_barbero(bid, nombre, apertura, cierre, dias_cerrados, margen=5):
    conn = get_conn()
    anterior = conn.execute("SELECT nombre FROM barberos WHERE id = ?", (bid,)).fetchone()
    conn.execute(
        "UPDATE barberos SET nombre=?, apertura=?, cierre=?, dias_cerrados=?, margen=? WHERE id=?",
        (nombre, apertura, cierre, dias_cerrados, margen, bid),
    )
    if anterior is not None and anterior["nombre"] != nombre:
        conn.execute("UPDATE citas SET barbero=? WHERE barbero=?", (nombre, anterior["nombre"]))
    conn.commit()
    conn.close()


def eliminar_barbero(bid):
    conn = get_conn()
    conn.execute("DELETE FROM barberos WHERE id = ?", (bid,))
    conn.commit()
    conn.close()


def nombre_barbero_existe(nombre, excluir_id=None):
    conn = get_conn()
    if excluir_id is None:
        fila = conn.execute("SELECT 1 FROM barberos WHERE nombre = ?", (nombre,)).fetchone()
    else:
        fila = conn.execute(
            "SELECT 1 FROM barberos WHERE nombre = ? AND id != ?", (nombre, excluir_id)
        ).fetchone()
    conn.close()
    return fila is not None


def citas_de_barbero(nombre):
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM citas WHERE barbero = ?", (nombre,)).fetchone()[0]
    conn.close()
    return n


# ---------- Citas ----------

def listar_citas():
    conn = get_conn()
    filas = conn.execute("SELECT * FROM citas ORDER BY fecha, hora").fetchall()
    conn.close()
    return [dict(f) for f in filas]


def citas_por_fecha(fecha):
    conn = get_conn()
    filas = conn.execute("SELECT * FROM citas WHERE fecha = ? ORDER BY hora", (fecha,)).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def citas_proximas(desde=None):
    desde = desde or date.today().isoformat()
    conn = get_conn()
    filas = conn.execute(
        "SELECT c.*, s.nombre AS servicio_nombre, s.precio AS servicio_precio, "
        "s.duracion AS servicio_duracion "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha >= ? ORDER BY c.fecha, c.hora",
        (desde,),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def marcar_recordatorio(cita_id, enviado=True):
    conn = get_conn()
    conn.execute(
        "UPDATE citas SET recordatorio_enviado = ? WHERE id = ?",
        (1 if enviado else 0, cita_id),
    )
    conn.commit()
    conn.close()


def agregar_cita(cita):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO citas (cliente, telefono, servicio_id, barbero, fecha, hora, cliente_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            cita["cliente"], cita["telefono"], cita["servicio_id"],
            cita["barbero"], cita["fecha"], cita["hora"], cita.get("cliente_id"),
        ),
    )
    conn.commit()
    nuevo_id = cur.lastrowid
    conn.close()
    return nuevo_id


def eliminar_cita(cita_id):
    conn = get_conn()
    conn.execute("DELETE FROM citas WHERE id = ?", (cita_id,))
    conn.commit()
    conn.close()


def _a_minutos(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def buscar_choque(barbero, fecha, hora, duracion, margen=0, ignorar_id=None):
    inicio = _a_minutos(hora)
    fin = inicio + duracion
    conn = get_conn()
    filas = conn.execute(
        "SELECT c.*, s.duracion AS dur FROM citas c "
        "JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.barbero = ? AND c.fecha = ?",
        (barbero, fecha),
    ).fetchall()
    conn.close()
    for c in filas:
        if c["id"] == ignorar_id:
            continue
        c_inicio = _a_minutos(c["hora"])
        c_fin = c_inicio + c["dur"]
        if inicio < c_fin + margen and c_inicio < fin + margen:
            return dict(c)
    return None


# ---------- Clientes ----------

def listar_clientes():
    conn = get_conn()
    filas = conn.execute("""
        SELECT cl.*,
               COUNT(c.id)               AS visitas,
               COALESCE(SUM(s.precio), 0) AS total_gastado,
               MAX(c.fecha)              AS ultima_visita
        FROM clientes cl
        LEFT JOIN citas c     ON c.cliente_id = cl.id
        LEFT JOIN servicios s ON s.id = c.servicio_id
        GROUP BY cl.id
        ORDER BY cl.nombre COLLATE NOCASE
    """).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def cliente_por_id(cid):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return _fila(fila)


def citas_de_cliente(cid):
    conn = get_conn()
    filas = conn.execute("""
        SELECT c.*, s.nombre AS servicio_nombre, s.precio AS servicio_precio,
               s.duracion AS servicio_duracion
        FROM citas c
        LEFT JOIN servicios s ON s.id = c.servicio_id
        WHERE c.cliente_id = ?
        ORDER BY c.fecha DESC, c.hora DESC
    """, (cid,)).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def agregar_cliente(nombre, telefono, notas=""):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO clientes (nombre, telefono, notas, creado) VALUES (?, ?, ?, ?)",
        (nombre, telefono, notas, date.today().isoformat()),
    )
    conn.commit()
    nuevo_id = cur.lastrowid
    conn.close()
    return nuevo_id


def actualizar_cliente(cid, nombre, telefono, notas):
    conn = get_conn()
    anterior = conn.execute("SELECT nombre FROM clientes WHERE id = ?", (cid,)).fetchone()
    conn.execute(
        "UPDATE clientes SET nombre=?, telefono=?, notas=? WHERE id=?",
        (nombre, telefono, notas, cid),
    )
    if anterior is not None and anterior["nombre"] != nombre:
        conn.execute("UPDATE citas SET cliente=? WHERE cliente_id=?", (nombre, cid))
    conn.commit()
    conn.close()


def eliminar_cliente(cid):
    conn = get_conn()
    conn.execute("DELETE FROM clientes WHERE id = ?", (cid,))
    conn.commit()
    conn.close()


def contar_citas_cliente(cid):
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM citas WHERE cliente_id = ?", (cid,)).fetchone()[0]
    conn.close()
    return n


# ---------- Configuración ----------

def get_config(clave, default=None):
    conn = get_conn()
    fila = conn.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    conn.close()
    return fila["valor"] if fila is not None else default


def set_config(clave, valor):
    conn = get_conn()
    conn.execute(
        "INSERT INTO config (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, str(valor)),
    )
    conn.commit()
    conn.close()


# ---------- Reportes ----------

def _mes_like(anio, mes):
    return f"{anio:04d}-{mes:02d}-%"


def ingresos_resumen(anio, mes):
    conn = get_conn()
    fila = conn.execute(
        "SELECT COUNT(*) AS citas, COALESCE(SUM(s.precio), 0) AS ingresos "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id WHERE c.fecha LIKE ?",
        (_mes_like(anio, mes),),
    ).fetchone()
    conn.close()
    citas = fila["citas"]
    ingresos = fila["ingresos"]
    return {"citas": citas, "ingresos": ingresos, "ticket": round(ingresos / citas) if citas else 0}


def ingresos_por_barbero(anio, mes):
    conn = get_conn()
    filas = conn.execute(
        "SELECT c.barbero AS barbero, COUNT(*) AS citas, COALESCE(SUM(s.precio), 0) AS ingresos "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ? GROUP BY c.barbero ORDER BY ingresos DESC, c.barbero COLLATE NOCASE",
        (_mes_like(anio, mes),),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def ingresos_por_servicio(anio, mes):
    conn = get_conn()
    filas = conn.execute(
        "SELECT s.nombre AS servicio, COUNT(*) AS veces, COALESCE(SUM(s.precio), 0) AS ingresos "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ? GROUP BY s.id ORDER BY ingresos DESC, s.nombre COLLATE NOCASE",
        (_mes_like(anio, mes),),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def citas_detalle_mes(anio, mes):
    conn = get_conn()
    filas = conn.execute(
        "SELECT c.fecha, c.hora, c.cliente, c.barbero, s.nombre AS servicio, s.precio AS precio "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ? ORDER BY c.fecha, c.hora",
        (_mes_like(anio, mes),),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def ingresos_total():
    conn = get_conn()
    total = conn.execute(
        "SELECT COALESCE(SUM(s.precio), 0) FROM citas c JOIN servicios s ON s.id = c.servicio_id"
    ).fetchone()[0]
    conn.close()
    return total


def buscar_o_crear_cliente(nombre, telefono):
    conn = get_conn()
    fila = conn.execute(
        "SELECT id, telefono FROM clientes WHERE nombre = ? COLLATE NOCASE", (nombre,)
    ).fetchone()
    if fila is not None:
        cid = fila["id"]
        if telefono and not (fila["telefono"] or "").strip():
            conn.execute("UPDATE clientes SET telefono=? WHERE id=?", (telefono, cid))
            conn.commit()
        conn.close()
        return cid
    cur = conn.execute(
        "INSERT INTO clientes (nombre, telefono, notas, creado) VALUES (?, ?, '', ?)",
        (nombre, telefono, date.today().isoformat()),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def slots_disponibles(nombre_barbero, fecha_iso, paso=30):
    """Lista de slots de `paso` minutos indicando si están libres u ocupados.
    Cada elemento: {'hora': 'HH:MM', 'libre': bool}.
    Un slot está ocupado si alguna cita (+ margen) está activa durante ese intervalo."""
    b = barbero_por_nombre(nombre_barbero)
    if b is None:
        return []
    d = date.fromisoformat(fecha_iso)
    if d.weekday() in b["dias_cerrados_set"]:
        return []
    inicio = _a_minutos(b["apertura"])
    fin = _a_minutos(b["cierre"])
    conn = get_conn()
    citas_raw = conn.execute(
        "SELECT c.hora, s.duracion FROM citas c "
        "JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.barbero = ? AND c.fecha = ?",
        (nombre_barbero, fecha_iso),
    ).fetchall()
    conn.close()
    ocupados = [
        (_a_minutos(c["hora"]), _a_minutos(c["hora"]) + c["duracion"] + b["margen"])
        for c in citas_raw
    ]
    slots = []
    t = inicio
    while t < fin:
        libre = not any(c_ini <= t < c_fin for c_ini, c_fin in ocupados)
        slots.append({"hora": f"{t // 60:02d}:{t % 60:02d}", "libre": libre})
        t += paso
    return slots


# ===========================================================================
#  PLANTILLAS JINJA2 (embebidas – sin carpeta templates/)
# ===========================================================================

_CSS = """\
:root {
  --crema: #fbf7f0;
  --crema-2: #f6ebdd;
  --crema-3: #f1e3d0;
  --dorado: #c8952f;
  --dorado-hover: #b07f22;
  --tinta: #2b2826;
  --tinta-suave: #6b6359;
  --borde: #eaddc9;
  --verde: #2f9e6b;
  --blanco: #ffffff;
  --sombra: 0 8px 30px rgba(80, 60, 30, 0.08);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Segoe UI", system-ui, -apple-system, sans-serif; background: var(--crema); color: var(--tinta); line-height: 1.55; }
h1, h2, h3, .serif { font-family: Georgia, "Times New Roman", serif; font-weight: 600; letter-spacing: -0.01em; }
a { color: inherit; text-decoration: none; }
.topbar { background: var(--crema-2); font-size: 0.78rem; color: var(--tinta-suave); border-bottom: 1px solid var(--borde); }
.topbar .wrap { display: flex; flex-wrap: wrap; gap: 1.5rem; justify-content: center; padding: 0.5rem 1.5rem; }
.topbar span::before { content: "✓ "; color: var(--verde); font-weight: 700; }
.nav { background: var(--blanco); border-bottom: 1px solid var(--borde); position: sticky; top: 0; z-index: 20; }
.nav .wrap { max-width: 1180px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.5rem; }
.brand { display: flex; align-items: center; gap: 0.75rem; font-size: 1.85rem; }
.brand .serif { font-size: 1.85rem; font-weight: 700; letter-spacing: -0.025em; color: var(--tinta); }
.brand .logo-leon { width: 42px; height: 42px; flex-shrink: 0; }
.menu { display: flex; gap: 1.6rem; align-items: center; }
.menu a { color: var(--tinta-suave); font-size: 0.95rem; padding: 0.3rem 0; border-bottom: 2px solid transparent; transition: 0.15s; }
.menu a:hover { color: var(--tinta); }
.menu a.activo { color: var(--tinta); border-bottom-color: var(--dorado); }
.btn { display: inline-flex; align-items: center; gap: 0.5rem; background: var(--dorado); color: #fff; font-weight: 600; padding: 0.7rem 1.4rem; border-radius: 8px; border: none; cursor: pointer; font-size: 0.95rem; transition: 0.15s; }
.btn:hover { background: var(--dorado-hover); }
.btn-light { background: var(--blanco); color: var(--tinta); border: 1px solid var(--borde); }
.btn-light:hover { background: var(--crema-2); }
.btn-sm { padding: 0.4rem 0.8rem; font-size: 0.82rem; }
.btn-danger { background: transparent; color: #b23b3b; border: 1px solid #e6c4c4; }
.btn-danger:hover { background: #fbeaea; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 1.5rem; }
.seccion { padding: 3rem 0; }
.hero { display: grid; grid-template-columns: 1.1fr 1fr; gap: 3rem; align-items: center; padding: 3.5rem 0 2rem; }
.hero h1 { font-size: 3.2rem; line-height: 1.05; }
.hero .sub { font-size: 1.15rem; font-weight: 600; margin: 1.2rem 0 0.6rem; }
.hero p { color: var(--tinta-suave); max-width: 38ch; }
.hero .cta { margin-top: 1.8rem; display: flex; gap: 0.8rem; flex-wrap: wrap; }
.hero-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; }
.hero-grid .tile { aspect-ratio: 1; border-radius: 12px; background: linear-gradient(135deg, var(--crema-3), var(--crema-2)); display: flex; align-items: center; justify-content: center; font-size: 2rem; box-shadow: var(--sombra); }
.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.2rem; margin-top: 1rem; }
.stat { background: var(--blanco); border: 1px solid var(--borde); border-radius: 14px; padding: 1.4rem 1.6rem; box-shadow: var(--sombra); }
.stat .num { font-family: Georgia, serif; font-size: 2.2rem; color: var(--dorado); }
.stat .lbl { color: var(--tinta-suave); font-size: 0.9rem; }
.titulo-seccion { text-align: center; margin-bottom: 2rem; }
.titulo-seccion h2 { font-size: 2.2rem; }
.titulo-seccion p { color: var(--tinta-suave); margin-top: 0.4rem; }
.grid-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.3rem; }
.card { background: var(--blanco); border: 1px solid var(--borde); border-radius: 16px; padding: 1.6rem; box-shadow: var(--sombra); transition: 0.18s; }
.card:hover { transform: translateY(-4px); box-shadow: 0 14px 40px rgba(80,60,30,0.13); }
.card .emoji { font-size: 1.8rem; }
.card h3 { font-size: 1.25rem; margin: 0.6rem 0 0.4rem; }
.card .desc { color: var(--tinta-suave); font-size: 0.92rem; min-height: 3.2em; }
.card .pie { display: flex; align-items: center; justify-content: space-between; margin-top: 1.1rem; }
.precio { font-family: Georgia, serif; font-size: 1.8rem; color: var(--dorado); }
.duracion { font-size: 0.82rem; color: var(--tinta-suave); background: var(--crema-2); padding: 0.25rem 0.6rem; border-radius: 20px; }
.form-card { background: var(--blanco); border: 1px solid var(--borde); border-radius: 18px; padding: 2.2rem; box-shadow: var(--sombra); max-width: 640px; margin: 0 auto; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.1rem; }
.field { display: flex; flex-direction: column; gap: 0.35rem; }
.field.full { grid-column: 1 / -1; }
.field label { font-size: 0.85rem; font-weight: 600; color: var(--tinta-suave); }
.field input, .field select { padding: 0.7rem 0.8rem; border: 1px solid var(--borde); border-radius: 9px; font-size: 0.95rem; background: var(--crema); color: var(--tinta); }
.field input:focus, .field select:focus { outline: 2px solid var(--dorado); border-color: transparent; }
.horarios { display: flex; flex-wrap: wrap; gap: 0.5rem; justify-content: center; margin-top: 0.9rem; }
.horario-badge { display: inline-block; padding: 0.35rem 0.9rem; background: var(--crema-2); border: 1px solid var(--borde); border-radius: 20px; font-size: 0.85rem; color: var(--tinta-suave); }
.horario-badge strong { color: var(--tinta); }
.alerta { max-width: 640px; margin: 0 auto 1.2rem; padding: 0.9rem 1.2rem; background: #fbeaea; border: 1px solid #e6c4c4; border-left: 4px solid #b23b3b; color: #8a2e2e; border-radius: 10px; font-size: 0.92rem; }
.cal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1.2rem; }
.cal-head h2 { font-size: 1.8rem; }
.cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }
.cal-dow { text-align: center; font-size: 0.8rem; font-weight: 700; color: var(--tinta-suave); text-transform: uppercase; padding: 0.4rem 0; }
.cal-cell { background: var(--blanco); border: 1px solid var(--borde); border-radius: 10px; min-height: 110px; padding: 0.5rem; display: flex; flex-direction: column; gap: 0.3rem; }
.cal-cell.fuera { background: var(--crema-2); color: var(--tinta-suave); opacity: 0.7; }
.cal-cell.hoy { border: 2px solid var(--dorado); }
.cal-cell .n { font-size: 0.85rem; font-weight: 600; align-self: flex-end; }
.cal-evt { background: var(--crema-3); border-left: 3px solid var(--dorado); border-radius: 5px; padding: 0.25rem 0.4rem; font-size: 0.72rem; line-height: 1.25; }
.cal-evt .h { font-weight: 700; color: var(--dorado-hover); }
.lista { display: flex; flex-direction: column; gap: 0.7rem; }
.cita-row { background: var(--blanco); border: 1px solid var(--borde); border-radius: 12px; padding: 0.9rem 1.2rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.cita-row .info { display: flex; align-items: center; gap: 1rem; }
.cita-row .hora { font-family: Georgia, serif; font-size: 1.2rem; color: var(--dorado); min-width: 64px; }
.cita-row .det small { color: var(--tinta-suave); }
.vacio { text-align: center; color: var(--tinta-suave); padding: 2rem; background: var(--blanco); border: 1px dashed var(--borde); border-radius: 12px; }
.flash { max-width: 760px; margin: 0 auto 1rem; padding: 0.8rem 1.1rem; border-radius: 10px; font-size: 0.92rem; border: 1px solid; }
.flash-ok { background: #eaf7f0; border-color: #bfe6d2; color: #1f6b46; }
.flash-error { background: #fbeaea; border-color: #e6c4c4; color: #8a2e2e; }
.barbero-card { background: var(--blanco); border: 1px solid var(--borde); border-radius: 16px; padding: 1.4rem 1.6rem; box-shadow: var(--sombra); max-width: 760px; margin: 0 auto 1.2rem; }
.barbero-card.nuevo { border-style: dashed; background: var(--crema); }
.barbero-card > h3 { margin-bottom: 1rem; }
.barbero-head { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; margin-bottom: 1rem; }
.barbero-head h3 { font-size: 1.3rem; }
.barbero-head .contador { font-size: 0.8rem; color: var(--tinta-suave); margin-left: auto; }
.bf-grid { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 1rem; }
.sf-grid { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 1rem; }
@media (max-width: 600px) { .bf-grid, .sf-grid { grid-template-columns: 1fr; } }
.dias-fila { display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin: 1rem 0; }
.dias-lbl { font-size: 0.85rem; font-weight: 600; color: var(--tinta-suave); margin-right: 0.3rem; }
.chip-dia { cursor: pointer; user-select: none; }
.chip-dia input { display: none; }
.chip-dia span { display: inline-block; padding: 0.35rem 0.7rem; border-radius: 20px; border: 1px solid var(--borde); background: var(--crema-2); color: var(--tinta-suave); font-size: 0.82rem; transition: 0.15s; }
.chip-dia input:checked + span { background: var(--dorado); color: #fff; border-color: var(--dorado); }
.bf-acciones { display: flex; gap: 0.6rem; }
.del-form { margin-top: 0.8rem; border-top: 1px solid var(--borde); padding-top: 0.9rem; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.clientes-tabla { background: var(--blanco); border: 1px solid var(--borde); border-radius: 14px; overflow: hidden; box-shadow: var(--sombra); }
.ct-head, .ct-fila { display: grid; grid-template-columns: 2fr 1.4fr 0.8fr 1fr 1.2fr; gap: 0.8rem; align-items: center; padding: 0.9rem 1.2rem; }
.ct-head { background: var(--crema-2); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--tinta-suave); }
.ct-fila { border-top: 1px solid var(--borde); transition: background 0.15s; }
.ct-fila:hover { background: var(--crema); }
.ct-nombre { font-weight: 600; color: var(--dorado-hover); }
.ct-head-citas, .ct-fila-citas { grid-template-columns: 1.1fr 0.7fr 1.6fr 1.2fr 0.8fr; }
.cd-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 1.6rem; }
.cd-stats .stat { background: var(--blanco); border: 1px solid var(--borde); border-radius: 14px; padding: 1.2rem; text-align: center; box-shadow: var(--sombra); }
.cd-stats .num { display: block; font-family: Georgia, serif; font-size: 1.6rem; color: var(--tinta); }
.cd-stats .lbl { font-size: 0.8rem; color: var(--tinta-suave); text-transform: uppercase; letter-spacing: 0.04em; }
.volver { display: inline-block; color: var(--tinta-suave); font-size: 0.85rem; margin-bottom: 0.6rem; }
.volver:hover { color: var(--dorado-hover); }
.rep-stats { grid-template-columns: repeat(4, 1fr); }
.rep-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.4rem; }
.rep-lista { display: flex; flex-direction: column; gap: 1rem; }
.rep-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.35rem; }
.rep-nombre { font-weight: 600; }
.rep-monto { color: var(--dorado-hover); font-weight: 600; }
.rep-monto small { color: var(--tinta-suave); font-weight: 400; }
.rep-barra { height: 10px; background: var(--crema-3); border-radius: 6px; overflow: hidden; }
.rep-barra span { display: block; height: 100%; background: linear-gradient(90deg, var(--dorado), var(--dorado-hover)); border-radius: 6px; }
@media (max-width: 880px) { .rep-stats { grid-template-columns: 1fr 1fr; } .rep-cols { grid-template-columns: 1fr; } }
.rec-lista { display: flex; flex-direction: column; gap: 0.9rem; }
.rec-card { display: grid; grid-template-columns: 90px 1fr auto; gap: 1.2rem; align-items: center; background: var(--blanco); border: 1px solid var(--borde); border-left: 4px solid var(--dorado); border-radius: 14px; padding: 1rem 1.2rem; box-shadow: var(--sombra); }
.rec-card.enviado { border-left-color: var(--verde); opacity: 0.85; }
.rec-cuando { text-align: center; }
.rec-dia { display: block; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--dorado-hover); }
.rec-hora { display: block; font-family: Georgia, serif; font-size: 1.3rem; }
.rec-info { display: flex; flex-direction: column; gap: 0.15rem; min-width: 0; }
.rec-cliente { font-weight: 600; }
.rec-ok { color: var(--verde); font-size: 0.8rem; font-weight: 600; }
.rec-detalle { color: var(--tinta-suave); font-size: 0.9rem; }
.rec-tel { color: var(--tinta-suave); font-size: 0.85rem; }
.rec-acciones { display: flex; gap: 0.5rem; align-items: center; }
.wa-btn { background: #25d366; border-color: #25d366; color: #fff; }
.wa-btn:hover { background: #1da851; border-color: #1da851; }
.rec-config { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; background: var(--blanco); border: 1px solid var(--borde); border-radius: 12px; padding: 0.8rem 1.1rem; margin-bottom: 1.6rem; color: var(--tinta-suave); }
.rec-config input { width: 70px; padding: 0.45rem 0.6rem; border: 1px solid var(--borde); border-radius: 8px; font-size: 1rem; }
.rec-config label { font-weight: 600; color: var(--tinta); }
.rec-bloque { margin-bottom: 1.8rem; }
.rec-titulo { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.9rem; font-size: 1.2rem; }
.rec-badge { background: var(--dorado); color: #fff; font-size: 0.85rem; font-weight: 700; border-radius: 999px; padding: 0.1rem 0.6rem; }
.rec-card.destacar { border-left-color: #d8392b; background: #fffaf3; }
.rec-aviso { display: flex; align-items: center; gap: 0.5rem; background: #fff6e6; border: 1px solid var(--dorado); color: var(--tinta); border-radius: 12px; padding: 0.9rem 1.2rem; font-size: 0.98rem; }
.rec-aviso span { margin-left: auto; color: var(--dorado-hover); font-weight: 600; }
.rec-aviso:hover { background: #fdeecc; }
@media (max-width: 700px) { .rec-card { grid-template-columns: 70px 1fr; } .rec-acciones { grid-column: 1 / -1; } }
.footer { background: var(--crema-2); border-top: 1px solid var(--borde); color: var(--tinta-suave); font-size: 0.85rem; text-align: center; padding: 1.6rem; margin-top: 2rem; }
.banda { background: var(--crema-2); }
@media (max-width: 880px) {
  .hero { grid-template-columns: 1fr; }
  .hero h1 { font-size: 2.4rem; }
  .stats, .grid-cards { grid-template-columns: 1fr; }
  .form-grid { grid-template-columns: 1fr; }
  .menu { display: none; }
  .cal-cell { min-height: 80px; }
  .cd-stats { grid-template-columns: 1fr; }
  .ct-head { display: none; }
  .ct-fila, .ct-fila-citas { grid-template-columns: 1fr 1fr; }
}
/* ---------- Dashboard mejorado ---------- */
.dash-hero { background: linear-gradient(135deg, #2b2826 0%, #3d3330 60%, #4a3c2a 100%); color: #fff; padding: 2.2rem 0; }
.dash-hero .wrap { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }
.dash-greeting h1 { color: #fff; font-size: 2rem; margin-bottom: 0.3rem; }
.dash-fecha { color: rgba(255,255,255,0.55); font-size: 0.9rem; }
.dash-hero-actions { display: flex; gap: 0.7rem; flex-wrap: wrap; }
.dash-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
.dash-stat { background: var(--blanco); border: 1px solid var(--borde); border-radius: 16px; padding: 1.4rem 1.6rem; box-shadow: var(--sombra); display: flex; align-items: center; gap: 1.1rem; }
.ds-icon { font-size: 2rem; flex-shrink: 0; }
.ds-body { display: flex; flex-direction: column; }
.ds-num { font-family: Georgia, serif; font-size: 2rem; color: var(--dorado); line-height: 1; }
.ds-lbl { font-size: 0.78rem; color: var(--tinta-suave); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 0.2rem; }
.dash-section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1.2rem; }
.dash-section-head h2 { font-size: 1.5rem; }
.prox-tabla { background: var(--blanco); border: 1px solid var(--borde); border-radius: 14px; overflow: hidden; box-shadow: var(--sombra); }
.prox-head, .prox-fila { display: grid; grid-template-columns: 0.9fr 0.6fr 1.2fr 1.1fr 1fr; gap: 0.8rem; align-items: center; padding: 0.85rem 1.2rem; }
.prox-head { background: var(--crema-2); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--tinta-suave); }
.prox-fila { border-top: 1px solid var(--borde); font-size: 0.9rem; }
.prox-fila:hover { background: var(--crema); }
.prox-fecha { font-weight: 600; color: var(--dorado-hover); }
.prox-hoy { background: #fffcf5; }
@media (max-width: 900px) { .dash-stats { grid-template-columns: 1fr 1fr; } }
@media (max-width: 560px) { .dash-stats { grid-template-columns: 1fr; } .prox-head { display: none; } .prox-fila { grid-template-columns: 1fr 1fr; } }
/* ---------- Home hero con foto ---------- */
.home-hero {
  position: relative;
  min-height: 92vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background-color: #1a1210;
  background-image: url('/hero');
  background-size: cover;
  background-position: center top;
}
.home-hero-sm { min-height: 56vh; }
.home-hero::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(160deg, rgba(12,8,5,0.82) 0%, rgba(30,20,12,0.55) 55%, rgba(12,8,5,0.78) 100%);
}
.home-hero-content {
  position: relative;
  z-index: 1;
  text-align: center;
  color: #fff;
  padding: 3rem 2rem;
  max-width: 780px;
}
.home-brand-tag {
  display: block;
  font-family: "Segoe UI", sans-serif;
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: var(--dorado);
  margin-bottom: 1.4rem;
}
.home-hero h1 {
  font-size: 4rem;
  line-height: 1.08;
  color: #fff;
  text-shadow: 0 2px 40px rgba(0,0,0,0.7);
  margin-bottom: 1.3rem;
}
.home-slogan {
  font-size: 1.15rem;
  color: rgba(255,255,255,0.72);
  font-style: italic;
  font-family: Georgia, serif;
  letter-spacing: 0.01em;
  margin-bottom: 2.8rem;
}
.hero-divider {
  width: 60px; height: 2px;
  background: var(--dorado);
  margin: 1.2rem auto;
  border-radius: 2px;
}
.btn-hero {
  display: inline-flex; align-items: center; gap: 0.5rem;
  background: var(--dorado); color: #fff;
  font-weight: 700; font-size: 1.05rem;
  padding: 1.05rem 2.8rem; border-radius: 50px;
  letter-spacing: 0.03em; transition: 0.22s;
  box-shadow: 0 6px 28px rgba(200,149,47,0.5);
  text-decoration: none;
}
.btn-hero:hover {
  background: var(--dorado-hover); color: #fff;
  transform: translateY(-3px);
  box-shadow: 0 12px 38px rgba(200,149,47,0.6);
}
.btn-hero-glass {
  background: rgba(255,255,255,0.10);
  backdrop-filter: blur(10px);
  border: 1.5px solid rgba(255,255,255,0.3);
  box-shadow: none;
  color: #fff;
}
.btn-hero-glass:hover {
  background: rgba(255,255,255,0.20);
  transform: translateY(-3px);
  box-shadow: none;
  color: #fff;
}
@media (max-width: 640px) {
  .home-hero h1 { font-size: 2.5rem; }
  .home-slogan { font-size: 0.98rem; }
  .btn-hero { padding: 0.9rem 2rem; font-size: 0.95rem; }
  .home-hero-sm { min-height: 45vh; }
}
/* ---------- Botones sociales ---------- */
.social-btns { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; margin-top: 2.2rem; }
.btn-social { display: inline-flex; align-items: center; gap: 0.6rem; padding: 0.85rem 1.8rem; border-radius: 50px; font-size: 0.95rem; font-weight: 700; border: none; cursor: pointer; transition: 0.22s; color: #fff; letter-spacing: 0.02em; }
.btn-social:hover { transform: translateY(-3px); filter: brightness(1.15); }
.btn-fb { background: #1877f2; }
.btn-ig { background: linear-gradient(45deg, #f09433 0%, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%); }
.btn-tt { background: #111; }
/* ---------- Modal Próximamente ---------- */
.prox-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.78); z-index: 3000; align-items: center; justify-content: center; }
.prox-overlay.abierto { display: flex; }
.prox-card { background: var(--blanco); border-radius: 20px; padding: 2.8rem 2.5rem; text-align: center; max-width: 360px; width: 92%; box-shadow: 0 30px 80px rgba(0,0,0,0.3); }
.prox-card h3 { font-size: 1.6rem; margin: 0.8rem 0 0.6rem; }
.prox-card p { color: var(--tinta-suave); font-size: 0.95rem; line-height: 1.5; }
/* ---------- Reportes unificado ---------- */
.rep-subtitulo {
  font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--tinta-suave);
  margin-bottom: 0.7rem; padding-left: 0.2rem;
  display: flex; align-items: center; gap: 0.5rem;
}
.rep-subtitulo::after {
  content: ''; flex: 1; height: 1px; background: var(--borde);
}
.rep-grid-auto { grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); }
/* ---------- Disponibilidad ---------- */
.disp-nav { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 1.6rem; flex-wrap: wrap; gap: 1rem; }
.disp-nav h2 { font-size: 1.8rem; }
.disp-nav-btns { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.disp-barbero { background: var(--blanco); border: 1px solid var(--borde); border-radius: 16px; padding: 1.4rem 1.6rem; box-shadow: var(--sombra); margin-bottom: 1.2rem; }
.disp-barbero-head { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 1rem; flex-wrap: wrap; }
.disp-barbero-head h3 { font-size: 1.2rem; }
.disp-slots { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.slot { padding: 0.5rem 1rem; border-radius: 8px; font-size: 0.88rem; font-weight: 600; letter-spacing: 0.02em; }
.slot-libre { background: #eaf7f0; border: 1.5px solid #2f9e6b; color: #1a6b46; text-decoration: none; transition: 0.15s; }
.slot-libre:hover { background: #2f9e6b; color: #fff; }
.slot-ocupado { background: var(--crema-2); border: 1.5px solid var(--borde); color: #b8b0a5; cursor: default; }
.disp-cerrado { color: var(--tinta-suave); font-style: italic; }
.disp-leyenda { margin-top: 0.8rem; font-size: 0.82rem; color: var(--tinta-suave); display: flex; gap: 1rem; flex-wrap: wrap; }
.disp-input { padding: 0.5rem 0.8rem; border: 1px solid var(--borde); border-radius: 8px; font-size: 0.9rem; background: var(--crema); color: var(--tinta); }
/* ---------- Login ---------- */
.login-wrap { display: flex; justify-content: center; align-items: flex-start; padding: 5rem 1.5rem 3rem; }
.login-card { background: var(--blanco); border: 1px solid var(--borde); border-radius: 20px; padding: 2.8rem 2.4rem; max-width: 400px; width: 100%; box-shadow: 0 20px 60px rgba(80,60,30,0.12); }
.login-logo { width: 56px; height: 56px; border-radius: 50%; background: radial-gradient(circle at 30% 30%, var(--dorado), #8a651a); margin: 0 auto 1.2rem; }
.login-card h2 { text-align: center; font-size: 1.6rem; margin-bottom: 0.4rem; }
.login-card .sub { text-align: center; color: var(--tinta-suave); font-size: 0.9rem; margin-bottom: 2rem; }
/* ---------- Nav admin ---------- */
.nav-admin-ok { font-size: 0.8rem; color: var(--verde); font-weight: 700; background: #eaf7f0; border: 1px solid #bfe6d2; border-radius: 20px; padding: 0.25rem 0.7rem; }
.nav-logout { background: transparent; border: 1px solid var(--borde); color: var(--tinta-suave); border-radius: 8px; padding: 0.3rem 0.7rem; font-size: 0.8rem; cursor: pointer; }
.nav-logout:hover { background: var(--crema-2); }
.nav-admin-link { font-size: 0.85rem; color: var(--tinta-suave); }
.nav-admin-link:hover { color: var(--dorado-hover); }
"""

_T_BASE_TOP = """\
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block titulo %}La Melena de Yenry{% endblock %}</title>
  <style>
"""

_T_BASE_BOT = """\
  </style>
</head>
<body>
  <div class="topbar">
    <div class="wrap">
      <span>Reservas en línea 24/7</span>
      <span>Barberos profesionales</span>
      <span>+2,500 clientes satisfechos</span>
      <span>Atención en el centro de la ciudad</span>
    </div>
  </div>
  <nav class="nav">
    <div class="wrap">
      <a href="{{ url_for('dashboard') }}" class="brand">
        <svg class="logo-leon" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
          <!-- Melena exterior -->
          <circle cx="20" cy="20" r="19" fill="#c8952f"/>
          <!-- Melena interior -->
          <circle cx="20" cy="20" r="15.5" fill="#a87020"/>
          <!-- Cara -->
          <circle cx="20" cy="22" r="11.5" fill="#e8b84b"/>
          <!-- Frente / copete -->
          <path d="M10 18 Q11 7 20 8 Q29 7 30 18 Z" fill="#c8952f"/>
          <!-- Oreja izquierda -->
          <path d="M7 18 L11 10 L15 18 Z" fill="#c8952f"/>
          <path d="M9.5 17 L12 11.5 L14 17 Z" fill="#e8b84b" opacity="0.6"/>
          <!-- Oreja derecha -->
          <path d="M33 18 L29 10 L25 18 Z" fill="#c8952f"/>
          <path d="M30.5 17 L28 11.5 L26 17 Z" fill="#e8b84b" opacity="0.6"/>
          <!-- Cara sobre la melena -->
          <circle cx="20" cy="22" r="11" fill="#e8b84b"/>
          <!-- Ojos -->
          <ellipse cx="15" cy="19.5" rx="2.8" ry="3.2" fill="#1a0d00"/>
          <ellipse cx="25" cy="19.5" rx="2.8" ry="3.2" fill="#1a0d00"/>
          <circle cx="15.9" cy="18.4" r="1.1" fill="rgba(255,255,255,0.45)"/>
          <circle cx="25.9" cy="18.4" r="1.1" fill="rgba(255,255,255,0.45)"/>
          <!-- Nariz -->
          <path d="M17 25 L20 23 L23 25 L20 27.5 Z" fill="#b85028"/>
          <!-- Hocico -->
          <ellipse cx="15.5" cy="26.5" rx="3" ry="2" fill="#f0ca70" opacity="0.5"/>
          <ellipse cx="24.5" cy="26.5" rx="3" ry="2" fill="#f0ca70" opacity="0.5"/>
          <!-- Boca -->
          <path d="M17 29 Q20 32.5 23 29" stroke="#904020" stroke-width="1.4" fill="none" stroke-linecap="round"/>
          <!-- Bigotes izquierda -->
          <line x1="5"  y1="26" x2="14" y2="26.5" stroke="#7a5020" stroke-width="0.9" opacity="0.5"/>
          <line x1="5"  y1="28.5" x2="14" y2="28" stroke="#7a5020" stroke-width="0.9" opacity="0.5"/>
          <!-- Bigotes derecha -->
          <line x1="26" y1="26.5" x2="35" y2="26" stroke="#7a5020" stroke-width="0.9" opacity="0.5"/>
          <line x1="26" y1="28" x2="35" y2="28.5" stroke="#7a5020" stroke-width="0.9" opacity="0.5"/>
        </svg>
        <span class="serif">La Melena de Yenry</span>
      </a>
      <div class="menu">
        <a href="{{ url_for('dashboard') }}" class="{{ 'activo' if seccion=='inicio' }}">Inicio</a>
        {% if not session.get('admin') %}
        <a href="{{ url_for('cortes') }}" class="{{ 'activo' if seccion=='cortes' }}">Cortes y precios</a>
        <a href="{{ url_for('disponibilidad') }}" class="{{ 'activo' if seccion=='disponibilidad' }}">Disponibilidad</a>
        {% endif %}
        <a href="{{ url_for('calendario') }}" class="{{ 'activo' if seccion=='calendario' }}">Calendario</a>
        {% if session.get('admin') %}
        <a href="{{ url_for('servicios_admin') }}" class="{{ 'activo' if seccion=='servicios' }}">Servicios</a>
        <a href="{{ url_for('barberos_admin') }}" class="{{ 'activo' if seccion=='barberos' }}">Barberos</a>
        <a href="{{ url_for('clientes_admin') }}" class="{{ 'activo' if seccion=='clientes' }}">Clientes</a>
        <a href="{{ url_for('reportes') }}" class="{{ 'activo' if seccion=='reportes' }}">Reportes</a>
        <a href="{{ url_for('recordatorios') }}" class="{{ 'activo' if seccion=='recordatorios' }}">Recordatorios</a>
        {% endif %}
        <a href="{{ url_for('agendar') }}" class="btn btn-sm">Agendar cita</a>
        {% if session.get('admin') %}
        <span class="nav-admin-ok">Admin ✓</span>
        <form method="post" action="{{ url_for('logout') }}" style="display:inline">
          <button class="nav-logout">Salir</button>
        </form>
        {% else %}
        <a href="{{ url_for('login') }}" class="nav-admin-link">Admin</a>
        {% endif %}
      </div>
    </div>
  </nav>
  <main>
    {% block contenido %}{% endblock %}
  </main>
  <footer class="footer">
    La Melena de Yenry · CRM interno · Hecho con Flask &amp; Python
  </footer>
</body>
</html>
"""

TEMPLATES = {
    "base.html": _T_BASE_TOP + _CSS + _T_BASE_BOT,

    "dashboard.html": """\
{% extends "base.html" %}
{% block titulo %}La Melena de Yenry — Barbería{% endblock %}
{% block contenido %}

{# ══ HERO — visible para todos ════════════════════════════════════ #}
<div class="home-hero {{ 'home-hero-sm' if session.get('admin') }}">
  <div class="home-hero-content">
    <span class="home-brand-tag">✦ La Melena de Yenry ✦</span>
    <div class="hero-divider"></div>
    <h1>Donde su estilo soñado<br>se hace realidad</h1>
    <p class="home-slogan">Barbería profesional · Tu imagen, nuestra pasión</p>
    <div class="social-btns">
      <button class="btn-social btn-fb" onclick="document.getElementById('prox-overlay').classList.add('abierto')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
        Facebook
      </button>
      <button class="btn-social btn-ig" onclick="document.getElementById('prox-overlay').classList.add('abierto')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/></svg>
        Instagram
      </button>
      <button class="btn-social btn-tt" onclick="document.getElementById('prox-overlay').classList.add('abierto')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>
        TikTok
      </button>
    </div>
  </div>
</div>

<div id="prox-overlay" class="prox-overlay" onclick="this.classList.remove('abierto')">
  <div class="prox-card" onclick="event.stopPropagation()">
    <div style="font-size:3rem">🚀</div>
    <h3>¡Próximamente!</h3>
    <p>Pronto podrás encontrarnos en redes sociales.<br>¡Estamos trabajando en ello!</p>
    <button onclick="document.getElementById('prox-overlay').classList.remove('abierto')"
            class="btn" style="margin-top:1.5rem;width:100%;justify-content:center">Entendido</button>
  </div>
</div>

{# ══ PANEL ADMIN — solo visible al administrador ══════════════════ #}
{% if session.get('admin') %}

{% if por_recordar %}
<div class="wrap" style="padding-top:1rem">
  <a class="rec-aviso" href="{{ url_for('recordatorios') }}">
    🔔 Tienes <strong>{{ por_recordar }}</strong> recordatorio(s) por enviar. <span>Ir al panel →</span>
  </a>
</div>
{% endif %}

<section class="banda">
  <div class="wrap seccion">
    <div class="dash-section-head">
      <h2>Agenda de hoy</h2>
      <a href="{{ url_for('calendario') }}" class="btn btn-sm btn-light">Calendario</a>
    </div>
    {% if citas_hoy %}
    <div class="lista">
      {% for c in citas_hoy %}
        {% set s = servicio_por_id(c.servicio_id) %}
        <div class="cita-row">
          <div class="info">
            <div class="hora">{{ c.hora }}</div>
            <div class="det">
              <strong>{{ c.cliente }}</strong><br>
              <small>{{ s.nombre if s else '?' }} · <strong>{{ c.barbero }}</strong> · {{ c.telefono or 'Sin tel.' }}</small>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:1rem">
            <span class="precio" style="font-size:1.3rem">${{ s.precio if s else 0 }}</span>
            <form method="post" action="{{ url_for('eliminar', cita_id=c.id) }}"
                  onsubmit="return confirm('¿Cancelar esta cita?')">
              <button class="btn btn-sm btn-danger">Cancelar</button>
            </form>
          </div>
        </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="vacio">No hay citas para hoy. <a href="{{ url_for('agendar') }}" style="color:var(--dorado-hover);font-weight:600">Agendar una →</a></div>
    {% endif %}
  </div>
</section>

{% if proximas %}
<section class="wrap seccion">
  <div class="dash-section-head">
    <h2>Próximas citas <small style="font-size:0.9rem;color:var(--tinta-suave);font-family:sans-serif;font-weight:400">(7 días)</small></h2>
    <a href="{{ url_for('calendario') }}" class="btn btn-sm btn-light">Ver calendario →</a>
  </div>
  <div class="prox-tabla">
    <div class="prox-head">
      <span>Fecha</span><span>Hora</span><span>Cliente</span><span>Servicio</span><span>Barbero</span>
    </div>
    {% for c in proximas %}
    <div class="prox-fila {{ 'prox-hoy' if c.fecha == hoy_iso }}">
      <span class="prox-fecha">{{ c.fecha }}</span>
      <span>{{ c.hora }}</span>
      <span>{{ c.cliente }}</span>
      <span>{{ c.servicio_nombre }}</span>
      <span>{{ c.barbero }}</span>
    </div>
    {% endfor %}
  </div>
</section>
{% endif %}

{% endif %}
{# ══ FIN PANEL ADMIN ══════════════════════════════════════════════ #}

{% endblock %}
""",

    "cortes.html": """\
{% extends "base.html" %}
{% block titulo %}Cortes y precios · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="titulo-seccion">
    <h2>Cortes y precios</h2>
    <p>Nuestros servicios. Elige el tuyo y agenda en segundos.</p>
  </div>
  {% set emojis = ['✂️','💈','🧔','🪒','🧒','✨'] %}
  <div class="grid-cards">
    {% for s in servicios %}
    <div class="card">
      <div class="emoji">{{ emojis[loop.index0 % emojis|length] }}</div>
      <h3>{{ s.nombre }}</h3>
      <p class="desc">{{ s.desc }}</p>
      <div class="pie">
        <span class="precio">${{ s.precio }}</span>
        <span class="duracion">{{ s.duracion }} min</span>
      </div>
      <a href="{{ url_for('agendar') }}" class="btn btn-sm" style="margin-top:1rem;width:100%;justify-content:center">Agendar este corte</a>
    </div>
    {% endfor %}
  </div>
</section>
{% endblock %}
""",

    "agendar.html": """\
{% extends "base.html" %}
{% block titulo %}Agendar cita · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="titulo-seccion">
    <h2>Agendar una cita</h2>
    <p>Completa los datos y reserva el turno en segundos.
       <a href="{{ url_for('disponibilidad') }}" style="color:var(--dorado-hover);font-weight:600">Ver disponibilidad →</a>
    </p>
    <div class="horarios">
      {% for b in barberos %}
      <span class="horario-badge">🕒 <strong>{{ b.nombre }}</strong>: {{ b.horario }}</span>
      {% endfor %}
    </div>
  </div>
  {% if error %}
  <div class="alerta">⚠️ {{ error }}</div>
  {% endif %}
  <form class="form-card" method="post" action="{{ url_for('agendar') }}">
    <div class="form-grid">
      <div class="field full">
        <label for="cliente">Nombre del cliente</label>
        <input type="text" id="cliente" name="cliente" list="lista-clientes"
               placeholder="Ej. Juan Rodríguez" value="{{ valores.cliente if valores }}" required>
        <datalist id="lista-clientes">
          {% for c in clientes %}<option value="{{ c.nombre }}">{% endfor %}
        </datalist>
      </div>
      <div class="field">
        <label for="telefono">Teléfono</label>
        <input type="text" id="telefono" name="telefono" placeholder="809-000-0000"
               value="{{ valores.telefono if valores }}">
      </div>
      <div class="field">
        <label for="barbero">Barbero</label>
        <select id="barbero" name="barbero">
          {% for b in barberos %}
          <option value="{{ b.nombre }}" {{ 'selected' if valores and valores.barbero == b.nombre }}>
            {{ b.nombre }} ({{ b.horario }})
          </option>
          {% endfor %}
        </select>
      </div>
      <div class="field full">
        <label for="servicio_id">Servicio</label>
        <select id="servicio_id" name="servicio_id">
          {% for s in servicios %}
          <option value="{{ s.id }}" {{ 'selected' if valores and valores.servicio_id == s.id }}>
            {{ s.nombre }} — ${{ s.precio }} ({{ s.duracion }} min)
          </option>
          {% endfor %}
        </select>
      </div>
      <div class="field">
        <label for="fecha">Fecha</label>
        <input type="date" id="fecha" name="fecha" value="{{ valores.fecha if valores else hoy }}" required>
      </div>
      <div class="field">
        <label for="hora">Hora</label>
        <input type="time" id="hora" name="hora" min="{{ apertura }}" max="{{ cierre }}"
               value="{{ valores.hora if valores else '10:00' }}" required>
      </div>
    </div>
    <button type="submit" class="btn" style="margin-top:1.6rem;width:100%;justify-content:center">Confirmar cita</button>
  </form>
</section>
{% endblock %}
""",

    "calendario.html": """\
{% extends "base.html" %}
{% block titulo %}Calendario · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="cal-head">
    <h2>{{ nombre_mes }} {{ anio }}</h2>
    <div style="display:flex;gap:0.5rem">
      <a class="btn btn-light btn-sm" href="{{ url_for('calendario', anio=nav_prev.anio, mes=nav_prev.mes) }}">← Anterior</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('calendario') }}">Hoy</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('calendario', anio=nav_sig.anio, mes=nav_sig.mes) }}">Siguiente →</a>
      <a class="btn btn-sm" href="{{ url_for('agendar') }}">+ Nueva cita</a>
    </div>
  </div>
  <div class="cal-grid">
    {% for d in dias_semana %}<div class="cal-dow">{{ d }}</div>{% endfor %}
    {% for semana in semanas %}
      {% for celda in semana %}
      <div class="cal-cell {{ 'fuera' if not celda.del_mes }} {{ 'hoy' if celda.es_hoy }}">
        <span class="n">{{ celda.dia }}</span>
        {% for c in celda.citas %}
          {% set s = servicio_por_id(c.servicio_id) %}
          <div class="cal-evt" title="{{ (c.cliente + ' · ') if session.get('admin') else '' }}{{ s.nombre if s else '?' }} · {{ c.barbero }}">
            <span class="h">{{ c.hora }}</span>
            {% if session.get('admin') %}{{ c.cliente }}<br>{% endif %}
            {{ s.nombre if s else '?' }}
          </div>
        {% endfor %}
      </div>
      {% endfor %}
    {% endfor %}
  </div>
</section>
{% endblock %}
""",

    "servicios.html": """\
{% extends "base.html" %}
{% block titulo %}Servicios · La Melena de Yenry{% endblock %}
{% macro form_servicio(s=None) %}
  <form class="barbero-form" method="post" action="{{ url_for('servicios_guardar') }}">
    <input type="hidden" name="id" value="{{ s.id if s else '' }}">
    <div class="sf-grid">
      <div class="field">
        <label>Nombre del servicio</label>
        <input type="text" name="nombre" value="{{ s.nombre if s else '' }}" placeholder="Ej. Corte clásico" required>
      </div>
      <div class="field">
        <label>Precio ($)</label>
        <input type="number" name="precio" min="0" step="1" value="{{ s.precio if s else '' }}" placeholder="12" required>
      </div>
      <div class="field">
        <label>Duración (min)</label>
        <input type="number" name="duracion" min="5" step="5" value="{{ s.duracion if s else '' }}" placeholder="30" required>
      </div>
    </div>
    <div class="field" style="margin-top:1rem">
      <label>Descripción</label>
      <input type="text" name="desc" value="{{ s.desc if s else '' }}" placeholder="Breve descripción">
    </div>
    <div class="bf-acciones" style="margin-top:1rem">
      <button type="submit" class="btn btn-sm">{{ 'Guardar cambios' if s else 'Agregar servicio' }}</button>
    </div>
  </form>
{% endmacro %}
{% block contenido %}
<section class="wrap seccion">
  <div class="titulo-seccion">
    <h2>Servicios y precios</h2>
    <p>Agrega, edita o elimina los servicios que ofrece la barbería.</p>
  </div>
  {% with mensajes = get_flashed_messages(with_categories=true) %}
    {% for categoria, texto in mensajes %}
      <div class="flash flash-{{ categoria }}">{{ texto }}</div>
    {% endfor %}
  {% endwith %}
  <div class="barbero-card nuevo">
    <h3>➕ Nuevo servicio</h3>
    {{ form_servicio() }}
  </div>
  {% for s in servicios %}
  <div class="barbero-card">
    <div class="barbero-head">
      <h3>{{ s.nombre }}</h3>
      <span class="horario-badge">${{ s.precio }} · {{ s.duracion }} min</span>
      <span class="contador">{{ s.citas }} cita(s)</span>
    </div>
    {{ form_servicio(s) }}
    <form method="post" action="{{ url_for('servicios_eliminar', sid=s.id) }}"
          onsubmit="return confirm('¿Eliminar «{{ s.nombre }}»?');" class="del-form">
      <button class="btn btn-sm btn-danger" {{ 'disabled' if s.citas > 0 }}>Eliminar</button>
    </form>
  </div>
  {% endfor %}
</section>
{% endblock %}
""",

    "barberos.html": """\
{% extends "base.html" %}
{% block titulo %}Barberos · La Melena de Yenry{% endblock %}
{% macro form_barbero(b=None) %}
  <form class="barbero-form" method="post" action="{{ url_for('barberos_guardar') }}">
    <input type="hidden" name="id" value="{{ b.id if b else '' }}">
    <div class="bf-grid">
      <div class="field">
        <label>Nombre</label>
        <input type="text" name="nombre" value="{{ b.nombre if b else '' }}" placeholder="Nombre del barbero" required>
      </div>
      <div class="field">
        <label>Abre</label>
        <input type="time" name="apertura" value="{{ b.apertura if b else '09:00' }}" required>
      </div>
      <div class="field">
        <label>Cierra</label>
        <input type="time" name="cierre" value="{{ b.cierre if b else '19:00' }}" required>
      </div>
      <div class="field">
        <label>Margen (min)</label>
        <input type="number" name="margen" min="0" step="5" value="{{ b.margen if b else 5 }}" required>
      </div>
    </div>
    <div class="dias-fila">
      <span class="dias-lbl">Días que trabaja:</span>
      {% for i, d in dias %}
        {% set trabaja = (b is none and i != 6) or (b and i not in b.dias_cerrados_set) %}
        <label class="chip-dia">
          <input type="checkbox" name="trabaja" value="{{ i }}" {{ 'checked' if trabaja }}>
          <span>{{ d }}</span>
        </label>
      {% endfor %}
    </div>
    <div class="bf-acciones">
      <button type="submit" class="btn btn-sm">{{ 'Guardar cambios' if b else 'Agregar barbero' }}</button>
    </div>
  </form>
{% endmacro %}
{% block contenido %}
<section class="wrap seccion">

  {# ── Disponibilidad por barbero ── #}
  <div class="disp-nav" style="margin-bottom:2rem">
    <div>
      <h2>Disponibilidad · Barberos</h2>
      <p style="color:var(--tinta-suave);margin-top:0.2rem">{{ fecha_texto }}</p>
    </div>
    <div class="disp-nav-btns">
      <a class="btn btn-light btn-sm" href="{{ url_for('barberos_admin', fecha=prev_fecha) }}">← Anterior</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('barberos_admin') }}">Hoy</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('barberos_admin', fecha=sig_fecha) }}">Siguiente →</a>
      <form method="get" action="{{ url_for('barberos_admin') }}" style="display:inline">
        <input type="date" name="fecha" value="{{ fecha_iso }}" class="disp-input" onchange="this.form.submit()">
      </form>
    </div>
  </div>
  <div style="margin-bottom:2.5rem">
    {% for item in agenda %}
    <div class="disp-barbero">
      <div class="disp-barbero-head">
        <h3>✂️ {{ item.barbero.nombre }}</h3>
        <span class="horario-badge">🕒 {{ item.barbero.horario }}</span>
        <span class="contador">{{ item.barbero.citas }} cita(s)</span>
      </div>
      {% if item.cerrado %}
        <p class="disp-cerrado">No atiende los {{ dias_nombre[fecha_dow] }}s.</p>
      {% elif not item.slots %}
        <p class="disp-cerrado">Sin horario configurado.</p>
      {% else %}
        <div class="disp-slots">
          {% for slot in item.slots %}
            {% if slot.libre %}
            <a class="slot slot-libre"
               href="{{ url_for('agendar', barbero=item.barbero.nombre, fecha=fecha_iso, hora=slot.hora) }}">
              {{ slot.hora }}
            </a>
            {% else %}
            <span class="slot slot-ocupado">{{ slot.hora }}</span>
            {% endif %}
          {% endfor %}
        </div>
        <div class="disp-leyenda">
          <span><span style="color:#1a6b46;font-size:1.1em">■</span> Libre</span>
          <span><span style="color:#c5bdb5;font-size:1.1em">■</span> Ocupado</span>
        </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>

  {# ── Gestión de barberos ── #}
  <div class="titulo-seccion" style="text-align:left;margin-bottom:1.2rem">
    <h2>Gestión de barberos</h2>
    <p>Agrega barberos y define los días y horas en que atiende cada uno.</p>
  </div>
  {% with mensajes = get_flashed_messages(with_categories=true) %}
    {% for categoria, texto in mensajes %}
      <div class="flash flash-{{ categoria }}">{{ texto }}</div>
    {% endfor %}
  {% endwith %}
  <div class="barbero-card nuevo">
    <h3>➕ Nuevo barbero</h3>
    {{ form_barbero() }}
  </div>
  {% for b in barberos %}
  <div class="barbero-card">
    <div class="barbero-head">
      <h3>{{ b.nombre }}</h3>
      <span class="horario-badge">🕒 {{ b.horario }}</span>
      <span class="horario-badge">⏳ {{ b.margen }} min entre turnos</span>
      <span class="contador">{{ b.citas }} cita(s)</span>
    </div>
    {{ form_barbero(b) }}
    <form method="post" action="{{ url_for('barberos_eliminar', bid=b.id) }}"
          onsubmit="return confirm('¿Eliminar a {{ b.nombre }}?');" class="del-form">
      <button class="btn btn-sm btn-danger" {{ 'disabled' if b.citas > 0 }}>Eliminar</button>
    </form>
  </div>
  {% endfor %}
</section>
{% endblock %}
""",

    "clientes.html": """\
{% extends "base.html" %}
{% block titulo %}Clientes · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="titulo-seccion">
    <h2>Clientes</h2>
    <p>Historial y datos de contacto de quienes han reservado en la barbería.</p>
  </div>
  {% with mensajes = get_flashed_messages(with_categories=true) %}
    {% for categoria, texto in mensajes %}
      <div class="flash flash-{{ categoria }}">{{ texto }}</div>
    {% endfor %}
  {% endwith %}
  <div class="barbero-card nuevo">
    <h3>➕ Nuevo cliente</h3>
    <form class="barbero-form" method="post" action="{{ url_for('clientes_guardar') }}">
      <div class="sf-grid">
        <div class="field">
          <label>Nombre</label>
          <input type="text" name="nombre" placeholder="Ej. Juan Rodríguez" required>
        </div>
        <div class="field">
          <label>Teléfono</label>
          <input type="text" name="telefono" placeholder="809-000-0000">
        </div>
      </div>
      <div class="field" style="margin-top:1rem">
        <label>Notas</label>
        <input type="text" name="notas" placeholder="Preferencias, alergias, etc.">
      </div>
      <div class="bf-acciones" style="margin-top:1rem">
        <button type="submit" class="btn btn-sm">Agregar cliente</button>
      </div>
    </form>
  </div>
  {% if clientes %}
  <div class="clientes-tabla">
    <div class="ct-head">
      <span>Cliente</span><span>Teléfono</span><span>Visitas</span><span>Gastado</span><span>Última visita</span>
    </div>
    {% for c in clientes %}
    <a class="ct-fila" href="{{ url_for('cliente_detalle', cid=c.id) }}">
      <span class="ct-nombre">{{ c.nombre }}</span>
      <span>{{ c.telefono or '—' }}</span>
      <span>{{ c.visitas }}</span>
      <span>${{ c.total_gastado }}</span>
      <span>{{ c.ultima_visita or '—' }}</span>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <p class="vacio">Aún no hay clientes registrados. Agenda una cita o agrega uno arriba.</p>
  {% endif %}
</section>
{% endblock %}
""",

    "cliente_detalle.html": """\
{% extends "base.html" %}
{% block titulo %}{{ cliente.nombre }} · Clientes{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="titulo-seccion">
    <a href="{{ url_for('clientes_admin') }}" class="volver">← Volver a clientes</a>
    <h2>{{ cliente.nombre }}</h2>
    <p>Cliente desde {{ cliente.creado }}.</p>
  </div>
  {% with mensajes = get_flashed_messages(with_categories=true) %}
    {% for categoria, texto in mensajes %}
      <div class="flash flash-{{ categoria }}">{{ texto }}</div>
    {% endfor %}
  {% endwith %}
  <div class="cd-stats">
    <div class="stat"><span class="num">{{ visitas }}</span><span class="lbl">Visitas</span></div>
    <div class="stat"><span class="num">${{ total_gastado }}</span><span class="lbl">Total gastado</span></div>
    <div class="stat"><span class="num">{{ citas[0].fecha if citas else '—' }}</span><span class="lbl">Última visita</span></div>
  </div>
  <div class="barbero-card">
    <h3>Datos del cliente</h3>
    <form class="barbero-form" method="post" action="{{ url_for('clientes_guardar') }}">
      <input type="hidden" name="id" value="{{ cliente.id }}">
      <div class="sf-grid">
        <div class="field">
          <label>Nombre</label>
          <input type="text" name="nombre" value="{{ cliente.nombre }}" required>
        </div>
        <div class="field">
          <label>Teléfono</label>
          <input type="text" name="telefono" value="{{ cliente.telefono or '' }}" placeholder="809-000-0000">
        </div>
      </div>
      <div class="field" style="margin-top:1rem">
        <label>Notas</label>
        <input type="text" name="notas" value="{{ cliente.notas or '' }}" placeholder="Preferencias, alergias, etc.">
      </div>
      <div class="bf-acciones" style="margin-top:1rem">
        <button type="submit" class="btn btn-sm">Guardar cambios</button>
      </div>
    </form>
    <form method="post" action="{{ url_for('clientes_eliminar', cid=cliente.id) }}"
          onsubmit="return confirm('¿Eliminar a «{{ cliente.nombre }}»?');" class="del-form">
      <button class="btn btn-sm btn-danger" {{ 'disabled' if visitas > 0 }}>Eliminar</button>
    </form>
  </div>
  <div class="barbero-card">
    <h3>Historial de citas</h3>
    {% if citas %}
    <div class="clientes-tabla">
      <div class="ct-head ct-head-citas">
        <span>Fecha</span><span>Hora</span><span>Servicio</span><span>Barbero</span><span>Precio</span>
      </div>
      {% for c in citas %}
      <div class="ct-fila ct-fila-citas">
        <span>{{ c.fecha }}</span>
        <span>{{ c.hora }}</span>
        <span>{{ c.servicio_nombre or '—' }}</span>
        <span>{{ c.barbero }}</span>
        <span>${{ c.servicio_precio or 0 }}</span>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <p class="vacio">Este cliente aún no tiene citas registradas.</p>
    {% endif %}
  </div>
</section>
{% endblock %}
""",

    "reportes.html": """\
{% extends "base.html" %}
{% block titulo %}Estadísticas · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">

  {# ── Cabecera ── #}
  <div class="cal-head" style="margin-bottom:2rem">
    <div>
      <h2>Estadísticas</h2>
      <p style="color:var(--tinta-suave);margin-top:0.3rem;font-size:0.88rem">{{ fecha_hoy }}</p>
    </div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
      <a class="btn btn-light btn-sm" href="{{ url_for('reportes', anio=nav_prev.anio, mes=nav_prev.mes) }}">← Anterior</a>
      <span style="font-weight:600;font-size:0.9rem;color:var(--tinta-suave)">{{ nombre_mes }} {{ anio }}</span>
      <a class="btn btn-light btn-sm" href="{{ url_for('reportes', anio=nav_sig.anio, mes=nav_sig.mes) }}">Siguiente →</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('reportes') }}">Este mes</a>
      <a class="btn btn-sm" href="{{ url_for('reportes_export', anio=anio, mes=mes) }}">⬇ CSV</a>
    </div>
  </div>

  {# ── Hoy ── #}
  <p class="rep-subtitulo">Hoy</p>
  <div class="dash-stats rep-grid-auto" style="margin-bottom:1.5rem">
    <div class="dash-stat">
      <div class="ds-icon">📅</div>
      <div class="ds-body"><div class="ds-num">{{ citas_hoy_count }}</div><div class="ds-lbl">Citas hoy</div></div>
    </div>
    <div class="dash-stat">
      <div class="ds-icon">💰</div>
      <div class="ds-body"><div class="ds-num">${{ ingresos_hoy }}</div><div class="ds-lbl">Ingresos hoy</div></div>
    </div>
  </div>

  {# ── Mes seleccionado ── #}
  <p class="rep-subtitulo">{{ nombre_mes }} {{ anio }}</p>
  <div class="dash-stats rep-grid-auto" style="margin-bottom:1.5rem">
    <div class="dash-stat">
      <div class="ds-icon">🗓️</div>
      <div class="ds-body"><div class="ds-num">{{ resumen.citas }}</div><div class="ds-lbl">Citas del mes</div></div>
    </div>
    <div class="dash-stat">
      <div class="ds-icon">📊</div>
      <div class="ds-body"><div class="ds-num">${{ resumen.ingresos }}</div><div class="ds-lbl">Ingresos del mes</div></div>
    </div>
    <div class="dash-stat">
      <div class="ds-icon">🎯</div>
      <div class="ds-body"><div class="ds-num">${{ resumen.ticket }}</div><div class="ds-lbl">Ticket promedio</div></div>
    </div>
  </div>

  {# ── General / histórico ── #}
  <p class="rep-subtitulo">General</p>
  <div class="dash-stats rep-grid-auto" style="margin-bottom:2.5rem">
    <div class="dash-stat">
      <div class="ds-icon">💎</div>
      <div class="ds-body"><div class="ds-num">${{ total_historico }}</div><div class="ds-lbl">Ingresos históricos</div></div>
    </div>
    <div class="dash-stat">
      <div class="ds-icon">👥</div>
      <div class="ds-body"><div class="ds-num">{{ total_clientes }}</div><div class="ds-lbl">Clientes registrados</div></div>
    </div>
  </div>

  {# ── Desglose por barbero y servicio ── #}
  <p class="rep-subtitulo">Desglose · {{ nombre_mes }} {{ anio }}</p>
  <div class="rep-cols" style="margin-top:1rem">
    <div class="barbero-card">
      <h3>Por barbero</h3>
      {% if por_barbero %}
      <div class="rep-lista">
        {% for b in por_barbero %}
        <div class="rep-fila">
          <div class="rep-top">
            <span class="rep-nombre">{{ b.barbero }}</span>
            <span class="rep-monto">${{ b.ingresos }} <small>· {{ b.citas }} cita(s)</small></span>
          </div>
          <div class="rep-barra"><span style="width:{{ (b.ingresos/max_barbero*100)|round }}%"></span></div>
        </div>
        {% endfor %}
      </div>
      {% else %}<p class="vacio">Sin citas este mes.</p>{% endif %}
    </div>
    <div class="barbero-card">
      <h3>Por servicio</h3>
      {% if por_servicio %}
      <div class="rep-lista">
        {% for s in por_servicio %}
        <div class="rep-fila">
          <div class="rep-top">
            <span class="rep-nombre">{{ s.servicio }}</span>
            <span class="rep-monto">${{ s.ingresos }} <small>· {{ s.veces }}×</small></span>
          </div>
          <div class="rep-barra"><span style="width:{{ (s.ingresos/max_servicio*100)|round }}%"></span></div>
        </div>
        {% endfor %}
      </div>
      {% else %}<p class="vacio">Sin citas este mes.</p>{% endif %}
    </div>
  </div>

</section>
{% endblock %}
""",

    "disponibilidad.html": """\
{% extends "base.html" %}
{% block titulo %}Disponibilidad · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="disp-nav">
    <div>
      <h2>Disponibilidad</h2>
      <p style="color:var(--tinta-suave);margin-top:0.2rem">{{ fecha_texto }}</p>
    </div>
    <div class="disp-nav-btns">
      <a class="btn btn-light btn-sm" href="{{ url_for('disponibilidad', fecha=prev_fecha) }}">← Anterior</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('disponibilidad') }}">Hoy</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('disponibilidad', fecha=sig_fecha) }}">Siguiente →</a>
      <form method="get" action="{{ url_for('disponibilidad') }}" style="display:inline">
        <input type="date" name="fecha" value="{{ fecha_iso }}" class="disp-input" onchange="this.form.submit()">
      </form>
    </div>
  </div>

  {% for item in agenda %}
  <div class="disp-barbero">
    <div class="disp-barbero-head">
      <h3>✂️ {{ item.barbero.nombre }}</h3>
      <span class="horario-badge">🕒 {{ item.barbero.horario }}</span>
    </div>
    {% if item.cerrado %}
      <p class="disp-cerrado">Este barbero no atiende los {{ dias_nombre[fecha_dow] }}s.</p>
    {% elif not item.slots %}
      <p class="disp-cerrado">Sin horario configurado para este día.</p>
    {% else %}
      <div class="disp-slots">
        {% for slot in item.slots %}
          {% if slot.libre %}
          <a class="slot slot-libre"
             href="{{ url_for('agendar', barbero=item.barbero.nombre, fecha=fecha_iso, hora=slot.hora) }}"
             title="Clic para agendar a las {{ slot.hora }}">
            {{ slot.hora }}
          </a>
          {% else %}
          <span class="slot slot-ocupado" title="Ocupado">{{ slot.hora }}</span>
          {% endif %}
        {% endfor %}
      </div>
      <div class="disp-leyenda">
        <span><span style="color:#1a6b46;font-size:1.1em">■</span> Disponible — haz clic para agendar</span>
        <span><span style="color:#c5bdb5;font-size:1.1em">■</span> Ocupado</span>
      </div>
    {% endif %}
  </div>
  {% endfor %}
</section>
{% endblock %}
""",

    "login.html": """\
{% extends "base.html" %}
{% block titulo %}Acceso admin · La Melena de Yenry{% endblock %}
{% block contenido %}
<div class="login-wrap">
  <div class="login-card">
    <div class="login-logo"></div>
    <h2>Área de administración</h2>
    <p class="sub">Introduce la contraseña para acceder a los datos de clientes y reportes.</p>
    {% with mensajes = get_flashed_messages(with_categories=true) %}
      {% for categoria, texto in mensajes %}
        <div class="flash flash-{{ categoria }}" style="margin-bottom:1rem">{{ texto }}</div>
      {% endfor %}
    {% endwith %}
    <form method="post" action="{{ url_for('login') }}{% if request.args.get('next') %}?next={{ request.args.get('next') | urlencode }}{% endif %}">
      <div class="field" style="margin-bottom:1.2rem">
        <label for="password">Contraseña de administrador</label>
        <input type="password" id="password" name="password" placeholder="••••••••" autofocus required>
      </div>
      <button type="submit" class="btn" style="width:100%;justify-content:center">Entrar →</button>
    </form>
  </div>
</div>
{% endblock %}
""",

    "recordatorios.html": """\
{% extends "base.html" %}
{% block titulo %}Recordatorios · La Melena de Yenry{% endblock %}
{% macro rec_card(c, destacar=False) %}
  <div class="rec-card {{ 'enviado' if c.recordatorio_enviado }} {{ 'destacar' if destacar }}">
    <div class="rec-cuando">
      <span class="rec-dia">{{ c.etiqueta }}</span>
      <span class="rec-hora">{{ c.hora }}</span>
    </div>
    <div class="rec-info">
      <span class="rec-cliente">{{ c.cliente }}{% if c.recordatorio_enviado %} <span class="rec-ok">✓ recordado</span>{% endif %}</span>
      <span class="rec-detalle">{{ c.servicio_nombre }} · {{ c.barbero }}</span>
      <span class="rec-tel">{{ c.telefono or 'Sin teléfono' }}</span>
    </div>
    <div class="rec-acciones">
      {% if c.wa %}
      <a class="btn btn-sm wa-btn" href="{{ c.wa }}" target="_blank" rel="noopener">WhatsApp</a>
      {% endif %}
      <form method="post" action="{{ url_for('recordatorios_marcar', cita_id=c.id) }}">
        <input type="hidden" name="enviado" value="{{ '0' if c.recordatorio_enviado else '1' }}">
        <button class="btn btn-sm {{ 'btn-light' if c.recordatorio_enviado else '' }}">
          {{ 'Deshacer' if c.recordatorio_enviado else 'Marcar recordado' }}
        </button>
      </form>
    </div>
  </div>
{% endmacro %}
{% block contenido %}
<section class="wrap seccion">
  <div class="titulo-seccion">
    <h2>Recordatorios de citas</h2>
    <p>La app detecta sola qué citas toca recordar. Envía el aviso por WhatsApp y márcalo.</p>
  </div>
  {% with mensajes = get_flashed_messages(with_categories=true) %}
    {% for categoria, texto in mensajes %}
      <div class="flash flash-{{ categoria }}">{{ texto }}</div>
    {% endfor %}
  {% endwith %}
  <form class="rec-config" method="post" action="{{ url_for('recordatorios_config') }}">
    <label for="anticipacion">Recordar con</label>
    <input type="number" id="anticipacion" name="anticipacion" min="0" max="30" value="{{ anticipacion }}">
    <span>día(s) de anticipación</span>
    <button type="submit" class="btn btn-sm btn-light">Guardar</button>
  </form>
  <div class="rec-bloque">
    <h3 class="rec-titulo">🔔 Por recordar
      {% if por_recordar %}<span class="rec-badge">{{ por_recordar|length }}</span>{% endif %}
    </h3>
    {% if por_recordar %}
    <div class="rec-lista">{% for c in por_recordar %}{{ rec_card(c, destacar=True) }}{% endfor %}</div>
    {% else %}
    <p class="vacio">Nada por recordar dentro de la ventana. ¡Todo al día!</p>
    {% endif %}
  </div>
  {% if programadas %}
  <div class="rec-bloque">
    <h3 class="rec-titulo">Programadas</h3>
    <div class="rec-lista">{% for c in programadas %}{{ rec_card(c) }}{% endfor %}</div>
  </div>
  {% endif %}
</section>
{% endblock %}
""",
}

# ===========================================================================
#  APLICACIÓN FLASK
# ===========================================================================
app = Flask(__name__)
app.secret_key = "barberia-la-melena-de-yenry-2024"
app.jinja_loader = DictLoader(TEMPLATES)
init_db()

MESES_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
MESES_LARGO = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
               "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fecha_bonita(d: date) -> str:
    dia = DIAS_NOMBRE[d.weekday()].capitalize()
    return f"{dia}, {d.day} de {MESES_LARGO[d.month]} de {d.year}"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            flash("Debes iniciar sesión para acceder a esa sección.", "error")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def _a_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def horario_texto(barbero):
    cerrados = barbero["dias_cerrados_set"]
    abiertos = [i for i in range(7) if i not in cerrados]
    if not abiertos:
        dias = "Sin días disponibles"
    elif abiertos == list(range(abiertos[0], abiertos[-1] + 1)):
        dias = f"{DIAS_ES[abiertos[0]]}–{DIAS_ES[abiertos[-1]]}"
    else:
        dias = ", ".join(DIAS_ES[i] for i in abiertos)
    return f"{dias} {barbero['apertura']}–{barbero['cierre']}"


def validar_horario(barbero, fecha_iso, hora, duracion):
    d = date.fromisoformat(fecha_iso)
    if d.weekday() in barbero["dias_cerrados_set"]:
        dia = DIAS_NOMBRE[d.weekday()]
        dia_plural = dia if dia.endswith("s") else dia + "s"
        return (f"{barbero['nombre']} no atiende los {dia_plural} "
                f"(horario: {horario_texto(barbero)}). Elige otro día u otro barbero.")
    inicio = _a_min(hora)
    fin = inicio + duracion
    if inicio < _a_min(barbero["apertura"]):
        return (f"{barbero['nombre']} abre a las {barbero['apertura']}. "
                f"Elige una hora más tarde u otro barbero.")
    if fin > _a_min(barbero["cierre"]):
        return (f"Ese servicio dura {duracion} min y terminaría después del cierre de "
                f"{barbero['nombre']} ({barbero['cierre']}). Elige una hora más temprana.")
    return None


def anticipacion_dias():
    try:
        return int(get_config("anticipacion_dias", 1))
    except (TypeError, ValueError):
        return 1


def contar_por_recordar():
    hoy = date.today()
    limite = (hoy + timedelta(days=anticipacion_dias())).isoformat()
    return sum(
        1 for c in citas_proximas(hoy.isoformat())
        if not c["recordatorio_enviado"] and c["fecha"] <= limite
    )


# ===========================================================================
#  RUTAS
# ===========================================================================

# Foto hero embebida en base64 (no necesita archivo externo)
_HERO_B64 = "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"

@app.route("/hero")
def hero_image():
    import base64 as _b64
    return Response(_b64.b64decode(_HERO_B64), mimetype="image/jpeg")


@app.route("/")
def dashboard():
    hoy = date.today()
    hoy_iso = hoy.isoformat()
    citas_hoy = citas_por_fecha(hoy_iso)
    ingresos_hoy = sum(
        (servicio_por_id(c["servicio_id"]) or {}).get("precio", 0) for c in citas_hoy
    )
    mes_resumen = ingresos_resumen(hoy.year, hoy.month)
    total_clientes = len(listar_clientes())
    limite_semana = (hoy + timedelta(days=7)).isoformat()
    proximas = [
        c for c in citas_proximas((hoy + timedelta(days=1)).isoformat())
        if c["fecha"] <= limite_semana
    ]
    return render_template(
        "dashboard.html",
        citas_hoy=citas_hoy,
        ingresos_hoy=ingresos_hoy,
        mes_resumen=mes_resumen,
        total_clientes=total_clientes,
        total_citas=len(listar_citas()),
        proximas=proximas,
        hoy_iso=hoy_iso,
        fecha_hoy=fecha_bonita(hoy),
        por_recordar=contar_por_recordar(),
        servicio_por_id=servicio_por_id,
        seccion="inicio",
    )


@app.route("/cortes")
def cortes():
    return render_template("cortes.html", servicios=listar_servicios(), seccion="cortes")


@app.route("/agendar", methods=["GET", "POST"])
def agendar():
    if request.method == "POST":
        nueva = {
            "cliente": request.form.get("cliente", "").strip(),
            "telefono": request.form.get("telefono", "").strip(),
            "servicio_id": int(request.form.get("servicio_id", 1)),
            "barbero": request.form.get("barbero", ""),
            "fecha": request.form.get("fecha") or date.today().isoformat(),
            "hora": request.form.get("hora", "10:00"),
        }
        servicio = servicio_por_id(nueva["servicio_id"])
        barbero = barbero_por_nombre(nueva["barbero"])
        if barbero is None:
            error = "Selecciona un barbero válido."
        else:
            error = validar_horario(barbero, nueva["fecha"], nueva["hora"], servicio["duracion"])
        if not error:
            choque = buscar_choque(
                nueva["barbero"], nueva["fecha"], nueva["hora"],
                servicio["duracion"], margen=barbero["margen"],
            )
            if choque:
                s_choque = servicio_por_id(choque["servicio_id"])
                extra = (f" (se necesitan {barbero['margen']} min libres entre turnos)"
                         if barbero["margen"] else "")
                error = (f"{nueva['barbero']} ya tiene una cita el {nueva['fecha']} a las "
                         f"{choque['hora']} ({s_choque['nombre']}, {s_choque['duracion']} min) "
                         f"con {choque['cliente']}{extra}. Elige otro horario o barbero.")
        if error:
            return render_template("agendar.html", **_ctx_agendar(valores=nueva, error=error))
        if nueva["cliente"]:
            nueva["cliente_id"] = buscar_o_crear_cliente(nueva["cliente"], nueva["telefono"])
            agregar_cita(nueva)
        return redirect(url_for("calendario"))
    pre = None
    if any(request.args.get(k) for k in ("barbero", "fecha", "hora")):
        pre = {
            "cliente": "",
            "telefono": "",
            "servicio_id": 1,
            "barbero": request.args.get("barbero", ""),
            "fecha": request.args.get("fecha", date.today().isoformat()),
            "hora": request.args.get("hora", "10:00"),
        }
    return render_template("agendar.html", **_ctx_agendar(valores=pre))


def _ctx_agendar(valores, error=None):
    barberos = listar_barberos()
    for b in barberos:
        b["horario"] = horario_texto(b)
    aperturas = [_a_min(b["apertura"]) for b in barberos] or [_a_min("09:00")]
    cierres = [_a_min(b["cierre"]) for b in barberos] or [_a_min("19:00")]
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return dict(
        servicios=listar_servicios(),
        barberos=barberos,
        clientes=listar_clientes() if session.get("admin") else [],
        hoy=date.today().isoformat(),
        seccion="agendar",
        valores=valores,
        error=error,
        apertura=fmt(min(aperturas)),
        cierre=fmt(max(cierres)),
    )


@app.route("/eliminar/<int:cita_id>", methods=["POST"])
def eliminar(cita_id):
    eliminar_cita(cita_id)
    return redirect(request.referrer or url_for("calendario"))


@app.route("/calendario")
def calendario():
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))

    por_fecha = defaultdict(list)
    for c in listar_citas():
        por_fecha[c["fecha"]].append(c)

    cal = calendar.Calendar(firstweekday=0)
    semanas = []
    for semana in cal.monthdatescalendar(anio, mes):
        fila = []
        for dia in semana:
            iso = dia.isoformat()
            fila.append({
                "dia": dia.day,
                "iso": iso,
                "del_mes": dia.month == mes,
                "es_hoy": dia == hoy,
                "citas": sorted(por_fecha.get(iso, []), key=lambda c: c["hora"]),
            })
        semanas.append(fila)

    mes_prev = (mes - 1) or 12
    anio_prev = anio - 1 if mes == 1 else anio
    mes_sig = (mes % 12) + 1
    anio_sig = anio + 1 if mes == 12 else anio

    return render_template(
        "calendario.html",
        semanas=semanas,
        dias_semana=DIAS_ES,
        nombre_mes=MESES_ES[mes],
        anio=anio,
        mes=mes,
        nav_prev={"anio": anio_prev, "mes": mes_prev},
        nav_sig={"anio": anio_sig, "mes": mes_sig},
        servicio_por_id=servicio_por_id,
        seccion="calendario",
    )


@app.route("/barberos")
@login_required
def barberos_admin():
    hoy = date.today()
    fecha_str = request.args.get("fecha", hoy.isoformat())
    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        fecha = hoy
    fecha_iso = fecha.isoformat()

    barberos = listar_barberos()
    agenda = []
    for b in barberos:
        b["horario"] = horario_texto(b)
        b["citas"] = citas_de_barbero(b["nombre"])
        cerrado = fecha.weekday() in b["dias_cerrados_set"]
        agenda.append({
            "barbero": b,
            "cerrado": cerrado,
            "slots": [] if cerrado else slots_disponibles(b["nombre"], fecha_iso),
        })
    return render_template(
        "barberos.html",
        barberos=barberos,
        agenda=agenda,
        fecha_iso=fecha_iso,
        fecha_texto=fecha_bonita(fecha),
        prev_fecha=(fecha - timedelta(days=1)).isoformat(),
        sig_fecha=(fecha + timedelta(days=1)).isoformat(),
        dias=list(enumerate(DIAS_ES)),
        dias_nombre=DIAS_NOMBRE,
        fecha_dow=fecha.weekday(),
        seccion="barberos",
    )


def _dias_cerrados_del_form():
    trabaja = set(request.form.getlist("trabaja"))
    return ",".join(str(i) for i in range(7) if str(i) not in trabaja)


@app.route("/barberos/guardar", methods=["POST"])
@login_required
def barberos_guardar():
    bid = request.form.get("id", "").strip()
    nombre = request.form.get("nombre", "").strip()
    apertura = request.form.get("apertura", "09:00")
    cierre = request.form.get("cierre", "19:00")
    dias_cerrados = _dias_cerrados_del_form()
    try:
        margen = int(request.form.get("margen", 5))
    except ValueError:
        margen = 5

    if not nombre:
        flash("El nombre del barbero no puede estar vacío.", "error")
        return redirect(url_for("barberos_admin"))
    if _a_min(apertura) >= _a_min(cierre):
        flash("La hora de apertura debe ser anterior a la de cierre.", "error")
        return redirect(url_for("barberos_admin"))
    if dias_cerrados == "0,1,2,3,4,5,6":
        flash("Debes marcar al menos un día de trabajo.", "error")
        return redirect(url_for("barberos_admin"))
    if margen < 0:
        flash("El margen entre turnos no puede ser negativo.", "error")
        return redirect(url_for("barberos_admin"))

    excluir = int(bid) if bid else None
    if nombre_barbero_existe(nombre, excluir_id=excluir):
        flash(f"Ya existe un barbero llamado «{nombre}».", "error")
        return redirect(url_for("barberos_admin"))

    if bid:
        actualizar_barbero(int(bid), nombre, apertura, cierre, dias_cerrados, margen)
        flash(f"Horario de {nombre} actualizado.", "ok")
    else:
        agregar_barbero(nombre, apertura, cierre, dias_cerrados, margen)
        flash(f"Barbero {nombre} agregado.", "ok")
    return redirect(url_for("barberos_admin"))


@app.route("/barberos/eliminar/<int:bid>", methods=["POST"])
@login_required
def barberos_eliminar(bid):
    barbero = barbero_por_id(bid)
    if barbero is None:
        flash("Ese barbero ya no existe.", "error")
    elif citas_de_barbero(barbero["nombre"]) > 0:
        flash(f"No puedes eliminar a {barbero['nombre']}: tiene citas registradas. "
              f"Cancélalas primero.", "error")
    else:
        eliminar_barbero(bid)
        flash(f"Barbero {barbero['nombre']} eliminado.", "ok")
    return redirect(url_for("barberos_admin"))


@app.route("/servicios")
@login_required
def servicios_admin():
    servicios = listar_servicios()
    for s in servicios:
        s["citas"] = citas_de_servicio(s["id"])
    return render_template("servicios.html", servicios=servicios, seccion="servicios")


@app.route("/servicios/guardar", methods=["POST"])
@login_required
def servicios_guardar():
    sid = request.form.get("id", "").strip()
    nombre = request.form.get("nombre", "").strip()
    desc = request.form.get("desc", "").strip()
    try:
        precio = int(request.form.get("precio", ""))
        duracion = int(request.form.get("duracion", ""))
    except ValueError:
        flash("Precio y duración deben ser números enteros.", "error")
        return redirect(url_for("servicios_admin"))

    if not nombre:
        flash("El nombre del servicio no puede estar vacío.", "error")
        return redirect(url_for("servicios_admin"))
    if precio < 0 or duracion <= 0:
        flash("El precio no puede ser negativo y la duración debe ser mayor a 0.", "error")
        return redirect(url_for("servicios_admin"))

    if sid:
        actualizar_servicio(int(sid), nombre, precio, duracion, desc)
        flash(f"Servicio «{nombre}» actualizado.", "ok")
    else:
        agregar_servicio(nombre, precio, duracion, desc)
        flash(f"Servicio «{nombre}» agregado.", "ok")
    return redirect(url_for("servicios_admin"))


@app.route("/servicios/eliminar/<int:sid>", methods=["POST"])
@login_required
def servicios_eliminar(sid):
    servicio = servicio_por_id(sid)
    if servicio is None:
        flash("Ese servicio ya no existe.", "error")
    elif citas_de_servicio(sid) > 0:
        flash(f"No puedes eliminar «{servicio['nombre']}»: hay citas que lo usan. "
              f"Cancélalas primero.", "error")
    else:
        eliminar_servicio(sid)
        flash(f"Servicio «{servicio['nombre']}» eliminado.", "ok")
    return redirect(url_for("servicios_admin"))


@app.route("/recordatorios")
@login_required
def recordatorios():
    hoy = date.today()
    hoy_iso = hoy.isoformat()
    manana = (hoy + timedelta(days=1)).isoformat()
    anticipacion = anticipacion_dias()
    limite = (hoy + timedelta(days=anticipacion)).isoformat()

    citas = citas_proximas(hoy_iso)
    por_recordar, programadas = [], []
    for c in citas:
        tel = "".join(ch for ch in (c["telefono"] or "") if ch.isdigit())
        if c["fecha"] == hoy_iso:
            c["etiqueta"] = "Hoy"
        elif c["fecha"] == manana:
            c["etiqueta"] = "Mañana"
        else:
            c["etiqueta"] = c["fecha"]
        if tel:
            msg = (f"Hola {c['cliente']}, te recordamos tu cita en La Melena de Yenry el "
                   f"{c['fecha']} a las {c['hora']} para {c['servicio_nombre']} con "
                   f"{c['barbero']}. ¡Te esperamos!")
            c["wa"] = f"https://wa.me/{tel}?text={quote(msg)}"
        else:
            c["wa"] = None
        if not c["recordatorio_enviado"] and c["fecha"] <= limite:
            por_recordar.append(c)
        else:
            programadas.append(c)

    return render_template(
        "recordatorios.html",
        por_recordar=por_recordar,
        programadas=programadas,
        anticipacion=anticipacion,
        seccion="recordatorios",
    )


@app.route("/recordatorios/<int:cita_id>/marcar", methods=["POST"])
@login_required
def recordatorios_marcar(cita_id):
    marcar_recordatorio(cita_id, request.form.get("enviado") == "1")
    return redirect(url_for("recordatorios"))


@app.route("/recordatorios/config", methods=["POST"])
@login_required
def recordatorios_config():
    try:
        dias = max(0, min(30, int(request.form.get("anticipacion", 1))))
    except ValueError:
        dias = 1
    set_config("anticipacion_dias", dias)
    flash(f"Ahora se recordarán las citas con {dias} día(s) de anticipación.", "ok")
    return redirect(url_for("recordatorios"))


@app.route("/reportes")
@login_required
def reportes():
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))

    resumen = ingresos_resumen(anio, mes)
    por_barbero = ingresos_por_barbero(anio, mes)
    por_servicio = ingresos_por_servicio(anio, mes)
    max_barbero = max((b["ingresos"] for b in por_barbero), default=1) or 1
    max_servicio = max((s["ingresos"] for s in por_servicio), default=1) or 1

    mes_prev = (mes - 1) or 12
    anio_prev = anio - 1 if mes == 1 else anio
    mes_sig = (mes % 12) + 1
    anio_sig = anio + 1 if mes == 12 else anio

    citas_hoy = citas_por_fecha(hoy.isoformat())
    ingresos_hoy = sum(
        (servicio_por_id(c["servicio_id"]) or {}).get("precio", 0) for c in citas_hoy
    )

    return render_template(
        "reportes.html",
        resumen=resumen,
        por_barbero=por_barbero,
        por_servicio=por_servicio,
        max_barbero=max_barbero,
        max_servicio=max_servicio,
        total_historico=ingresos_total(),
        total_clientes=len(listar_clientes()),
        citas_hoy_count=len(citas_hoy),
        ingresos_hoy=ingresos_hoy,
        fecha_hoy=fecha_bonita(hoy),
        nombre_mes=MESES_ES[mes],
        anio=anio,
        mes=mes,
        nav_prev={"anio": anio_prev, "mes": mes_prev},
        nav_sig={"anio": anio_sig, "mes": mes_sig},
        seccion="reportes",
    )


@app.route("/reportes/export.csv")
@login_required
def reportes_export():
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))
    filas = citas_detalle_mes(anio, mes)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Fecha", "Hora", "Cliente", "Servicio", "Barbero", "Precio"])
    for f in filas:
        w.writerow([f["fecha"], f["hora"], f["cliente"], f["servicio"], f["barbero"], f["precio"]])
    w.writerow([])
    w.writerow(["", "", "", "", "Total", sum(f["precio"] for f in filas)])

    contenido = "﻿" + buf.getvalue()  # BOM para Excel
    nombre = f"ingresos_{anio:04d}-{mes:02d}.csv"
    return Response(
        contenido,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={nombre}"},
    )


@app.route("/disponibilidad")
def disponibilidad():
    hoy = date.today()
    fecha_str = request.args.get("fecha", hoy.isoformat())
    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        fecha = hoy
    fecha_iso = fecha.isoformat()
    barberos = listar_barberos()
    agenda = []
    for b in barberos:
        b["horario"] = horario_texto(b)
        cerrado = fecha.weekday() in b["dias_cerrados_set"]
        agenda.append({
            "barbero": b,
            "cerrado": cerrado,
            "slots": [] if cerrado else slots_disponibles(b["nombre"], fecha_iso),
        })
    return render_template(
        "disponibilidad.html",
        agenda=agenda,
        fecha_iso=fecha_iso,
        fecha_texto=fecha_bonita(fecha),
        prev_fecha=(fecha - timedelta(days=1)).isoformat(),
        sig_fecha=(fecha + timedelta(days=1)).isoformat(),
        dias_nombre=DIAS_NOMBRE,
        fecha_dow=fecha.weekday(),
        seccion="disponibilidad",
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        password = request.form.get("password", "")
        stored = get_config("admin_password", "admin123")
        if password == stored:
            session["admin"] = True
            flash("Sesión iniciada correctamente.", "ok")
            destino = request.args.get("next") or url_for("clientes_admin")
            return redirect(destino)
        flash("Contraseña incorrecta. Inténtalo de nuevo.", "error")
    return render_template("login.html", seccion=None)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("admin", None)
    flash("Sesión cerrada.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/clientes")
@login_required
def clientes_admin():
    return render_template("clientes.html", clientes=listar_clientes(), seccion="clientes")


@app.route("/clientes/<int:cid>")
@login_required
def cliente_detalle(cid):
    cliente = cliente_por_id(cid)
    if cliente is None:
        flash("Ese cliente ya no existe.", "error")
        return redirect(url_for("clientes_admin"))
    citas = citas_de_cliente(cid)
    total_gastado = sum(c["servicio_precio"] or 0 for c in citas)
    return render_template(
        "cliente_detalle.html",
        cliente=cliente,
        citas=citas,
        visitas=len(citas),
        total_gastado=total_gastado,
        seccion="clientes",
    )


@app.route("/clientes/guardar", methods=["POST"])
@login_required
def clientes_guardar():
    cid = request.form.get("id", "").strip()
    nombre = request.form.get("nombre", "").strip()
    telefono = request.form.get("telefono", "").strip()
    notas = request.form.get("notas", "").strip()

    if not nombre:
        flash("El nombre del cliente no puede estar vacío.", "error")
        return redirect(request.referrer or url_for("clientes_admin"))

    if cid:
        actualizar_cliente(int(cid), nombre, telefono, notas)
        flash(f"Cliente «{nombre}» actualizado.", "ok")
        return redirect(url_for("cliente_detalle", cid=int(cid)))
    else:
        nuevo_id = agregar_cliente(nombre, telefono, notas)
        flash(f"Cliente «{nombre}» agregado.", "ok")
        return redirect(url_for("cliente_detalle", cid=nuevo_id))


@app.route("/clientes/eliminar/<int:cid>", methods=["POST"])
@login_required
def clientes_eliminar(cid):
    cliente = cliente_por_id(cid)
    if cliente is None:
        flash("Ese cliente ya no existe.", "error")
    elif contar_citas_cliente(cid) > 0:
        flash(f"No puedes eliminar a {cliente['nombre']}: tiene citas registradas. "
              f"Cancélalas primero.", "error")
        return redirect(url_for("cliente_detalle", cid=cid))
    else:
        eliminar_cliente(cid)
        flash(f"Cliente {cliente['nombre']} eliminado.", "ok")
    return redirect(url_for("clientes_admin"))


# Punto de entrada WSGI para PythonAnywhere
application = app

if __name__ == "__main__":
    app.run(debug=True, port=5000)
