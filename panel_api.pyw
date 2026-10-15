"""Panel para manejar la API de lectura OCR (Windows): encender/apagar, probar documentos, ver estadísticas y claves.

Se abre con doble clic en panel_api.pyw (o con el acceso directo "Panel API OCR"). Cerrar el panel NO apaga la
API: usa el botón. Las pestañas viven en la carpeta panel_ui/.
"""
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))  # para importar panel_ui al abrirlo con doble clic

from panel_ui.common import (BG, BLUE, FG, FONT, MUTED, NO_WINDOW, PANEL, PORT, RED, ROOT, SLATE, URL, Btn,  # noqa: E402
                             apply_theme, http_ok, load_env)
from panel_ui.keys_tab import KeysTab  # noqa: E402
from panel_ui.received_tab import ReceivedTab  # noqa: E402
from panel_ui.stats_tab import StatsTab  # noqa: E402
from panel_ui.tunnel_tab import TunnelTab  # noqa: E402
from panel_ui.upload_tab import UploadTab  # noqa: E402

LOG = ROOT / "logs" / "api.log"
COLORS = {"off": "#64748b", "starting": "#fbbf24", "on": "#4ade80", "stopping": "#fbbf24"}
TEXTS = {
    "off": "API de lectura OCR apagada",
    "starting": "Encendiendo API de lectura OCR...",
    "on": "API de lectura OCR encendida",
    "stopping": "Apagando API de lectura OCR...",
}


def listening_pids() -> list:
    """PIDs que escuchan en el puerto de la API (sea quien sea que la haya encendido)."""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return sorted({int(m.group(1)) for m in re.finditer(rf":{PORT}\s+\S+\s+LISTENING\s+(\d+)", out)})


