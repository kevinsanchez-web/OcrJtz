"""Pestaña "Túnel": abre/cierra el túnel temporal de Cloudflare hacia la API, avisa la dirección al Worker de dirección
fija (*.workers.dev) y vigila que toda la cadena responda (Worker -> túnel -> API). Hace lo mismo que
tools/tunel_api.ps1 y Detener_tunel_api.bat."""
import json
import re
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import ttk

from panel_ui.common import AMBER, BG, FG, GREEN, MUTED, NO_WINDOW, PANEL, PORT, RED, ROOT, SLATE, URL, http_ok, load_env

EXE = ROOT / "tools" / "bin" / "cloudflared.exe"
LOG = ROOT / "logs" / "tunnel_api.log"
ENV = ROOT / ".env.local"
LIMITS = {"RATE_LIMIT_PER_MIN": "30", "RATE_LIMIT_READS_PER_MIN": "600"}  # con la API a la vista de internet
UA = {"User-Agent": "panel-api/1.0"}  # Cloudflare bloquea el User-Agent por defecto de Python
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def tunnel_pids() -> list:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    return [int(m.group(1)) for m in re.finditer(r'"cloudflared\.exe","(\d+)"', out)]


def kill_tunnel():
    subprocess.run(["taskkill", "/IM", "cloudflared.exe", "/F"], capture_output=True, creationflags=NO_WINDOW)


def log_tail(n: int = 12) -> list:
    try:
        with open(LOG, "rb") as f:
            f.seek(max(0, LOG.stat().st_size - 12000))
            text = f.read().decode("utf-8", errors="replace")
        return [line for line in text.splitlines() if line.strip()][-n:]
    except OSError:
        return []


def current_url() -> str:
    for line in reversed(log_tail(200)):
        m = URL_RE.search(line)
        if m and "api.trycloudflare" not in line:
            return m.group(0)
    return ""


def set_limits(on: bool) -> bool:
    """Pone (30/600) o quita (0) los límites por minuto en .env.local. Devuelve True si hubo cambios."""
    if not ENV.exists():
        return False
    old = ENV.read_text(encoding="utf-8-sig")
    new = old
    for key, val in LIMITS.items():
        val = val if on else "0"
        if re.search(rf"(?m)^{key}=", new):
            new = re.sub(rf"(?m)^{key}=.*$", f"{key}={val}", new)
        else:
            new = new.rstrip() + f"\n{key}={val}\n"
    if new != old:
        ENV.write_text(new, encoding="utf-8")
        return True
    return False


def fixed_url() -> str:
    return load_env().get("TUNNEL_REGISTER_URL", "").rstrip("/")


