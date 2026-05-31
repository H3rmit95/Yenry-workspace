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

from flask import Flask, Response, flash, redirect, render_template, request, url_for
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
.brand { display: flex; align-items: center; gap: 0.6rem; font-size: 1.4rem; }
.brand .logo { width: 34px; height: 34px; border-radius: 50%; background: radial-gradient(circle at 30% 30%, var(--dorado), #8a651a); display: inline-block; }
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
        <span class="logo"></span>
        <span class="serif">La Melena de Yenry</span>
      </a>
      <div class="menu">
        <a href="{{ url_for('dashboard') }}" class="{{ 'activo' if seccion=='inicio' }}">Inicio</a>
        <a href="{{ url_for('cortes') }}" class="{{ 'activo' if seccion=='cortes' }}">Cortes y precios</a>
        <a href="{{ url_for('calendario') }}" class="{{ 'activo' if seccion=='calendario' }}">Calendario</a>
        <a href="{{ url_for('servicios_admin') }}" class="{{ 'activo' if seccion=='servicios' }}">Servicios</a>
        <a href="{{ url_for('barberos_admin') }}" class="{{ 'activo' if seccion=='barberos' }}">Barberos</a>
        <a href="{{ url_for('clientes_admin') }}" class="{{ 'activo' if seccion=='clientes' }}">Clientes</a>
        <a href="{{ url_for('reportes') }}" class="{{ 'activo' if seccion=='reportes' }}">Reportes</a>
        <a href="{{ url_for('recordatorios') }}" class="{{ 'activo' if seccion=='recordatorios' }}">Recordatorios</a>
        <a href="{{ url_for('agendar') }}" class="btn btn-sm">Agendar cita</a>
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
{% block titulo %}Inicio · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap hero">
  <div>
    <h1>Tu mejor versión<br>empieza en la silla</h1>
    <p class="sub">El CRM #1 para gestionar tu barbería</p>
    <p>Agenda citas, organiza tu calendario y controla tus servicios desde un solo lugar.</p>
    <div class="cta">
      <a href="{{ url_for('agendar') }}" class="btn">Agendar cita →</a>
      <a href="{{ url_for('cortes') }}" class="btn btn-light">Ver cortes y precios</a>
    </div>
  </div>
  <div class="hero-grid">
    <div class="tile">💈</div><div class="tile">✂️</div><div class="tile">🧔</div>
    <div class="tile">🪒</div><div class="tile">💇</div><div class="tile">🧴</div>
    <div class="tile">⭐</div><div class="tile">📅</div><div class="tile">💈</div>
  </div>
</section>
{% if por_recordar %}
<section class="wrap" style="padding-top:1.5rem">
  <a class="rec-aviso" href="{{ url_for('recordatorios') }}">
    🔔 Tienes <strong>{{ por_recordar }}</strong> recordatorio(s) por enviar. <span>Ir al panel →</span>
  </a>
</section>
{% endif %}
<section class="wrap seccion">
  <div class="stats">
    <div class="stat"><div class="num">{{ citas_hoy|length }}</div><div class="lbl">Citas para hoy</div></div>
    <div class="stat"><div class="num">{{ total_citas }}</div><div class="lbl">Citas activas en total</div></div>
    <div class="stat"><div class="num">${{ ingresos_hoy }}</div><div class="lbl">Ingreso estimado de hoy</div></div>
  </div>
</section>
<section class="banda">
  <div class="wrap seccion">
    <div class="titulo-seccion">
      <h2>Agenda de hoy</h2>
      <p>Las citas programadas para la fecha actual</p>
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
              <small>{{ s.nombre if s else '?' }} · {{ c.barbero }} · {{ c.telefono }}</small>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:1rem">
            <span class="precio" style="font-size:1.3rem">${{ s.precio if s else 0 }}</span>
            <form method="post" action="{{ url_for('eliminar', cita_id=c.id) }}">
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
    <p>Completa los datos y reserva el turno en segundos.</p>
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
          <div class="cal-evt" title="{{ c.cliente }} · {{ s.nombre if s else '?' }} · {{ c.barbero }}">
            <span class="h">{{ c.hora }}</span> {{ c.cliente }}<br>{{ s.nombre if s else '?' }}
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
  <div class="titulo-seccion">
    <h2>Barberos y horarios</h2>
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
{% block titulo %}Reportes · La Melena de Yenry{% endblock %}
{% block contenido %}
<section class="wrap seccion">
  <div class="cal-head">
    <h2>Ingresos · {{ nombre_mes }} {{ anio }}</h2>
    <div style="display:flex;gap:0.5rem">
      <a class="btn btn-light btn-sm" href="{{ url_for('reportes', anio=nav_prev.anio, mes=nav_prev.mes) }}">← Anterior</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('reportes') }}">Este mes</a>
      <a class="btn btn-light btn-sm" href="{{ url_for('reportes', anio=nav_sig.anio, mes=nav_sig.mes) }}">Siguiente →</a>
      <a class="btn btn-sm" href="{{ url_for('reportes_export', anio=anio, mes=mes) }}">⬇ Exportar CSV</a>
    </div>
  </div>
  <div class="cd-stats rep-stats">
    <div class="stat"><span class="num">${{ resumen.ingresos }}</span><span class="lbl">Ingresos del mes</span></div>
    <div class="stat"><span class="num">{{ resumen.citas }}</span><span class="lbl">Citas del mes</span></div>
    <div class="stat"><span class="num">${{ resumen.ticket }}</span><span class="lbl">Ticket promedio</span></div>
    <div class="stat"><span class="num">${{ total_historico }}</span><span class="lbl">Histórico total</span></div>
  </div>
  <div class="rep-cols">
    <div class="barbero-card">
      <h3>Ingresos por barbero</h3>
      {% if por_barbero %}
      <div class="rep-lista">
        {% for b in por_barbero %}
        <div class="rep-fila">
          <div class="rep-top">
            <span class="rep-nombre">{{ b.barbero }}</span>
            <span class="rep-monto">${{ b.ingresos }} <small>· {{ b.citas }} cita(s)</small></span>
          </div>
          <div class="rep-barra"><span style="width: {{ (b.ingresos / max_barbero * 100)|round }}%" ></span></div>
        </div>
        {% endfor %}
      </div>
      {% else %}<p class="vacio">Sin citas este mes.</p>{% endif %}
    </div>
    <div class="barbero-card">
      <h3>Ingresos por servicio</h3>
      {% if por_servicio %}
      <div class="rep-lista">
        {% for s in por_servicio %}
        <div class="rep-fila">
          <div class="rep-top">
            <span class="rep-nombre">{{ s.servicio }}</span>
            <span class="rep-monto">${{ s.ingresos }} <small>· {{ s.veces }}×</small></span>
          </div>
          <div class="rep-barra"><span style="width: {{ (s.ingresos / max_servicio * 100)|round }}%"></span></div>
        </div>
        {% endfor %}
      </div>
      {% else %}<p class="vacio">Sin citas este mes.</p>{% endif %}
    </div>
  </div>
