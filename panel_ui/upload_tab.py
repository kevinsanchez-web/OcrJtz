"""Pestaña "Probar documentos": sube archivos a la API, muestra la salida, la vista con recuadros y exporta."""
import csv
import io
import json
import queue
import threading
import tkinter as tk
import urllib.parse
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, ImageDraw, ImageTk

from panel_ui.common import (AMBER, BG, BLUE, FG, FILETYPES, GREEN, MUTED, PANEL, PURPLE, RED, SLATE,
                             describe, run_job, selftest_image)

HELP = "Elige un archivo o pulsa Autoprueba para comprobar que la API lee bien."


def conf_color(c: float) -> str:
    return "#22c55e" if c >= 0.9 else "#f59e0b" if c >= 0.7 else "#ef4444"


def page_images(name: str, data: bytes) -> list:
    """Imágenes PIL de cada página (PDF) o la imagen misma, para la vista previa."""
    try:
        if data[:1024].find(b"%PDF-") != -1:
            import pymupdf
            doc = pymupdf.open(stream=data, filetype="pdf")
            return [Image.open(io.BytesIO(doc[i].get_pixmap(dpi=110).tobytes("png"))).convert("RGB")
                    for i in range(min(doc.page_count, 20))]
        from PIL import ImageOps
        return [ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")]
    except Exception:
        return []


def apply_rotations(img: Image.Image, rotations: list) -> Image.Image:
    """Aplica a la página original los mismos giros (antihorarios, ampliando el lienzo) que la API usó para
    enderezarla antes de leer; así los recuadros caen sobre el texto aunque la hoja venga de lado o al revés."""
    for r in rotations:
        a = r % 360
        if abs(a - 90) < 0.3:
            img = img.transpose(Image.ROTATE_90)
        elif abs(a - 180) < 0.3:
            img = img.transpose(Image.ROTATE_180)
        elif abs(a - 270) < 0.3:
            img = img.transpose(Image.ROTATE_270)
        elif a > 0.05 and a < 359.95:
            img = img.rotate(r, expand=True, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    return img


class UploadTab(tk.Frame):
    def __init__(self, notebook: ttk.Notebook, app):
        super().__init__(notebook, bg=BG)
        notebook.add(self, text="Probar documentos")
        self.app = app
        self.events = queue.Queue()
        self.results = []
        self.mode = tk.StringVar(value="transito")
        self._photo = None
        self._page = 0
        self._build()
        self.after(250, self._poll)

    # --- interfaz ---------------------------------------------------------------------------------
    def _build(self):
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=10, pady=(10, 4))
        btn = dict(font=("Segoe UI", 10, "bold"), fg="white", bd=0, cursor="hand2", padx=14, pady=6)
        tk.Button(bar, text="Subir imágenes o documentos…", bg=BLUE, activebackground=BLUE,
                  command=self.pick_files, **btn).pack(side="left")
        tk.Button(bar, text="Autoprueba", bg=PURPLE, activebackground=PURPLE, command=self.selftest, **btn).pack(side="left", padx=8)
        tk.Button(bar, text="Limpiar", bg=SLATE, activebackground=SLATE, command=self.clear, **btn).pack(side="right")

        modes = tk.Frame(self, bg=BG)
        modes.pack(fill="x", padx=10)
        tk.Label(modes, text="Modalidad:", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        for key, label in (("transito", "Documentos de tránsito (campos de la app)"), ("general", "OCR general (cualquier documento)")):
            tk.Radiobutton(modes, text=label, variable=self.mode, value=key, bg=BG, fg=FG, selectcolor="#1e293b",
                           activebackground=BG, activeforeground=FG, font=("Segoe UI", 9)).pack(side="left", padx=8)
        opts = tk.Frame(self, bg=BG)
        opts.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(opts, text="Campos a pedir (solo OCR general):", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.fields = tk.Entry(opts, bg=PANEL, fg=FG, insertbackground=FG, bd=0, font=("Consolas", 9), width=34)
        self.fields.pack(side="left", padx=6, ipady=3)
        self.deep = tk.BooleanVar(value=False)
        tk.Checkbutton(opts, text="buscar con el modelo de visión (lento)", variable=self.deep, bg=BG, fg=MUTED,
                       selectcolor="#1e293b", activebackground=BG, activeforeground=FG, font=("Segoe UI", 8)).pack(side="left")
        tk.Label(opts, text="ej.: total, fecha, correo", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="right")

        self.msg = tk.Label(self, text=HELP, bg=BG, fg=MUTED, anchor="w", justify="left", font=("Segoe UI", 10), wraplength=780)
        self.msg.pack(fill="x", padx=12, pady=6)

        body = tk.PanedWindow(self, orient="horizontal", bg=BG, bd=0, sashwidth=4)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.files = tk.Listbox(body, bg=PANEL, fg=FG, bd=0, highlightthickness=0, activestyle="none",
                                selectbackground="#334155", font=("Segoe UI", 9), width=28, exportselection=False)
        self.files.bind("<<ListboxSelect>>", lambda e: self.show_selected())
        body.add(self.files, minsize=150)
        self.view = ttk.Notebook(body)
        body.add(self.view, minsize=320)

        self.out = {}
        for key, title in (("summary", "Resumen"), ("text", "Texto / campos"), ("json", "JSON")):
            frame = tk.Frame(self.view, bg=BG)
            self.view.add(frame, text=title)
            w = tk.Text(frame, bg=PANEL, fg="#cbd5e1", font=("Consolas", 9), bd=0, wrap="word", state="disabled")
            sb = tk.Scrollbar(frame, command=w.yview)
            w.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            w.pack(side="left", fill="both", expand=True)
            self.out[key] = w

        # Vista previa del documento con los recuadros de cada línea leída
        vframe = tk.Frame(self.view, bg=BG)
        self.view.add(vframe, text="Vista con recuadros")
        nav = tk.Frame(vframe, bg=BG)
        nav.pack(fill="x", pady=4)
        for txt, d in (("◀", -1), ("▶", 1)):
            tk.Button(nav, text=txt, bg=SLATE, fg="white", bd=0, width=3, command=lambda d=d: self.turn(d)).pack(side="left", padx=2)
        self.page_label = tk.Label(nav, text="", bg=BG, fg=FG, font=("Segoe UI", 9))
        self.page_label.pack(side="left", padx=8)
        for color, txt in (("#22c55e", "segura"), ("#f59e0b", "dudosa"), ("#ef4444", "poco fiable")):
            tk.Label(nav, text="■ " + txt, bg=BG, fg=color, font=("Segoe UI", 8)).pack(side="right", padx=4)
        self.canvas = tk.Canvas(vframe, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.after(120, self.draw_page))
        self.canvas.bind("<Button-1>", self.click_canvas)
        self.hint = tk.Label(vframe, text="", bg=BG, fg=FG, anchor="w", justify="left", font=("Consolas", 9), wraplength=600)
        self.hint.pack(fill="x", pady=(2, 0))

        exp = tk.Frame(self, bg=BG)
        exp.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(exp, text="Guardar resultado:", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        for txt, fn in (("JSON", self.export_json), ("Texto", self.export_text), ("CSV (Excel)", self.export_csv)):
            tk.Button(exp, text=txt, bg=SLATE, fg="white", bd=0, padx=10, pady=3, command=fn).pack(side="left", padx=4)

    # --- subir ------------------------------------------------------------------------------------
    def pick_files(self):
        for p in filedialog.askopenfilenames(title="Elige imágenes o documentos", filetypes=FILETYPES):
            try:
                self.submit(Path(p).name, Path(p).read_bytes(), selftest=False)
            except OSError as e:
                self.msg.config(text=f"No se pudo leer {p}: {e}", fg=RED)

    def selftest(self):
        self.mode.set("general")
        self.submit("autoprueba.png", selftest_image(), selftest=True)

    def submit(self, name: str, data: bytes, selftest: bool):
        if self.app.state_name != "on":
            self.msg.config(text="Primero enciende la API (botón verde de arriba).", fg=AMBER)
            return
        general = self.mode.get() == "general"
        params = "&lines=true"  # en ambas modalidades: la vista con recuadros necesita la posición de cada línea
        fields = ",".join(f.strip() for f in self.fields.get().split(",") if f.strip()) if general and not selftest else ""
        if fields:
            params += "&fields=" + urllib.parse.quote(fields) + ("&deep=true" if self.deep.get() else "")
        item = {"name": name, "mode": "ocr" if general else "transit", "params": params, "selftest": selftest,
                "state": "pending", "data": data}
        self.results.append(item)
        self.files.insert("end", f"⏳ {name}")
        self.files.selection_clear(0, "end")
        self.files.selection_set("end")
        self.msg.config(text=f"Procesando {name}... (los PDF con varias páginas pueden tardar minutos)", fg=AMBER)
        threading.Thread(target=self._send, args=(item,), daemon=True).start()

    def _send(self, item: dict):
        def progress(status, prog):
            self.events.put((item, {"progress": (status, prog)}))
        res = run_job(item["mode"], item["name"], item["data"], item["params"], progress)
        if item["selftest"] and res["ok"]:
            text = (res["json"] or {}).get("text", "")
            res["selftest_ok"] = "PRUEBA" in text.upper() and "12345" in text and "98765" in text
            res["selftest_text"] = text
        self.events.put((item, res))

    def _poll(self):
        try:
            while True:
                item, res = self.events.get_nowait()
                if "progress" in res:
                    status, prog = res["progress"]
                    where = f"página {prog["page"] + 1} de {prog["total"]}" if prog else "en cola" if status == "queued" else "leyendo"
                    if item["state"] != "done":
                        self.msg.config(text=f"Procesando {item["name"]}: {where}...", fg=AMBER)
                    continue
                item["res"], item["state"] = res, "done"
                self._finished(item)
        except queue.Empty:
            pass
        self.after(250, self._poll)

    def _finished(self, item: dict):
        i = self.results.index(item)
        res = item["res"]
        ok = res["ok"] and res.get("selftest_ok", True)
        self.files.delete(i)
        self.files.insert(i, f"{'✔' if ok else '✘'} {item['name']}  ({res['secs']:.0f} s)")
        self.files.itemconfig(i, fg="#4ade80" if ok else RED)
        if item["selftest"]:
            if res["ok"] and res.get("selftest_ok"):
                self.msg.config(text=f"✔ La API funciona: leyó la imagen de prueba en {res['secs']:.1f} s.", fg="#4ade80")
            elif res["ok"]:
                self.msg.config(text=f"✘ La API respondió pero NO leyó bien el texto de prueba: {res['selftest_text']!r}", fg=RED)
            else:
                self.msg.config(text=f"✘ No funciona: {res['error']}", fg=RED)
        else:
            self.msg.config(text=describe(res)[0].splitlines()[0], fg="#4ade80" if res["ok"] else RED)
        self.files.selection_clear(0, "end")
        self.files.selection_set(i)
        self.show_selected()

    # --- mostrar ----------------------------------------------------------------------------------
    def current(self):
        sel = self.files.curselection()
        return self.results[sel[0]] if sel else None

    def show_selected(self):
        item = self.current()
        if not item or not item.get("res"):
            return
        res = item["res"]
        summary, readable = describe(res)
        if item["selftest"] and res["ok"]:
            summary += f"\n\nTexto leído: {res.get('selftest_text', '')!r}\nVerificación: " + \
                       ("CORRECTA" if res.get("selftest_ok") else "FALLÓ")
        raw = json.dumps(res.get("json"), ensure_ascii=False, indent=2) if res.get("json") is not None else res.get("error", "")
        for key, content in (("summary", summary), ("text", readable or "(sin texto)"), ("json", raw)):
            w = self.out[key]
            w.config(state="normal")
            w.delete("1.0", "end")
            w.insert("end", content)
            w.config(state="disabled")
        if "images" not in item:
            item["images"] = page_images(item["name"], item["data"])
        self._page = 0
        self.draw_page()

    def turn(self, d: int):
        item = self.current()
        if item and item.get("images"):
            self._page = (self._page + d) % len(item["images"])
            self.draw_page()

    def page_rotations(self, item, n: int) -> list:
        """Giros que la API aplicó a la página antes de leerla: los recuadros están en la página ya enderezada."""
        j = (item.get("res") or {}).get("json") or {}
        for p in j.get("pages", []):
            if p["page"] == n + 1:
                return p.get("rotations") or []
        return []

    def page_lines(self, item, n: int):
        j = (item.get("res") or {}).get("json") or {}
        for p in j.get("pages", []):
            if p["page"] == n + 1 and p.get("lines"):
                return p["lines"]
        return None

    def draw_page(self):
        item = self.current()
        self.canvas.delete("all")
        self._boxes = []
        if not item or not item.get("images"):
            self.page_label.config(text="")
            self.hint.config(text="Sube un documento para ver aquí qué leyó la API.")
            return
        img = apply_rotations(item["images"][self._page], self.page_rotations(item, self._page))
        cw = max(self.canvas.winfo_width(), 200)
        ch = max(self.canvas.winfo_height(), 200)
        scale = min(cw / img.width, ch / img.height)
        w, h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        shown = img.resize((w, h), Image.LANCZOS)
        lines = self.page_lines(item, self._page)
        if lines:
            ov = ImageDraw.Draw(shown, "RGBA")
            for ln in lines:
                x0, y0, x1, y1 = (ln["box"][0] * w, ln["box"][1] * h, ln["box"][2] * w, ln["box"][3] * h)
                col = conf_color(ln["confidence"])
                ov.rectangle([x0, y0, x1, y1], outline=col, width=2, fill=col + "30")
                self._boxes.append((x0, y0, x1, y1, ln))
        self._photo = ImageTk.PhotoImage(shown)
        self._off = ((cw - w) // 2, 0)
        self.canvas.create_image(self._off[0], self._off[1], image=self._photo, anchor="nw")
        self.page_label.config(text=f"Página {self._page + 1} de {len(item['images'])}")
        self.hint.config(text="Toca un recuadro para ver su texto." if lines else
                         "Esta modalidad no devuelve posiciones: usa «OCR general» para ver los recuadros.")

    def click_canvas(self, e):
        x, y = e.x - self._off[0], e.y - self._off[1]
        for x0, y0, x1, y1, ln in self._boxes:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.hint.config(text=f"{ln['text']}   (confianza {ln['confidence']:.2f})")
                return

    # --- exportar ---------------------------------------------------------------------------------
    def _need_result(self):
        item = self.current()
        if not item or not item.get("res") or not item["res"].get("ok"):
            self.msg.config(text="Primero elige un resultado correcto de la lista.", fg=AMBER)
            return None
        return item

    def _save(self, item, ext: str, types, content: str, encoding="utf-8"):
        path = filedialog.asksaveasfilename(defaultextension=ext, filetypes=types,
                                            initialfile=Path(item["name"]).stem + "_resultado" + ext)
        if path:
            with open(path, "w", encoding=encoding, newline="") as f:
                f.write(content)
            self.msg.config(text=f"Guardado: {path}", fg="#4ade80")

    def export_json(self):
        item = self._need_result()
        if item:
            self._save(item, ".json", [("JSON", "*.json")], json.dumps(item["res"]["json"], ensure_ascii=False, indent=2))

    def export_text(self):
        item = self._need_result()
        if item:
            self._save(item, ".txt", [("Texto", "*.txt")], describe(item["res"])[1] or "")

    def export_csv(self):
        item = self._need_result()
        if not item:
            return
        j, buf = item["res"]["json"], io.StringIO()
        w = csv.writer(buf, delimiter=";")
        if j.get("pages"):
            wrote = False
            for p in j["pages"]:
                for n, t in enumerate(p.get("tables") or [], 1):  # tablas detectadas: una fila por fila de la tabla
                    w.writerow([f"Tabla {n} (página {p['page']})"])
                    w.writerows(t["rows"])
                    w.writerow([])
                    wrote = True
            if not wrote:
                w.writerow(["página", "texto", "confianza"])
                for p in j["pages"]:
                    for ln in p.get("lines") or [{"text": t, "confidence": p["confidence"]} for t in p["text"].split("\n")]:
                        w.writerow([p["page"], ln["text"], ln["confidence"]])
        else:
            w.writerow(["página", "documento", "campo", "valor"])
            for d in j.get("documents", []):
                for k, v in d["extractedData"].items():
                    if isinstance(v, dict):
                        v = ", ".join(f"{a}: {b}" for a, b in v.items())
                    w.writerow([d.get("page"), d["documentType"], k, v if not isinstance(v, list) else " | ".join(map(str, v))])
        self._save(item, ".csv", [("CSV (Excel)", "*.csv")], buf.getvalue(), encoding="utf-8-sig")

    def clear(self):
        self.results.clear()
        self.files.delete(0, "end")
        for w in self.out.values():
            w.config(state="normal")
            w.delete("1.0", "end")
            w.config(state="disabled")
        self.canvas.delete("all")
        self.msg.config(text=HELP, fg=MUTED)
