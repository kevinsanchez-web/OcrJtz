"""Pestaña "Estadísticas": cuántas peticiones llegan, de qué app, cuánto tardan y cuántas fallan."""
import threading
import time
import tkinter as tk
from tkinter import ttk

from panel_ui.common import AMBER, BG, FG, GREEN, MUTED, PANEL, RED, ago, api_get
from panel_ui.common import Btn, DANGER, SURFACE  # noqa: E402,F401


def fmt_duration(s: int) -> str:
    return f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min {s % 60} s"


class StatsTab(tk.Frame):
    def __init__(self, notebook: ttk.Notebook, app):
        super().__init__(notebook, bg=BG)
        notebook.add(self, text="Estadísticas")
        self.app = app
        self.notebook = notebook
        self._data = None
        self._build()
        self.after(1000, self._refresh)
        self.after(500, self._paint)

    def _card(self, parent, title: str):
        f = tk.Frame(parent, bg=SURFACE)
        f.pack(side="left", expand=True, fill="x", padx=4)
        tk.Label(f, text=title, bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(6, 0))
        v = tk.Label(f, text="–", bg=SURFACE, fg=FG, font=("Segoe UI", 16, "bold"))
        v.pack(anchor="w", padx=10, pady=(0, 6))
        return v

    def _tree(self, parent, cols, widths, height):
        style = ttk.Style(self)
        style.configure("Dark.Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, borderwidth=0, rowheight=22)
        style.configure("Dark.Treeview.Heading", background=SURFACE, foreground=FG, relief="flat")
        t = ttk.Treeview(parent, columns=cols, show="headings", height=height, style="Dark.Treeview")
        for c, w in zip(cols, widths):
            t.heading(c, text=c, anchor="w")
            t.column(c, width=w, anchor="w")
        t.tag_configure("bad", foreground=RED)
        t.tag_configure("ok", foreground=FG)
        return t

    def _build(self):
        cards = tk.Frame(self, bg=BG)
        cards.pack(fill="x", padx=8, pady=(10, 4))
        self.c_total = self._card(cards, "PETICIONES")
        self.c_day = self._card(cards, "ÚLTIMAS 24 H")
        self.c_err = self._card(cards, "ERRORES")
        self.c_avg = self._card(cards, "TIEMPO MEDIO")
        self.c_up = self._card(cards, "API ENCENDIDA")

        self.vlm = tk.Label(self, text="", bg=BG, fg=MUTED, anchor="w", font=("Segoe UI", 10))
        self.vlm.pack(fill="x", padx=12, pady=(6, 2))

        tk.Label(self, text="Por app", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=12)
        self.t_clients = self._tree(self, ("App", "Peticiones", "Errores", "Tiempo medio", "Última"), (160, 90, 80, 110, 120), 4)
        self.t_clients.pack(fill="x", padx=10, pady=(0, 8))

        tk.Label(self, text="Últimas peticiones (solo datos de uso, nunca el contenido de los archivos)",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=12)
        self.t_recent = self._tree(self, ("Cuándo", "App", "Servicio", "Tamaño", "Tiempo", "Estado", "Docs", "Detalle"),
                                   (80, 110, 120, 70, 70, 60, 50, 220), 10)
        self.t_recent.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.note = tk.Label(self, text="", bg=BG, fg=AMBER, anchor="w", font=("Segoe UI", 10))
        self.note.pack(fill="x", padx=12, pady=(0, 6))

    def _refresh(self):
        # Solo consulta si la API está encendida y esta pestaña es la visible (no gasta recursos de más)
        if self.app.state_name == "on" and self.notebook.select() == str(self):
            threading.Thread(target=self._fetch, daemon=True).start()
        self.after(3000, self._refresh)

    def _fetch(self):
        self._data = api_get("/api/v1/stats?recent=25")

    def _paint(self):
        res, self._data = self._data, None
        if res:
            if not res["ok"]:
                self.note.config(text=f"No se pudieron leer las estadísticas: {res['error']}")
            else:
                self.note.config(text="")
                s = res["json"]
                self.c_total.config(text=str(s["total"]))
                self.c_day.config(text=str(s["last24h"]))
                self.c_err.config(text=str(s["errors"]), fg=RED if s["errors"] else GREEN)
                self.c_avg.config(text=f"{s['avgSeconds']:.1f} s")
                self.c_up.config(text=fmt_duration(s["uptimeSeconds"]))
                v = s.get("vlm", {})
                self.vlm.config(text=(f"Modelo de visión (VLM): encendido · {v.get('model')}" if v.get("enabled") else
                                      "Modelo de visión (VLM): apagado"), fg=GREEN if v.get("enabled") else MUTED)
                for t in (self.t_clients, self.t_recent):
                    t.delete(*t.get_children())
                for c in s["clients"]:
                    self.t_clients.insert("", "end", values=(c["client"], c["requests"], c["errors"],
                                                             f"{c['avgSeconds']:.1f} s", ago(c["lastTs"])),
                                          tags=("bad" if c["errors"] else "ok",))
                for r in s["recent"]:
                    kb = r["bytes"] / 1024
                    self.t_recent.insert("", "end", tags=("bad" if r["status"] >= 400 else "ok",), values=(
                        ago(r["ts"]), r["client"], r["endpoint"], f"{kb / 1024:.1f} MB" if kb > 1024 else f"{kb:.0f} KB",
                        f"{r['seconds']:.1f} s", r["status"], r["docs"] or "", r["error"]))
        self.after(500, self._paint)
