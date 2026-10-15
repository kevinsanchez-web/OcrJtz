"""Pestaña "Recibidos": los documentos que llegan a la API desde las apps, con su semáforo (verde, amarillo, rojo), la
misma vista con recuadros de "Probar documentos" y, mientras se procesan, una vista EN VIVO (etapas, OCR y el texto que
va escribiendo el VLM). Necesita KEEP_RECEIVED=1 en .env.local (todo vive solo en la memoria de la API)."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from panel_ui.common import AMBER, BG, FG, MUTED, PANEL, RED, SLATE, api_delete, api_get, api_get_bytes
from panel_ui.common import Btn, DANGER, SURFACE  # noqa: E402,F401
from panel_ui.upload_tab import UploadTab

GRADE = {"green": ("#4ade80", "lectura segura"), "amber": (AMBER, "conviene revisar"), "red": (RED, "no se leyó / falló")}
LIVE_COLOR = {"queued": "#94a3b8", "running": "#60a5fa", "done": "#60a5fa", "error": RED}
HELP = ""
LIVE_TAB = 4  # posición de "En vivo" entre las pestañas del visor (después de Resumen, Texto, JSON y Vista con recuadros)
BOXES_TAB = 3


def hhmmss(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


class ReceivedTab(UploadTab):
    EMPTY_LIST = "Aún no ha llegado\nningún documento"
    EMPTY_VIEW = "Aquí verás en vivo lo que las apps\nmandan a la API y qué lee de cada una"

    def __init__(self, notebook: ttk.Notebook, app):
        self._seen = set()          # ids ya mostrados como resultado final
        self._fetching = set()
        self._by_id = {}            # id -> item final
        self.live_items = {}        # id -> item "en vivo" (aún sin resultado final)
        self._latest = None
        self._live_latest = None
        self._incoming = queue.Queue()
        super().__init__(notebook, app, title="Recibidos")
        self.after(1500, self._refresh)
        self.after(1000, self._tick_live)
        self.after(400, self._apply)

    # --- interfaz: barra propia + el visor de UploadTab + la vista en vivo --------------------------
    def _build(self):
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=10, pady=(10, 2))
        btn = dict(font=("Segoe UI", 10, "bold"), fg="white", bd=0, cursor="hand2", padx=12, pady=5, bg=SLATE, activebackground=SLATE)
        Btn(bar, text="Actualizar ahora", command=self.refresh_now, **btn).pack(side="left")
        Btn(bar, text="Quitar de la vista", command=self.clear, **btn).pack(side="left", padx=6)
        Btn(bar, text="Borrar de la API", command=self.wipe, **btn).pack(side="left")
        for color, txt in (("#ef4444", "no se leyó / falló"), ("#f59e0b", "conviene revisar"), ("#22c55e", "lectura segura"),
                           ("#60a5fa", "procesando")):
            tk.Label(bar, text="■ " + txt, bg=BG, fg=color, font=("Segoe UI", 10)).pack(side="right", padx=6)
        self._build_viewer()
        live = tk.Frame(self.view, bg=BG)
        self.view.add(live, text="En vivo")
        self.live_text = tk.Text(live, bg=PANEL, fg="#cbd5e1", font=("Consolas", 10), bd=0, wrap="word", state="disabled")
        sb = ttk.Scrollbar(live, command=self.live_text.yview)
        self.live_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.live_text.pack(side="left", fill="both", expand=True)
        self.live_text.tag_configure("head", foreground="#e2e8f0", font=("Consolas", 10, "bold"))
        self.live_text.tag_configure("stage", foreground="#60a5fa", font=("Consolas", 10, "bold"))
        self.live_text.tag_configure("time", foreground="#64748b")
        self.live_text.tag_configure("vlm", foreground="#a5b4fc")
        self.live_text.tag_configure("section", foreground="#94a3b8", font=("Consolas", 10, "bold"))
        self.msg.config(text=HELP)

    # --- consulta periódica -------------------------------------------------------------------------
    def _visible(self) -> bool:
        return self.master.select() == str(self)

    def refresh_now(self):
        self._refresh(once=True)

    def _refresh(self, once: bool = False):
        # Solo consulta con la API encendida y esta pestaña visible (no gasta recursos de más)
        if self.app.state_name == "on" and (once or self._visible()):
            threading.Thread(target=self._fetch_list, daemon=True).start()
        if not once:
            self.after(3000, self._refresh)

    def _tick_live(self):
        if self.app.state_name == "on" and (self._visible() or self.live_items):
            threading.Thread(target=self._fetch_live, daemon=True).start()
        self.after(1000, self._tick_live)

    def _fetch_list(self):
        self._latest = api_get("/api/v1/received", timeout=5)

    def _fetch_live(self):
        self._live_latest = api_get("/api/v1/live", timeout=5)

    def _fetch_item(self, meta: dict):
        try:
            detail = api_get(f"/api/v1/received/{meta['id']}", timeout=15)
            data = api_get_bytes(f"/api/v1/received/{meta['id']}/file", timeout=30)
            if detail["ok"] and data is not None:
                self._incoming.put((meta, detail["json"], data))
        finally:
            self._fetching.discard(meta["id"])

    def _apply(self):
        res, self._latest = self._latest, None
        if res is not None:
            if not res["ok"]:
                hint = {404: "La API no guarda los recibidos: pon KEEP_RECEIVED=1 en .env.local y reiníciala.",
                        403: "La API rechazó la consulta (solo responde al panel local)."}.get(res["status"], res["error"])
                self.msg.config(text=hint, fg=AMBER)
            else:
                for meta in res["json"]["items"]:
                    if meta["id"] not in self._seen and meta["id"] not in self._fetching:
                        self._fetching.add(meta["id"])
                        threading.Thread(target=self._fetch_item, args=(meta,), daemon=True).start()
        try:
            while True:
                meta, detail, data = self._incoming.get_nowait()
                if meta["id"] not in self._seen:
                    self._seen.add(meta["id"])
                    try:
                        self._add(meta, detail, data)
                    except Exception as e:  # un documento raro no debe detener la pestaña
                        self.msg.config(text=f"No se pudo mostrar {meta['name'] or meta['id']}: {type(e).__name__}: {e}", fg=RED)
        except queue.Empty:
            pass
        lv, self._live_latest = self._live_latest, None
        if lv is not None and lv["ok"]:
            try:
                self._apply_live(lv["json"]["items"])
            except Exception as e:
                self.msg.config(text=f"No se pudo actualizar la vista en vivo: {type(e).__name__}: {e}", fg=RED)
        self.after(500, self._apply)

    # --- filas -------------------------------------------------------------------------------------
    def _row_text(self, item: dict) -> str:
        e = item["live"]
        name = e["name"] or e["endpoint"]
        return f"⏳ {hhmmss(item['ts'])} · {e['client']} · {name} — {e['stage']} ({e['elapsed']:.0f} s)"

    def _set_row(self, item: dict):
        """Repinta la fila de `item` conservando la selección."""
        i = self.results.index(item)
        selected = i in self.files.curselection()
        self.files.delete(i)
        self.files.insert(i, self._row_text(item))
        self.files.itemconfig(i, fg=LIVE_COLOR.get(item["live"]["state"], "#60a5fa"))
        if selected:
            self.files.selection_set(i)

    def _apply_live(self, entries: list):
        present = set()
        for e in entries:
            present.add(e["id"])
            final = self._by_id.get(e["id"])
            if final is not None:  # ya tiene resultado: se guarda su línea de tiempo y se refresca si se está mirando
                final["live"] = e
                if self.current() is final and self.view.index(self.view.select()) == LIVE_TAB:
                    self.render_live(final)
                continue
            item = self.live_items.get(e["id"])
            if item is None:
                item = {"name": e["name"] or e["endpoint"], "mode": "transit", "selftest": False, "state": "live",
                        "data": None, "res": None, "id": e["id"], "live": e, "ts": time.time()}
                self.live_items[e["id"]] = item
                previous = self.current()
                self.results.insert(0, item)
                self.files.insert(0, self._row_text(item))
                self.files.itemconfig(0, fg=LIVE_COLOR.get(e["state"], "#60a5fa"))
                if previous is not None:  # no te quita el documento que estás mirando
                    self.files.selection_clear(0, "end")
                    self.files.selection_set(self.results.index(previous))
            else:
                item["live"] = e
                self._set_row(item)
            if self.current() is item:
                self.render_live(item)
        for key in [k for k in self.live_items if k not in present]:  # ya no está en la API (caducó o se canceló)
            self._drop_live(key)

    def _drop_live(self, key: str):
        item = self.live_items.pop(key, None)
        if item is not None and item in self.results:
            i = self.results.index(item)
            self.results.pop(i)
            self.files.delete(i)

    def _add(self, meta: dict, detail: dict, data: bytes):
        err = detail.get("error")
        res = ({"ok": False, "status": err["status"], "secs": meta["seconds"], "error": err["detail"]} if err else
               {"ok": True, "status": 200, "secs": meta["seconds"], "json": detail["result"]})
        item = {"name": meta["name"] or meta["endpoint"], "mode": "ocr" if meta["endpoint"].endswith("ocr") else "transit",
                "selftest": False, "state": "done", "data": data, "res": res, "meta": meta}
        watching = False
        pending = self.live_items.get(meta["id"])
        if pending is not None:  # el documento que se veía en vivo ya terminó: su fila se cambia por el resultado
            item["live"] = pending["live"]
            watching = self.current() is pending
            self._drop_live(meta["id"])
        self._by_id[meta["id"]] = item
        previous = self.current()
        self.results.insert(0, item)
        color, _ = GRADE[meta["grade"]]
        kinds = ", ".join(d["type"] for d in meta["docs"]) or ("error" if err else "sin documento reconocido")
        self.files.insert(0, f"■ {hhmmss(meta['ts'])} · {meta['client']} · {item['name']} ({kinds})")
        self.files.itemconfig(0, fg=color)
        self.files.selection_clear(0, "end")
        index = 0 if (watching or previous is None) else self.results.index(previous)  # no te quita lo que estás mirando
        self.files.selection_set(index)
        self.show_selected()
        if watching:
            self.view.select(BOXES_TAB)  # lo que se veía en vivo pasa a mostrarse con sus recuadros
        self.msg.config(text=f"Llegó {item['name']} de «{meta['client']}»: {GRADE[meta['grade']][1]}.", fg=color)

    # --- visor -------------------------------------------------------------------------------------
    def show_selected(self):
        item = self.current()
        if item is not None and item.get("state") == "live":
            self.render_live(item)
            self.view.select(LIVE_TAB)
            return
        super().show_selected()
        if item is not None:
            self.render_live(item)

    def render_live(self, item: dict):
        """Línea de tiempo y texto del VLM del documento (en curso o ya terminado)."""
        e = item.get("live")
        w = self.live_text
        at_end = w.yview()[1] > 0.97
        w.config(state="normal")
        w.delete("1.0", "end")
        if not e:
            w.insert("end", "(Sin línea de tiempo: el documento llegó antes de abrir el panel o ya caducó en la API.)", "time")
        else:
            state = {"queued": "en cola", "running": "procesando", "done": "terminado", "error": "con error"}.get(e["state"], e["state"])
            w.insert("end", f"{e['name'] or e['endpoint']} · {e['client']} · {state} · {e['elapsed']:.1f} s\n", "head")
            w.insert("end", f"Ahora: {e['stage']}\n", "stage")
            if e.get("total"):
                w.insert("end", f"Página {e['page']} de {e['total']}\n", "time")
            w.insert("end", "\n── Línea de tiempo ──\n", "section")
            for ev in e["events"]:
                w.insert("end", f"+{ev['t']:>6.1f} s  ", "time")
                w.insert("end", ev["text"] + "\n")
            if e["vlm"]:
                w.insert("end", "\n── Modelo de visión (VLM) ──\n", "section")
                for call in e["vlm"]:
                    status = f"listo en {call['seconds']} s" if call["done"] else "escribiendo…"
                    w.insert("end", f"[{call['label']}] {status}\n", "head")
                    w.insert("end", (call["text"] or "(aún sin texto)") + "\n\n", "vlm")
        w.config(state="disabled")
        if at_end:
            w.see("end")

    # --- limpiar ------------------------------------------------------------------------------------
    def clear(self):
        """Quita de la vista (los que sigan en la API no vuelven a aparecer)."""
        super().clear()
        self.live_items.clear()
        self.live_text.config(state="normal")
        self.live_text.delete("1.0", "end")
        self.live_text.config(state="disabled")
        self.msg.config(text=HELP, fg=MUTED)

    def wipe(self):
        """Borra también los guardados en la API."""
        res = api_delete("/api/v1/received")
        self.clear()
        self._seen.clear()
        self._by_id.clear()
        self.msg.config(text="Borrados de la API." if res["ok"] else f"No se pudo borrar: {res['error']}", fg=FG)