</section>
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
DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


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

@app.route("/")
def dashboard():
    hoy = date.today().isoformat()
    citas_hoy = citas_por_fecha(hoy)
    ingresos_hoy = sum(
        (servicio_por_id(c["servicio_id"]) or {}).get("precio", 0) for c in citas_hoy
    )
    return render_template(
        "dashboard.html",
        servicios=listar_servicios(),
        citas_hoy=citas_hoy,
        total_citas=len(listar_citas()),
        ingresos_hoy=ingresos_hoy,
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
    return render_template("agendar.html", **_ctx_agendar(valores=None))


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
        clientes=listar_clientes(),
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
def barberos_admin():
    barberos = listar_barberos()
    for b in barberos:
        b["horario"] = horario_texto(b)
        b["citas"] = citas_de_barbero(b["nombre"])
    return render_template(
        "barberos.html",
        barberos=barberos,
        dias=list(enumerate(DIAS_ES)),
        seccion="barberos",
    )


def _dias_cerrados_del_form():
    trabaja = set(request.form.getlist("trabaja"))
    return ",".join(str(i) for i in range(7) if str(i) not in trabaja)


@app.route("/barberos/guardar", methods=["POST"])
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
def servicios_admin():
    servicios = listar_servicios()
    for s in servicios:
        s["citas"] = citas_de_servicio(s["id"])
    return render_template("servicios.html", servicios=servicios, seccion="servicios")


@app.route("/servicios/guardar", methods=["POST"])
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
def recordatorios_marcar(cita_id):
    marcar_recordatorio(cita_id, request.form.get("enviado") == "1")
    return redirect(url_for("recordatorios"))


@app.route("/recordatorios/config", methods=["POST"])
def recordatorios_config():
    try:
        dias = max(0, min(30, int(request.form.get("anticipacion", 1))))
    except ValueError:
        dias = 1
    set_config("anticipacion_dias", dias)
    flash(f"Ahora se recordarán las citas con {dias} día(s) de anticipación.", "ok")
    return redirect(url_for("recordatorios"))


@app.route("/reportes")
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

    return render_template(
        "reportes.html",
        resumen=resumen,
        por_barbero=por_barbero,
        por_servicio=por_servicio,
        max_barbero=max_barbero,
        max_servicio=max_servicio,
        total_historico=ingresos_total(),
        nombre_mes=MESES_ES[mes],
        anio=anio,
        mes=mes,
        nav_prev={"anio": anio_prev, "mes": mes_prev},
        nav_sig={"anio": anio_sig, "mes": mes_sig},
        seccion="reportes",
    )


@app.route("/reportes/export.csv")
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


@app.route("/clientes")
def clientes_admin():
    return render_template("clientes.html", clientes=listar_clientes(), seccion="clientes")


@app.route("/clientes/<int:cid>")
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