class Panel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("API de lectura OCR")
        self.configure(bg=BG)
        self.geometry("1000x820")
        self.minsize(760, 600)
        self.state_name = "off"
        self.busy = None  # "starting" / "stopping" mientras dura la acción

        # Cabecera: estado a la izquierda y un solo botón compacto a la derecha (sin la barra gigante de antes)
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=24, pady=(20, 6))
        left = tk.Frame(top, bg=BG)
        left.pack(side="left")
        head = tk.Frame(left, bg=BG)
        head.pack(anchor="w")
        self.dot = tk.Canvas(head, width=14, height=14, bg=BG, highlightthickness=0)
        self.dot.pack(side="left")
        self.circle = self.dot.create_oval(2, 2, 12, 12, fill=COLORS["off"], outline="")
        self.status = tk.Label(head, text=TEXTS["off"], bg=BG, fg=FG, font=(FONT, 14, "bold"))
        self.status.pack(side="left", padx=(8, 0))
        tk.Label(left, text=URL, bg=BG, fg=MUTED, font=("Consolas", 10)).pack(anchor="w", pady=(2, 0))
        self.button = Btn(top, text="Encender", bg=BLUE, fg="white", font=(FONT, 10, "bold"), padx=22, pady=8, command=self.toggle)
        self.button.pack(side="right")

        apply_theme(self)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=24, pady=(8, 20))
        self.upload = UploadTab(tabs, self)
        self.received = ReceivedTab(tabs, self)
        self.stats = StatsTab(tabs, self)
        self.keys = KeysTab(tabs, self)
        self.tunnel = TunnelTab(tabs, self)

        logbox = tk.Frame(tabs, bg=BG)
        tabs.add(logbox, text="Registro")
        self.log = tk.Text(logbox, bg=PANEL, fg="#94a3b8", font=("Consolas", 9), bd=0, wrap="none", state="disabled", height=8)
        sb = ttk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        self.refresh()
        self.apply_latest()
        self.tail()

    # --- estado -----------------------------------------------------------------------------------
    def current(self) -> str:
        if self.busy:
            return self.busy
        return "on" if http_ok(f"{URL}/health") else "off"

    def refresh(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()
        self.after(1500, self.refresh)

    def _refresh_worker(self):
        self._latest = self.current()  # se pinta desde el hilo principal (tkinter no es seguro entre hilos)

    def apply_latest(self):
        st = getattr(self, "_latest", None)
        if st and st != self.state_name or self.busy:
            self.paint(self.busy or st)
        self.after(300, self.apply_latest)

    def paint(self, st: str):
        self.state_name = st
        self.dot.itemconfig(self.circle, fill=COLORS[st])
        self.status.config(text=TEXTS[st])
        label, bg, fg = {"off": ("Encender", BLUE, "white"), "on": ("Apagar", SLATE, RED),
                         "starting": ("Encendiendo...", SLATE, MUTED), "stopping": ("Apagando...", SLATE, MUTED)}[st]
        self.button.restyle(bg=bg, fg=fg, text=label, state="normal" if st in ("on", "off") else "disabled")

    def toggle(self):
        if self.busy:
            return
        action = self.stop if self.state_name == "on" else self.start
        self.busy = "stopping" if self.state_name == "on" else "starting"
        self.paint(self.busy)
        threading.Thread(target=action, daemon=True).start()

    def restart_api(self):
        """Apaga y vuelve a encender la API (para que tome cambios de claves)."""
        if self.busy:
            return
        self.busy = "stopping"
        self.paint(self.busy)

        def work():
            self.stop(release=False)
            self.busy = "starting"
            self.start()
        threading.Thread(target=work, daemon=True).start()

    # --- acciones ---------------------------------------------------------------------------------
    def start(self):
        try:
            env = load_env()
            if not env.get("API_KEYS") and not env.get("API_KEY"):
                self.note("Falta API_KEYS en .env.local: la API no puede arrancar.")
                return
            llm = env.get("LLM_URL", "")
            if llm and not http_ok(f"{llm}/api/tags") and shutil.which("ollama"):
                self.note("Encendiendo Ollama (modelo local)...")
                subprocess.Popen(["ollama", "serve"], creationflags=NO_WINDOW,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(4)
            LOG.parent.mkdir(exist_ok=True)
            out = open(LOG, "ab")
            python = ROOT / "venv" / "Scripts" / "python.exe"
            subprocess.Popen([str(python if python.exists() else sys.executable), "-m", "uvicorn", "main:app",
                              "--host", "127.0.0.1", "--port", str(PORT), "--workers", "1"],
                             cwd=ROOT, env=env, stdout=out, stderr=out, creationflags=NO_WINDOW)
            for _ in range(120):  # hasta ~60 s: cargar los modelos de OCR tarda
                if http_ok(f"{URL}/health"):
                    return
                time.sleep(0.5)
            self.note("La API no respondió a tiempo. Revisa el registro.")
        finally:
            self._latest = None
            self.busy = None

    def stop(self, release: bool = True):
        try:
            for pid in listening_pids():
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=NO_WINDOW)
            for _ in range(20):
                if not http_ok(f"{URL}/health", 0.5):
                    break
                time.sleep(0.5)
        finally:
            self._latest = None
            if release:
                self.busy = None

    # --- registro ---------------------------------------------------------------------------------
    def note(self, text: str):
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[panel] {text}\n")

    def tail(self):
        try:
            if LOG.exists():
                with open(LOG, "rb") as f:
                    f.seek(max(0, LOG.stat().st_size - 8000))
                    text = f.read().decode("utf-8", errors="replace")
                lines = [l for l in text.splitlines() if l.strip()][-60:]
                content = "\n".join(lines)
                if content != getattr(self, "_last", None):
                    self._last = content
                    at_end = self.log.yview()[1] > 0.98
                    self.log.config(state="normal")
                    self.log.delete("1.0", "end")
                    self.log.insert("end", content)
                    self.log.config(state="disabled")
                    if at_end:
                        self.log.see("end")
        except Exception:
            pass
        self.after(1000, self.tail)


if __name__ == "__main__":
    Panel().mainloop()