def register(action: str, url: str = "") -> tuple:
    """Avisa al Worker (set/clear). Devuelve (ok|None si no hay dirección fija configurada, mensaje)."""
    env = load_env()
    wu, key = env.get("TUNNEL_REGISTER_URL", "").rstrip("/"), env.get("TUNNEL_REGISTER_KEY", "")
    if not wu or not key:
        return None, "Sin dirección fija configurada (TUNNEL_REGISTER_URL / TUNNEL_REGISTER_KEY en .env.local)."
    headers = {**UA, "X-Register-Key": key, "Content-Type": "application/json"}
    data = json.dumps({"url": url}).encode() if action == "set" else None
    req = urllib.request.Request(f"{wu}/__tunnel", data=data, method="POST" if action == "set" else "DELETE", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200, "Dirección fija actualizada." if action == "set" else "Dirección fija desactivada."
    except urllib.error.HTTPError as e:
        return False, f"El Worker respondió {e.code}."
    except Exception as e:
        return False, f"No se pudo avisar al Worker: {e}"


def probe(wu: str) -> int:
    """Código HTTP de la dirección fija /health (0 si no responde): recorre Worker -> túnel -> API."""
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{wu}/health", headers=UA), timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


class TunnelTab(tk.Frame):
    def __init__(self, notebook: ttk.Notebook, app):
        super().__init__(notebook, bg=BG)
        notebook.add(self, text="Túnel")
        self.app = app
        self.working = False  # abrir/cerrar en curso
        self._snap = None
        self._note = None
        self._work = ("Trabajando...", "")
        self._build()
        self.after(500, self._refresh)
        self.after(300, self._paint)

    def _row(self, title: str):
        f = tk.Frame(self, bg=BG)
        f.pack(fill="x", padx=14, pady=2)
        tk.Label(f, text=title, bg=BG, fg=MUTED, width=18, anchor="w", font=("Segoe UI", 9)).pack(side="left")
        v = tk.Label(f, text="–", bg=BG, fg=FG, anchor="w", font=("Consolas", 10))
        v.pack(side="left", fill="x", expand=True)
        return f, v

    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=14, pady=(14, 4))
        self.dot = tk.Canvas(head, width=20, height=20, bg=BG, highlightthickness=0)
        self.dot.pack(side="left")
        self.circle = self.dot.create_oval(3, 3, 17, 17, fill=SLATE, outline="")
        self.title = tk.Label(head, text="Túnel cerrado", bg=BG, fg=FG, font=("Segoe UI", 13, "bold"))
        self.title.pack(side="left", padx=8)
        self.button = tk.Button(self, text="Abrir túnel", font=("Segoe UI", 11, "bold"), fg="white", bd=0, height=2,
                                activeforeground="white", cursor="hand2", command=self.toggle)
        self.button.pack(fill="x", padx=14, pady=(6, 10))

        f, self.v_fixed = self._row("Dirección fija")
        self.copy_btn = tk.Button(f, text="Copiar", bg="#334155", fg="white", bd=0, cursor="hand2", command=self.copy_fixed)
        self.copy_btn.pack(side="right")
        self.reg_btn = tk.Button(f, text="Volver a registrar", bg="#334155", fg="white", bd=0, cursor="hand2",
                                 command=self.reregister)
        self.reg_btn.pack(side="right", padx=6)
        _, self.v_url = self._row("Túnel temporal")
        _, self.v_chain = self._row("Estado de la cadena")
        _, self.v_limits = self._row("Límites por minuto")

        self.msg = tk.Label(self, text="", bg=BG, fg=AMBER, anchor="w", justify="left", wraplength=820, font=("Segoe UI", 9))
        self.msg.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(self, text="Registro del túnel (cloudflared). Cerrar el panel NO cierra el túnel.", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(6, 0))
        self.log = tk.Text(self, bg=PANEL, fg="#94a3b8", font=("Consolas", 8), bd=0, wrap="none", state="disabled", height=10)
        self.log.pack(fill="both", expand=True, padx=12, pady=(2, 12))

    # --- vigilancia -------------------------------------------------------------------------------
    def _refresh(self):
        threading.Thread(target=self._poll, daemon=True).start()
        self.after(4000, self._refresh)

    def _poll(self):
        running = bool(tunnel_pids())
        wu = fixed_url()
        snap = {"running": running, "url": current_url() if running else "", "fixed": wu, "code": None,
                "log": log_tail(10) if running or LOG.exists() else []}
        if running and wu and not self.working:
            snap["code"] = probe(wu)
        self._snap = snap

    def _paint(self):
        s, self._snap = self._snap, None
        if self._note is not None:
            self.msg.config(text=self._note)
            self._note = None
        if s:
            env = load_env()
            lim = f"{env.get('RATE_LIMIT_PER_MIN', '0')} subidas / {env.get('RATE_LIMIT_READS_PER_MIN', '0')} consultas"
            self.v_limits.config(text="sin límite" if lim.startswith("0 sub") and lim.endswith("0 consultas") else lim)
            self.v_fixed.config(text=s["fixed"] or "(no configurada)")
            self.v_url.config(text=s["url"] or ("abriendo..." if s["running"] else "–"))
            if self.working:
                color, (title, chain) = AMBER, self._work
            elif not s["running"]:
                color, title, chain = SLATE, "Túnel cerrado", "–"
            elif not s["url"]:
                color, title, chain = AMBER, "Abriendo túnel...", "esperando una dirección de Cloudflare"
            elif not s["fixed"]:
                color, title, chain = GREEN, "Túnel abierto", "sin dirección fija: la dirección del túnel cambia en cada apertura"
            else:
                code = s["code"]
                if code == 200:
                    color, title, chain = GREEN, "Túnel abierto y en línea", "OK: dirección fija -> túnel -> API"
                elif code == 503:
                    color, title, chain = AMBER, "Túnel abierto, sin enlazar", "la dirección fija no apunta a este túnel (pulsa Volver a registrar)"
                elif code == 502:
                    color, title, chain = RED, "Túnel abierto, API sin respuesta", "el túnel llega pero la API no responde (¿apagada?)"
                else:
                    color, title, chain = RED, "Túnel abierto, sin respuesta", f"la dirección fija no responde ({code or 'sin conexión'})"
            self.dot.itemconfig(self.circle, fill=color)
            self.title.config(text=title)
            self.v_chain.config(text=chain, fg={GREEN: GREEN, RED: RED, AMBER: AMBER}.get(color, FG))
            if not self.working:
                label, bg = ("Cerrar túnel", "#dc2626") if s["running"] else ("Abrir túnel", "#16a34a")
                self.button.config(text=label, bg=bg, activebackground=bg, state="normal")
            text = "\n".join(s["log"])
            if text != getattr(self, "_last", None):
                self._last = text
                self.log.config(state="normal")
                self.log.delete("1.0", "end")
                self.log.insert("end", text)
                self.log.config(state="disabled")
                self.log.see("end")
        self.after(500, self._paint)

    # --- acciones ---------------------------------------------------------------------------------
    def note(self, text: str):
        self._note = text  # se pinta desde el hilo principal (tkinter no es seguro entre hilos)

    def status(self, title: str, chain: str):
        self._work = (title, chain)

    def copy_fixed(self):
        wu = fixed_url()
        if wu:
            self.clipboard_clear()
            self.clipboard_append(f"{wu}/api/v1/analyze-document")
            self.note(f"Copiado: {wu}/api/v1/analyze-document (el valor de JETSON_API_URL).")

    def reregister(self):
        url = current_url() if tunnel_pids() else ""
        if not url:
            self.note("No hay un túnel abierto que registrar.")
            return
        ok, text = register("set", url)
        self.note(text)

    def toggle(self):
        if self.working or self.app.busy:
            self.note("Espera: hay otra acción en curso.")
            return
        self.working = True
        self.button.config(state="disabled", text="Trabajando...", bg=SLATE)
        target = self._close if tunnel_pids() else self._open
        threading.Thread(target=self._guard, args=(target,), daemon=True).start()

    def _guard(self, fn):
        try:
            fn()
        except Exception as e:
            self.note(f"Error: {e}")
        finally:
            self.working = False

    def _restart_api(self):
        """Reinicia la API para que tome los límites nuevos (sin tocar tkinter desde este hilo)."""
        self.app.busy = "stopping"
        self.app.stop(release=False)
        self.app.busy = "starting"
        self.app.start()

    def _open(self):
        if not EXE.exists():
            self.note(f"Falta cloudflared en {EXE}")
            return
        if self.app.state_name != "on":
            self.note("Enciende primero la API (botón de arriba) y vuelve a intentar.")
            return
        self.status("Abriendo túnel...", "activando límites y abriendo el túnel")
        if set_limits(True):
            self.note("Activando límites por minuto y reiniciando la API...")
            self._restart_api()
        if not http_ok(f"{URL}/health"):
            self.note("La API no volvió a responder: no se abrió el túnel.")
            return
        kill_tunnel()
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "wb") as errlog:
            subprocess.Popen([str(EXE), "tunnel", "--url", URL, "--http-host-header", f"127.0.0.1:{PORT}", "--no-autoupdate"],
                             stdout=subprocess.DEVNULL, stderr=errlog, creationflags=NO_WINDOW)
        url = ""
        for _ in range(40):
            time.sleep(2)
            url = current_url()
            if url:
                break
        if not url:
            self.note("El túnel no dio una dirección (revisa la conexión a internet y el registro).")
            return
        ok, text = register("set", url)
        self.note(text + (" Tarda unos 30 s en responder." if ok else ""))

    def _close(self):
        self.status("Cerrando túnel...", "avisando a la dirección fija y cerrando")
        ok, text = register("clear")
        kill_tunnel()
        text2 = ""
        if set_limits(False) and self.app.state_name == "on":
            self._restart_api()
            text2 = " La API se reinició sin límites por minuto."
        self.note("Túnel cerrado: la dirección pública ya no funciona." + text2
                  + ("" if ok in (True, None) else " (" + text + ")"))
