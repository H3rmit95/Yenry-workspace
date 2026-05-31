"""Acceso a datos con SQLite. La base se guarda en barberia.db (junto a este archivo)."""
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "barberia.db"

SERVICIOS_SEED = [
    ("Corte Clásico", 12, 30, "Corte tradicional a tijera y máquina, lavado incluido."),
    ("Fade / Degradado", 15, 40, "Degradado limpio a tu medida: low, mid o high fade."),
    ("Corte + Barba", 20, 50, "Corte completo más perfilado y arreglo de barba con toalla caliente."),
    ("Arreglo de Barba", 10, 25, "Perfilado, recorte y acabado con aceites y bálsamo."),
    ("Corte Niño", 9, 25, "Corte para los más pequeños, con paciencia y estilo."),
    ("Diseño / Líneas", 5, 15, "Detalles y líneas decorativas para personalizar tu corte."),
]

# (nombre, apertura, cierre, dias_cerrados, margen)  -> dias 0=lunes ... 6=domingo
BARBEROS_SEED = [
    ("Carlos", "09:00", "19:00", "6", 5),    # Lun–Sáb (cerrado domingo)
    ("Miguel", "10:00", "18:00", "0,6", 5),  # Mar–Sáb (cerrado lunes y domingo)
    ("Andrés", "08:00", "16:00", "5,6", 5),  # Lun–Vie (cerrado sábado y domingo)
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
    conn.executescript(
        """
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
        """
    )

    # Migración: agregar columnas de horario a barberos si la BD es de una versión previa
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
            "INSERT INTO barberos (nombre, apertura, cierre, dias_cerrados, margen) "
            "VALUES (?, ?, ?, ?, ?)",
            BARBEROS_SEED,
        )
    # Migración: vincular citas a clientes (BD de versión previa)
    cols_citas = {r[1] for r in conn.execute("PRAGMA table_info(citas)").fetchall()}
    if "cliente_id" not in cols_citas:
        conn.execute("ALTER TABLE citas ADD COLUMN cliente_id INTEGER REFERENCES clientes(id)")
    if "recordatorio_enviado" not in cols_citas:
        conn.execute("ALTER TABLE citas ADD COLUMN recordatorio_enviado INTEGER NOT NULL DEFAULT 0")

    if conn.execute("SELECT COUNT(*) FROM citas").fetchone()[0] == 0:
        hoy = date.today().isoformat()
        conn.executemany(
            "INSERT INTO citas (cliente, telefono, servicio_id, barbero, fecha, hora) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("José Pérez", "809-555-1010", 2, "Carlos", hoy, "10:00"),
                ("Luis Gómez", "809-555-2020", 3, "Miguel", hoy, "11:30"),
            ],
        )

    # Backfill: crear un cliente por cada nombre de cita aún sin vincular y enlazarlo
    faltan = conn.execute("SELECT DISTINCT cliente FROM citas WHERE cliente_id IS NULL").fetchall()
    for (nombre,) in faltan:
        tel_row = conn.execute(
            "SELECT telefono FROM citas WHERE cliente = ? AND telefono IS NOT NULL "
            "AND telefono != '' LIMIT 1",
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
        conn.execute("UPDATE citas SET cliente_id = ? WHERE cliente = ? AND cliente_id IS NULL",
                     (cid, nombre))

    conn.commit()
    conn.close()


# ---------- Consultas ----------

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
        "INSERT INTO barberos (nombre, apertura, cierre, dias_cerrados, margen) "
        "VALUES (?, ?, ?, ?, ?)",
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
    # Si se renombró, propagar el nuevo nombre a las citas existentes
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


def listar_citas():
    conn = get_conn()
    filas = conn.execute("SELECT * FROM citas ORDER BY fecha, hora").fetchall()
    conn.close()
    return [dict(f) for f in filas]


def citas_por_fecha(fecha):
    conn = get_conn()
    filas = conn.execute(
        "SELECT * FROM citas WHERE fecha = ? ORDER BY hora", (fecha,)
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def citas_proximas(desde=None):
    """Citas con fecha >= `desde` (hoy por defecto), con datos del servicio, ordenadas."""
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
        (cita["cliente"], cita["telefono"], cita["servicio_id"],
         cita["barbero"], cita["fecha"], cita["hora"], cita.get("cliente_id")),
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
    """Devuelve la cita en conflicto (mismo barbero, misma fecha, horario solapado o sin
    respetar el margen entre turnos), o None si el turno está libre. `margen` es la pausa
    mínima en minutos que debe quedar libre entre el final de una cita y el inicio de otra."""
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
    filas = conn.execute(
        """
        SELECT cl.*,
               COUNT(c.id)              AS visitas,
               COALESCE(SUM(s.precio), 0) AS total_gastado,
               MAX(c.fecha)             AS ultima_visita
        FROM clientes cl
        LEFT JOIN citas c     ON c.cliente_id = cl.id
        LEFT JOIN servicios s ON s.id = c.servicio_id
        GROUP BY cl.id
        ORDER BY cl.nombre COLLATE NOCASE
        """
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def cliente_por_id(cid):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM clientes WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return _fila(fila)


def citas_de_cliente(cid):
    conn = get_conn()
    filas = conn.execute(
        """
        SELECT c.*, s.nombre AS servicio_nombre, s.precio AS servicio_precio,
               s.duracion AS servicio_duracion
        FROM citas c
        LEFT JOIN servicios s ON s.id = c.servicio_id
        WHERE c.cliente_id = ?
        ORDER BY c.fecha DESC, c.hora DESC
        """,
        (cid,),
    ).fetchall()
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
    # Mantener consistente el nombre denormalizado en las citas
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
    """Citas, ingresos y ticket promedio del mes indicado."""
    conn = get_conn()
    fila = conn.execute(
        "SELECT COUNT(*) AS citas, COALESCE(SUM(s.precio), 0) AS ingresos "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ?",
        (_mes_like(anio, mes),),
    ).fetchone()
    conn.close()
    citas = fila["citas"]
    ingresos = fila["ingresos"]
    ticket = round(ingresos / citas) if citas else 0
    return {"citas": citas, "ingresos": ingresos, "ticket": ticket}


def ingresos_por_barbero(anio, mes):
    conn = get_conn()
    filas = conn.execute(
        "SELECT c.barbero AS barbero, COUNT(*) AS citas, "
        "COALESCE(SUM(s.precio), 0) AS ingresos "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ? "
        "GROUP BY c.barbero ORDER BY ingresos DESC, c.barbero COLLATE NOCASE",
        (_mes_like(anio, mes),),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def ingresos_por_servicio(anio, mes):
    conn = get_conn()
    filas = conn.execute(
        "SELECT s.nombre AS servicio, COUNT(*) AS veces, "
        "COALESCE(SUM(s.precio), 0) AS ingresos "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ? "
        "GROUP BY s.id ORDER BY ingresos DESC, s.nombre COLLATE NOCASE",
        (_mes_like(anio, mes),),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def citas_detalle_mes(anio, mes):
    """Detalle de citas del mes (una fila por cita) para exportar."""
    conn = get_conn()
    filas = conn.execute(
        "SELECT c.fecha, c.hora, c.cliente, c.barbero, s.nombre AS servicio, "
        "s.precio AS precio "
        "FROM citas c JOIN servicios s ON s.id = c.servicio_id "
        "WHERE c.fecha LIKE ? ORDER BY c.fecha, c.hora",
        (_mes_like(anio, mes),),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def ingresos_total():
    """Ingreso histórico acumulado de todas las citas registradas."""
    conn = get_conn()
    total = conn.execute(
        "SELECT COALESCE(SUM(s.precio), 0) FROM citas c "
        "JOIN servicios s ON s.id = c.servicio_id"
    ).fetchone()[0]
    conn.close()
    return total


def buscar_o_crear_cliente(nombre, telefono):
    """Busca un cliente por nombre (sin distinguir mayúsculas). Si existe, actualiza su
    teléfono cuando esté vacío; si no, lo crea. Devuelve el id."""
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
