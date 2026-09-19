"""
Versión para GitHub Actions: revisa la taquilla del Racing UNA vez por ejecución.

- El token y el chat ID se leen de los "Secrets" de GitHub (variables de entorno).
  NO escribas el token en este archivo: el repositorio es público.
- El estado (avisos ya enviados, etc.) se guarda en estado.json, que el
  workflow conserva entre ejecuciones.

Uso:
    python monitor_github.py            -> revisión normal
    python monitor_github.py --prueba   -> lee la web una vez y te manda el resultado
"""
import hashlib
import json
import os
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ------------------------- CONFIGURACIÓN -------------------------
URL_ENTRADAS = "https://taquilla.realracingclub.es/"
TOKEN_TELEGRAM = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID_TELEGRAM = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

FICHERO_ESTADO = "estado.json"
RIVAL = "madrid"
TEXTO_SIN_PARTIDOS = "no hay partidos disponibles"
AVISAR_OTROS_EVENTOS = True
MAX_AVISOS_MADRID = 3
MINUTOS_ENTRE_AVISOS = 10
LATIDO_HORAS = 12                # mensaje "sigo vivo" cada X horas (0 = desactivado)
PROBLEMAS_PARA_AVISAR = 12       # ~1 hora seguida sin poder leer la web

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

DESCRIPCION_ESTADO = {
    "SIN_PARTIDOS": "la taquilla sigue diciendo que no hay partidos",
    "MADRID": "hay un evento del Real Madrid",
    "OTROS_EVENTOS": "hay eventos, pero ninguno del Madrid",
    "ANOMALA": "la página no parece la de la taquilla (¿bloqueo o error?)",
    "SIN_CONEXION": "no he podido abrir la web",
}


# ------------------------- TELEGRAM -------------------------
def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TOKEN_TELEGRAM}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID_TELEGRAM, "text": mensaje}, timeout=10)
        if r.status_code == 200:
            print("✉️ Mensaje enviado a Telegram.")
            return True
        print(f"❌ Telegram devolvió {r.status_code}: {r.text}")
    except requests.RequestException as e:
        print(f"❌ Error de red con Telegram: {e}")
    return False


def verificar_secretos():
    """Comprueba que los Secrets existen y que Telegram acepta el token."""
    if not TOKEN_TELEGRAM or not CHAT_ID_TELEGRAM:
        print("❌ Faltan los Secrets TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID en GitHub.")
        return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN_TELEGRAM}/getMe", timeout=10)
    except requests.RequestException as e:
        print(f"❌ No pude conectar con Telegram: {e}")
        return False
    if r.status_code == 200:
        print("✅ Token correcto.")
        return True
    print(f"❌ Telegram no acepta el token ({r.status_code}). Revisa el Secret TELEGRAM_TOKEN.")
    return False


