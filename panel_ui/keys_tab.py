"""Pestaña "Apps y claves": da a cada app su propia clave de acceso y permite revocarla, sin editar archivos."""
import hashlib
import os
import re
import secrets
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from panel_ui.common import AMBER, BG, BLUE, FG, GREEN, MUTED, PANEL, RED, ROOT, SLATE, key_entries

ENV_FILE = ROOT / ".env.local"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,29}$")


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def write_keys(entries: list) -> None:
    """Reescribe API_KEYS en .env.local (UTF-8 sin BOM) conservando el resto de variables."""
    value = ",".join(key if name == "transito" else f"{name}:{key}" for name, key in entries)
    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines() if ENV_FILE.exists() else []
    done = False
    for i, line in enumerate(lines):
        if re.match(r"\s*API_KEYS?\s*=", line):
            lines[i], done = f"API_KEYS={value}", True
    if not done:
        lines.insert(0, f"API_KEYS={value}")
    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")  # sin BOM: con BOM la API no arranca
    os.replace(tmp, ENV_FILE)


class KeysTab(tk.Frame):
    def __init__(self, notebook: ttk.Notebook, app):
        super().__init__(notebook, bg=BG)
        notebook.add(self, text="Apps y claves")
        self.app = app
        self._build()
        self.reload()

    def _build(self):
        tk.Label(self, text="Cada app que use la API tiene su propia clave. Así sabes quién llama, y puedes quitar el acceso "
                            "a una sin afectar a las demás.", bg=BG, fg=MUTED, anchor="w", justify="left",
                 font=("Segoe UI", 9), wraplength=780).pack(fill="x", padx=12, pady=(12, 6))
        style = ttk.Style(self)
        style.configure("Keys.Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=26, borderwidth=0)
        style.configure("Keys.Treeview.Heading", background="#1e293b", foreground=FG, relief="flat")
        self.tree = ttk.Treeview(self, columns=("App", "Tipo", "Huella"), show="headings", height=8, style="Keys.Treeview")
        for c, w in (("App", 200), ("Tipo", 330), ("Huella", 120)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", padx=10)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=10, pady=10)
        btn = dict(font=("Segoe UI", 10, "bold"), fg="white", bd=0, cursor="hand2", padx=14, pady=6)
        tk.Button(bar, text="Nueva app…", bg=BLUE, activebackground=BLUE, command=self.create, **btn).pack(side="left")
        tk.Button(bar, text="Copiar clave", bg=SLATE, activebackground=SLATE, command=self.copy, **btn).pack(side="left", padx=8)
        tk.Button(bar, text="Revocar", bg="#dc2626", activebackground="#dc2626", command=self.revoke, **btn).pack(side="left")
        self.msg = tk.Label(self, text="", bg=BG, fg=MUTED, anchor="w", justify="left", font=("Segoe UI", 10), wraplength=780)
        self.msg.pack(fill="x", padx=12)
        self.restart_btn = tk.Button(self, text="Reiniciar la API para aplicar los cambios", bg=PANEL, fg=AMBER, bd=0,
                                     cursor="hand2", font=("Segoe UI", 10, "bold"), command=self.restart)
        tk.Label(self, text="\nCómo usar una clave en otra app: enviarla en el encabezado  X-API-Key  de cada petición\n"
                            "(por ejemplo, POST http://127.0.0.1:8000/api/v1/ocr con el archivo en el campo «file»).",
                 bg=BG, fg=MUTED, anchor="w", justify="left", font=("Segoe UI", 9)).pack(fill="x", padx=12)

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        for name, key in key_entries():
            kind = "App de extracción de documentos de tránsito (clave principal)" if name == "transito" else "Otra app"
            self.tree.insert("", "end", iid=name, values=(name, kind, fingerprint(key)))

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def create(self):
        name = simpledialog.askstring("Nueva app", "Nombre de la app (letras, números, - o _):", parent=self)
        if not name:
            return
        name = name.strip().lower()
        if not NAME_RE.match(name):
            self.msg.config(text="Nombre no válido: usa 2 a 30 letras, números, - o _, empezando por una letra.", fg=RED)
            return
        entries = key_entries()
        if any(n == name for n, _ in entries):
            self.msg.config(text=f"Ya existe una app llamada «{name}».", fg=RED)
            return
        key = secrets.token_urlsafe(32)
        write_keys(entries + [(name, key)])
        self.reload()
        self.clipboard_clear()
        self.clipboard_append(key)
        self.msg.config(text=f"✔ Clave creada para «{name}» y copiada al portapapeles. Guárdala en esa app: "
                             f"{key}", fg=GREEN)
        self.restart_btn.pack(anchor="w", padx=12, pady=6)

    def copy(self):
        name = self.selected()
        if not name:
            self.msg.config(text="Elige una app de la lista.", fg=AMBER)
            return
        key = dict(key_entries()).get(name, "")
        self.clipboard_clear()
        self.clipboard_append(key)
        self.msg.config(text=f"Clave de «{name}» copiada al portapapeles.", fg=GREEN)

    def revoke(self):
        name = self.selected()
        if not name:
            self.msg.config(text="Elige una app de la lista.", fg=AMBER)
            return
        if name == "transito":
            self.msg.config(text="La clave principal es la de la app de extracción de documentos de tránsito; "
                                 "si la quitas esa app dejará de funcionar. Cámbiala a mano si de verdad quieres rotarla.",
                            fg=AMBER)
            return
        if not messagebox.askyesno("Revocar acceso", f"¿Quitar el acceso a «{name}»? Esa app dejará de poder usar la API.", parent=self):
            return
        write_keys([(n, k) for n, k in key_entries() if n != name])
        self.reload()
        self.msg.config(text=f"Acceso de «{name}» revocado.", fg=GREEN)
        self.restart_btn.pack(anchor="w", padx=12, pady=6)

    def restart(self):
        self.restart_btn.pack_forget()
        self.msg.config(text="Reiniciando la API...", fg=AMBER)
        self.app.restart_api()
