"""Corrección de direcciones escritas a mano con vocabulario de Colombia (tipos de vía, departamentos y municipios).

El OCR y el VLM leen la letra a mano con errores ('Cundinomrcq', 'madnd', 'C/24E'). Aquí solo se corrige cuando la
lectura es MUY parecida a una palabra conocida, o es el comienzo único de un departamento al final de la dirección;
lo que no se parece a nada queda tal cual (un barrio o un municipio que no está en la lista no debe «corregirse»).
La lista de municipios NO es exhaustiva: son los de Cundinamarca más cercanos a Bogotá y las ciudades principales.
"""
import re
from typing import Dict, List, Optional

from app.extraction.lexicon import canonical
from app.text_utils import norm

DEPARTAMENTOS = [
    "AMAZONAS", "ANTIOQUIA", "ARAUCA", "ATLANTICO", "BOLIVAR", "BOYACA", "CALDAS", "CAQUETA", "CASANARE",
    "CAUCA", "CESAR", "CHOCO", "CORDOBA", "CUNDINAMARCA", "GUAINIA", "GUAVIARE", "HUILA", "LA GUAJIRA", "MAGDALENA",
    "META", "NARINO", "NORTE DE SANTANDER", "PUTUMAYO", "QUINDIO", "RISARALDA", "SAN ANDRES", "SANTANDER", "SUCRE",
    "TOLIMA", "VALLE DEL CAUCA", "VAUPES", "VICHADA",
]
MUNICIPIOS = [
    # Cundinamarca (cercanos a Bogotá)
    "SOACHA", "MADRID", "MOSQUERA", "FUNZA", "FACATATIVA", "CHIA", "CAJICA", "ZIPAQUIRA", "COTA", "TENJO", "TABIO", "SOPO",
    "TOCANCIPA", "GACHANCIPA", "LA CALERA", "FUSAGASUGA", "GIRARDOT", "UBATE", "SIBATE", "BOJACA", "SUBACHOQUE",
    "EL ROSAL", "COGUA", "NEMOCON", "VILLETA", "GUADUAS", "SILVANIA", "ARBELAEZ", "ANAPOIMA", "APULO", "TOCAIMA",
    "RICAURTE", "AGUA DE DIOS", "CHOCONTA", "SUESCA", "SESQUILE", "GUASCA", "GUATAVITA", "CAQUEZA", "CHOACHI",
    "UBAQUE", "PACHO", "LA VEGA", "SASAIMA", "NILO", "VIOTA", "SAN BERNARDO", "CARMEN DE CARUPA",
    # Capitales y ciudades principales
    "BOGOTA", "MEDELLIN", "CALI", "BARRANQUILLA", "CARTAGENA", "CUCUTA", "BUCARAMANGA", "PEREIRA", "SANTA MARTA",
    "IBAGUE", "MANIZALES", "PASTO", "NEIVA", "VILLAVICENCIO", "ARMENIA", "MONTERIA", "VALLEDUPAR", "POPAYAN",
    "SINCELEJO", "TUNJA", "FLORENCIA", "RIOHACHA", "YOPAL", "QUIBDO", "MOCOA", "LETICIA", "ENVIGADO", "ITAGUI", "BELLO",
    "SABANETA", "SOLEDAD", "PALMIRA", "BUENAVENTURA", "TULUA", "DOSQUEBRADAS", "FLORIDABLANCA", "GIRON", "PIEDECUESTA",
    "RIONEGRO", "APARTADO", "SOGAMOSO", "DUITAMA", "MAICAO", "IPIALES", "TUMACO", "BARRANCABERMEJA",
]
_PLACES = sorted(set(DEPARTAMENTOS + MUNICIPIOS))

# Tipo de vía: lo que se escribe a mano -> forma habitual (Colombia)
_VIAS: Dict[str, str] = {
    "calle": "Cl", "cll": "Cl", "cl": "Cl", "c": "Cl", "carrera": "Cra", "cra": "Cra", "cr": "Cra", "kr": "Cra",
    "kra": "Cra", "k": "Cra", "diagonal": "Dg", "dg": "Dg", "diag": "Dg", "transversal": "Tv", "tv": "Tv",
    "trans": "Tv", "avenida": "Av", "av": "Av", "avda": "Av",
}
# Palabras que anuncian un barrio u otra referencia que no es un municipio: lo que sigue no se corrige
_NOT_PLACE = {"barrio", "br", "urb", "urbanizacion", "conjunto", "cj", "torre", "edificio", "ed", "vereda", "sector", "mz",
              "manzana", "casa", "apto", "apartamento", "int", "interior", "bloque", "etapa", "lote"}
_LEAD = re.compile(r"^\s*([A-Za-z1|/\\.]{1,10})\s*(?=\d)")


def _via(token: str) -> Optional[str]:
    letters = re.sub(r"[^A-Za-z1|]", "", token).lower()
    if len(letters) <= 2 and letters.startswith("c"):
        return "Cl"  # 'C/', 'C1', 'CI', 'Cl': la letra a mano 'l' se lee como 1, I o |
    return _VIAS.get(letters)


def _place(word: str, at_end: bool) -> Optional[str]:
    key = norm(word).strip().upper()
    if len(key) < 3:
        return None
    if key in _PLACES:
        return key  # ya está bien escrito: solo se normaliza la mayúscula
    if len(key) >= 4:
        hit = canonical(key, _PLACES, 0.72)
        if hit:
            return hit
    if at_end:  # el final de la línea cortado: 'CUN' -> el único departamento que empieza así
        starts = [d for d in DEPARTAMENTOS if d.startswith(key)]
        if len(starts) == 1:
            return starts[0]
    return None


def _title(s: str) -> str:
    return " ".join(w.capitalize() for w in s.lower().split())


def fix_address(address: str) -> str:
    """Dirección con el tipo de vía normalizado ('C/24E' -> 'Cl 24E') y el municipio o departamento corregido."""
    s = address.strip()
    if not s:
        return s
    m = _LEAD.match(s)
    if m and _via(m.group(1)):
        s = f"{_via(m.group(1))} {s[m.end():]}"
    s = re.sub(r"\s*#\s*", " # ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # La parte con letras después de la última cifra es el municipio/departamento
    last_digit = max((m.end() for m in re.finditer(r"\d", s)), default=0)
    words: List[str] = re.findall(r"[^\W\d_]+|[\W\d_]+", s[last_digit:])  # lo que sigue a la última cifra
    letters = [i for i, w in enumerate(words) if re.match(r"[^\W\d_]", w)]
    if letters:
        # Solo se corrige el final de la dirección: la última palabra y, si esa es un departamento, el municipio que la
        # precede. Un barrio («Chico») jamás se cambia por un departamento parecido («Chocó»).
        last = letters[-1]
        after_barrio = len(letters) >= 2 and norm(words[letters[-2]]).strip().lower() in _NOT_PLACE
        fixed = None if after_barrio else _place(words[last], at_end=True)
        if fixed:
            words[last] = _title(fixed)
        if len(letters) >= 2 and norm(words[last]).strip().upper() in DEPARTAMENTOS:
            before = norm(words[letters[-3]]).strip().lower() if len(letters) >= 3 else ""
            if before not in _NOT_PLACE:
                prev = _place(words[letters[-2]], at_end=False)
                if prev:
                    words[letters[-2]] = _title(prev)
    return (s[:last_digit] + "".join(words)).strip()