# ------------------------- TAQUILLA -------------------------
def descargar_pagina():
    for intento in (1, 2):
        try:
            r = requests.get(URL_ENTRADAS, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.text
            print(f"La web respondió {r.status_code} (intento {intento})")
        except requests.RequestException as e:
            print(f"Error de conexión (intento {intento}): {e}")
        if intento == 1:
            time.sleep(5)
    return None


def analizar_pagina(html):
    """Devuelve (situación, texto, enlace). Ver DESCRIPCION_ESTADO."""
    soup = BeautifulSoup(html, "html.parser")
    for etiqueta in soup(["script", "style", "noscript"]):
        etiqueta.decompose()

    if "real racing club" not in soup.get_text(" ").lower():
        return "ANOMALA", "", None

    contenido = soup.find(id="content") or soup.body or soup
    texto = " ".join(contenido.get_text(" ").split())
    minusculas = texto.lower()

    if TEXTO_SIN_PARTIDOS in minusculas:
        return "SIN_PARTIDOS", texto, None

    if RIVAL in minusculas:
        enlace = URL_ENTRADAS
        for a in contenido.find_all("a", href=True):
            if RIVAL in a.get_text(" ").lower() or RIVAL in a["href"].lower():
                enlace = urljoin(URL_ENTRADAS, a["href"])
                break
        return "MADRID", texto, enlace

    return "OTROS_EVENTOS", texto, None


# ------------------------- ESTADO ENTRE EJECUCIONES -------------------------
def cargar_estado():
    try:
        with open(FICHERO_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def guardar_estado(estado):
    with open(FICHERO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f)


def huella(texto):
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


# ------------------------- EJECUCIONES -------------------------
def revision_normal():
    estado = cargar_estado()
    ahora = time.time()

    if not estado:
        enviar_telegram("✅ Monitor en la nube activo. Vigilo taquilla.realracingclub.es y te aviso si sale el Racing - Real Madrid.")
        estado["ultimo_latido"] = ahora

    html = descargar_pagina()
    situacion, texto, enlace = ("SIN_CONEXION", "", None) if html is None else analizar_pagina(html)
    print(f"Situación: {situacion} → {DESCRIPCION_ESTADO[situacion]}")
    estado["ultima_lectura"] = DESCRIPCION_ESTADO[situacion]

    if situacion in ("SIN_CONEXION", "ANOMALA"):
        estado["problemas"] = estado.get("problemas", 0) + 1
        if estado["problemas"] == PROBLEMAS_PARA_AVISAR:
            enviar_telegram(
                "⚠️ Llevo ~1 hora sin poder leer bien la taquilla desde la nube "
                f"({DESCRIPCION_ESTADO[situacion]}). Puede que la web bloquee a GitHub; "
                "usa también la laptop."
            )
    else:
        estado["problemas"] = 0

        if situacion == "MADRID":
            avisos = estado.get("avisos_madrid", 0)
            hace_min = (ahora - estado.get("ultimo_aviso_madrid", 0)) / 60
            if avisos < MAX_AVISOS_MADRID and hace_min >= MINUTOS_ENTRE_AVISOS:
                enviar_telegram(
                    "🚨 ¡RACING - REAL MADRID EN LA TAQUILLA! 🚨\n"
                    f"Entra ya: {enlace}\n\n"
                    f"Texto detectado: {texto[:300]}"
                )
                estado["avisos_madrid"] = avisos + 1
                estado["ultimo_aviso_madrid"] = ahora

        elif situacion == "OTROS_EVENTOS":
            vistos = estado.get("otros", [])
            h = huella(texto)
            if AVISAR_OTROS_EVENTOS and h not in vistos:
                enviar_telegram(
                    "ℹ️ Ha aparecido algo nuevo en la taquilla del Racing (aún no es el Madrid):\n"
                    f"{URL_ENTRADAS}\n\n{texto[:300]}"
                )
                estado["otros"] = (vistos + [h])[-20:]

        else:  # SIN_PARTIDOS
            estado["avisos_madrid"] = 0  # si el Madrid aparece más tarde, avisará de nuevo

    if LATIDO_HORAS and ahora - estado.get("ultimo_latido", ahora) >= LATIDO_HORAS * 3600:
        enviar_telegram(f"💓 Sigo vigilando la taquilla desde la nube. Última lectura: {estado['ultima_lectura']}.")
        estado["ultimo_latido"] = ahora

    guardar_estado(estado)


def modo_prueba():
    print("🔎 MODO PRUEBA: leo la taquilla una vez y te mando el resultado.")
    html = descargar_pagina()
    if html is None:
        enviar_telegram("❌ Prueba: GitHub no ha podido abrir la taquilla del Racing.")
        return
    situacion, texto, _ = analizar_pagina(html)
    print(f"Situación: {situacion}\nTexto que veo: {texto[:200]}")
    enviar_telegram(
        "🔎 Prueba de lectura desde GitHub\n"
        f"Estado: {situacion} → {DESCRIPCION_ESTADO[situacion]}\n\n"
        f"Texto que veo: {texto[:200]}"
    )


if __name__ == "__main__":
    if not verificar_secretos():
        sys.exit(1)  # el workflow saldrá en rojo y verás el motivo en el registro
    if "--prueba" in sys.argv:
        modo_prueba()
    else:
        revision_normal()
