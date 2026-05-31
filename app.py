import calendar
import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import quote

from flask import Flask, Response, flash, redirect, render_template, request, url_for

import db

app = Flask(__name__)
app.secret_key = "barberia-la-melena-de-yenry-dev"  # para mensajes flash (uso local)
db.init_db()

MESES_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _a_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def horario_texto(barbero):
    """Texto legible del horario de un barbero, p.ej. 'Lun–Sáb 09:00–19:00'."""
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
    """Devuelve un mensaje de error si la cita cae fuera del horario del barbero, o None."""
    d = date.fromisoformat(fecha_iso)
    if d.weekday() in barbero["dias_cerrados_set"]:
        dia = DIAS_NOMBRE[d.weekday()]
        dia_plural = dia if dia.endswith("s") else dia + "s"  # sábado->sábados, lunes->lunes
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
    """Días de anticipación configurados para recordar citas (default 1)."""
    try:
        return int(db.get_config("anticipacion_dias", 1))
    except (TypeError, ValueError):
        return 1


def contar_por_recordar():
    """Citas próximas, pendientes de recordar y dentro de la ventana de anticipación."""
    hoy = date.today()
    limite = (hoy + timedelta(days=anticipacion_dias())).isoformat()
    return sum(
        1 for c in db.citas_proximas(hoy.isoformat())
        if not c["recordatorio_enviado"] and c["fecha"] <= limite
    )


@app.route("/")
def dashboard():
    hoy = date.today().isoformat()
    citas_hoy = db.citas_por_fecha(hoy)
    ingresos_hoy = sum(db.servicio_por_id(c["servicio_id"])["precio"] for c in citas_hoy)
    return render_template(
        "dashboard.html",
        servicios=db.listar_servicios(),
        citas_hoy=citas_hoy,
        total_citas=len(db.listar_citas()),
        ingresos_hoy=ingresos_hoy,
        por_recordar=contar_por_recordar(),
        servicio_por_id=db.servicio_por_id,
        seccion="inicio",
    )


@app.route("/cortes")
def cortes():
    return render_template("cortes.html", servicios=db.listar_servicios(), seccion="cortes")


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
        servicio = db.servicio_por_id(nueva["servicio_id"])
        barbero = db.barbero_por_nombre(nueva["barbero"])
        if barbero is None:
            error = "Selecciona un barbero válido."
        else:
            error = validar_horario(barbero, nueva["fecha"], nueva["hora"], servicio["duracion"])
        if not error:
            choque = db.buscar_choque(nueva["barbero"], nueva["fecha"], nueva["hora"],
                                      servicio["duracion"], margen=barbero["margen"])
            if choque:
                s_choque = db.servicio_por_id(choque["servicio_id"])
                extra = (f" (se necesitan {barbero['margen']} min libres entre turnos)"
                         if barbero["margen"] else "")
                error = (f"{nueva['barbero']} ya tiene una cita el {nueva['fecha']} a las "
                         f"{choque['hora']} ({s_choque['nombre']}, {s_choque['duracion']} min) "
                         f"con {choque['cliente']}{extra}. Elige otro horario o barbero.")
        if error:
            return render_template(
                "agendar.html",
                **_ctx_agendar(valores=nueva, error=error),
            )
        if nueva["cliente"]:
            nueva["cliente_id"] = db.buscar_o_crear_cliente(nueva["cliente"], nueva["telefono"])
            db.agregar_cita(nueva)
        return redirect(url_for("calendario"))

    return render_template("agendar.html", **_ctx_agendar(valores=None))


def _ctx_agendar(valores, error=None):
    barberos = db.listar_barberos()
    for b in barberos:
        b["horario"] = horario_texto(b)
    aperturas = [_a_min(b["apertura"]) for b in barberos] or [_a_min("09:00")]
    cierres = [_a_min(b["cierre"]) for b in barberos] or [_a_min("19:00")]
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return dict(
        servicios=db.listar_servicios(),
        barberos=barberos,
        clientes=db.listar_clientes(),
        hoy=date.today().isoformat(),
        seccion="agendar",
        valores=valores,
        error=error,
        apertura=fmt(min(aperturas)),
        cierre=fmt(max(cierres)),
    )


@app.route("/eliminar/<int:cita_id>", methods=["POST"])
def eliminar(cita_id):
    db.eliminar_cita(cita_id)
    return redirect(request.referrer or url_for("calendario"))


@app.route("/calendario")
def calendario():
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))

    por_fecha = defaultdict(list)
    for c in db.listar_citas():
        por_fecha[c["fecha"]].append(c)

    cal = calendar.Calendar(firstweekday=0)  # lunes
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
        servicio_por_id=db.servicio_por_id,
        seccion="calendario",
    )


@app.route("/barberos")
def barberos_admin():
    barberos = db.listar_barberos()
    for b in barberos:
        b["horario"] = horario_texto(b)
        b["citas"] = db.citas_de_barbero(b["nombre"])
    return render_template(
        "barberos.html",
        barberos=barberos,
        dias=list(enumerate(DIAS_ES)),
        seccion="barberos",
    )


def _dias_cerrados_del_form():
    """Lee los checkboxes de días que trabaja y devuelve los días cerrados (texto)."""
    trabaja = set(request.form.getlist("trabaja"))  # ['0','1',...]
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
    if db.nombre_barbero_existe(nombre, excluir_id=excluir):
        flash(f"Ya existe un barbero llamado «{nombre}».", "error")
        return redirect(url_for("barberos_admin"))

    if bid:
        db.actualizar_barbero(int(bid), nombre, apertura, cierre, dias_cerrados, margen)
        flash(f"Horario de {nombre} actualizado.", "ok")
    else:
        db.agregar_barbero(nombre, apertura, cierre, dias_cerrados, margen)
        flash(f"Barbero {nombre} agregado.", "ok")
    return redirect(url_for("barberos_admin"))


@app.route("/barberos/eliminar/<int:bid>", methods=["POST"])
def barberos_eliminar(bid):
    barbero = db.barbero_por_id(bid)
    if barbero is None:
        flash("Ese barbero ya no existe.", "error")
    elif db.citas_de_barbero(barbero["nombre"]) > 0:
        flash(f"No puedes eliminar a {barbero['nombre']}: tiene citas registradas. "
              f"Cancélalas primero.", "error")
    else:
        db.eliminar_barbero(bid)
        flash(f"Barbero {barbero['nombre']} eliminado.", "ok")
    return redirect(url_for("barberos_admin"))


@app.route("/servicios")
def servicios_admin():
    servicios = db.listar_servicios()
    for s in servicios:
        s["citas"] = db.citas_de_servicio(s["id"])
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
        db.actualizar_servicio(int(sid), nombre, precio, duracion, desc)
        flash(f"Servicio «{nombre}» actualizado.", "ok")
    else:
        db.agregar_servicio(nombre, precio, duracion, desc)
        flash(f"Servicio «{nombre}» agregado.", "ok")
    return redirect(url_for("servicios_admin"))


@app.route("/servicios/eliminar/<int:sid>", methods=["POST"])
def servicios_eliminar(sid):
    servicio = db.servicio_por_id(sid)
    if servicio is None:
        flash("Ese servicio ya no existe.", "error")
    elif db.citas_de_servicio(sid) > 0:
        flash(f"No puedes eliminar «{servicio['nombre']}»: hay citas que lo usan. "
              f"Cancélalas primero.", "error")
    else:
        db.eliminar_servicio(sid)
        flash(f"Servicio «{servicio['nombre']}» eliminado.", "ok")
    return redirect(url_for("servicios_admin"))


@app.route("/recordatorios")
def recordatorios():
    hoy = date.today()
    hoy_iso = hoy.isoformat()
    manana = (hoy + timedelta(days=1)).isoformat()
    anticipacion = anticipacion_dias()
    limite = (hoy + timedelta(days=anticipacion)).isoformat()

    citas = db.citas_proximas(hoy_iso)
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
        # Auto-detección: pendiente de recordar y dentro de la ventana de anticipación
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
    db.marcar_recordatorio(cita_id, request.form.get("enviado") == "1")
    return redirect(url_for("recordatorios"))


@app.route("/recordatorios/config", methods=["POST"])
def recordatorios_config():
    try:
        dias = max(0, min(30, int(request.form.get("anticipacion", 1))))
    except ValueError:
        dias = 1
    db.set_config("anticipacion_dias", dias)
    flash(f"Ahora se recordarán las citas con {dias} día(s) de anticipación.", "ok")
    return redirect(url_for("recordatorios"))


@app.route("/reportes")
def reportes():
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))

    resumen = db.ingresos_resumen(anio, mes)
    por_barbero = db.ingresos_por_barbero(anio, mes)
    por_servicio = db.ingresos_por_servicio(anio, mes)
    max_barbero = max((b["ingresos"] for b in por_barbero), default=0)
    max_servicio = max((s["ingresos"] for s in por_servicio), default=0)

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
        total_historico=db.ingresos_total(),
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
    filas = db.citas_detalle_mes(anio, mes)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Fecha", "Hora", "Cliente", "Servicio", "Barbero", "Precio"])
    for f in filas:
        w.writerow([f["fecha"], f["hora"], f["cliente"], f["servicio"], f["barbero"], f["precio"]])
    w.writerow([])
    w.writerow(["", "", "", "", "Total", sum(f["precio"] for f in filas)])

    contenido = "﻿" + buf.getvalue()  # BOM para que Excel lea bien los acentos
    nombre = f"ingresos_{anio:04d}-{mes:02d}.csv"
    return Response(
        contenido,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={nombre}"},
    )


@app.route("/clientes")
def clientes_admin():
    return render_template("clientes.html", clientes=db.listar_clientes(), seccion="clientes")


@app.route("/clientes/<int:cid>")
def cliente_detalle(cid):
    cliente = db.cliente_por_id(cid)
    if cliente is None:
        flash("Ese cliente ya no existe.", "error")
        return redirect(url_for("clientes_admin"))
    citas = db.citas_de_cliente(cid)
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
        db.actualizar_cliente(int(cid), nombre, telefono, notas)
        flash(f"Cliente «{nombre}» actualizado.", "ok")
        return redirect(url_for("cliente_detalle", cid=int(cid)))
    else:
        nuevo_id = db.agregar_cliente(nombre, telefono, notas)
        flash(f"Cliente «{nombre}» agregado.", "ok")
        return redirect(url_for("cliente_detalle", cid=nuevo_id))


@app.route("/clientes/eliminar/<int:cid>", methods=["POST"])
def clientes_eliminar(cid):
    cliente = db.cliente_por_id(cid)
    if cliente is None:
        flash("Ese cliente ya no existe.", "error")
    elif db.contar_citas_cliente(cid) > 0:
        flash(f"No puedes eliminar a {cliente['nombre']}: tiene citas registradas. "
              f"Cancélalas primero.", "error")
        return redirect(url_for("cliente_detalle", cid=cid))
    else:
        db.eliminar_cliente(cid)
        flash(f"Cliente {cliente['nombre']} eliminado.", "ok")
    return redirect(url_for("clientes_admin"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
