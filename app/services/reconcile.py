"""Reconciliación entre los documentos de un mismo PDF.

Un PDF de trámite trae varios documentos del mismo vehículo y persona (cédula, factura, RUNT, gases...).
Si el OCR lee un dato con un error en uno de ellos (motor `...44S1039S` en vez de `...445103Q5`), los demás
documentos lo desmienten. Solo se corrigen valores MUY parecidos entre sí y solo cuando la mayoría coincide;
nunca se rellena un campo vacío con datos de otro documento.
"""
import logging
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from app.schemas import DocumentItem

logger = logging.getLogger("document-api")

# Campos que deben coincidir entre documentos
GROUPS: Dict[str, List[str]] = {
    "persona": ["numeroDocumento", "compradorIdentificacion", "propietarioIdentificacion", "otorganteIdentificacion"],
    "motor": ["vehiculoMotor"],
    "chasis": ["vehiculoChasis"],
    "linea": ["vehiculoLinea"],
}
NAME_FIELDS = ["nombreCompleto", "compradorNombre", "propietarioNombre", "otorganteNombre", "apoderadoNombre"]
SIMILARITY = 0.78


def _key(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", v.upper())


def _fuzzy(k: str) -> str:
    """Para agrupar: el OCR confunde O/0 e I/1."""
    return k.replace("O", "0").replace("I", "1")


def _complete_truncated_names(items: List[DocumentItem]) -> None:
    """Un nombre cortado en un documento (JIME) se completa con el mismo nombre entero de otro (JIMENEZ)."""
    names = [(i, f, str(i.extractedData[f])) for i in items for f in NAME_FIELDS if i.extractedData.get(f)]
    for item, field, value in names:
        k = _key(value)
        longer = [v for _, _, v in names if len(_key(v)) > len(k) and _key(v).startswith(k)]
        if longer and len(k) >= 8:
            item.extractedData[field] = max(longer, key=len)


def reconcile(items: List[DocumentItem]) -> None:
    """Corrige en el sitio los valores que difieren por errores de OCR respecto a la mayoría."""
    _complete_truncated_names(items)
    for group, fields in GROUPS.items():
        refs = [(item, f) for item in items for f in fields if item.extractedData.get(f)]
        clusters: List[List] = []
        for ref in refs:
            k = _key(str(ref[0].extractedData[ref[1]]))
            for cluster in clusters:
                if SequenceMatcher(None, _fuzzy(k), _fuzzy(cluster[0][2])).ratio() >= SIMILARITY:
                    cluster.append((*ref, k))
                    break
            else:
                clusters.append([(*ref, k)])
        for cluster in clusters:
            counts = Counter(k for _, _, k in cluster)
            if len(counts) < 2:
                continue
            (winner, n1), (_, n2) = counts.most_common(2)
            if n1 < 2 or n1 == n2:  # sin mayoría clara no se toca nada
                continue
            for item, field, k in cluster:
                if k != winner:
                    good = next(i.extractedData[f] for i, f, kk in cluster if kk == winner)
                    logger.info("Reconciliado %s entre documentos (%s -> %s)", field, "…", "valor mayoritario")
                    item.extractedData[field] = good
    _names_from_identity(items)
    _inherit_missing_names(items)


# campos de nombre que pueden heredarse (el apoderado es otra persona: no se hereda)
INHERITABLE_NAMES = {"nombreCompleto": "numeroDocumento", "compradorNombre": "compradorIdentificacion",
                     "propietarioNombre": "propietarioIdentificacion", "otorganteNombre": "otorganteIdentificacion"}


def _inherit_missing_names(items: List[DocumentItem]) -> None:
    """Si el nombre de la persona quedó vacío en una página pero ya se identificó en otra del mismo archivo,
    se reutiliza. Solo si es seguro: la página no tiene una identificación distinta y el archivo trata de
    UNA sola persona (con varias personas distintas no se sabría a cuál corresponde)."""
    ids = {_key(str(i.extractedData[f])) for i in items for f in INHERITABLE_NAMES.values() if i.extractedData.get(f)}
    if len(ids) <= 1:  # una sola persona en el archivo
        names = Counter()
        for item in items:  # la cédula pesa más: es la fuente más fiable del nombre
            for name_f in INHERITABLE_NAMES:
                if item.extractedData.get(name_f):
                    names[str(item.extractedData[name_f])] += 3 if item.documentType == "cedula" else 1
        _fill(items, list(INHERITABLE_NAMES), names.most_common(1)[0][0] if names else None)
        # su identificación, si falta en alguna página
        _fill(items, list(INHERITABLE_NAMES.values()), _single_value(items, list(INHERITABLE_NAMES.values())))
        for f in ("celular", "correo", "direccion"):  # contacto de esa persona
            _fill(items, [f], _single_value(items, [f]))

    # Un solo vehículo en el archivo (un único chasis): sus datos se comparten entre documentos
    chassis = {_key(str(i.extractedData["vehiculoChasis"])) for i in items if i.extractedData.get("vehiculoChasis")}
    if len(chassis) <= 1:
        for f in VEHICLE_FIELDS:
            _fill(items, [f], _single_value(items, [f]))  # solo si todos los documentos coinciden en el valor


VEHICLE_FIELDS = ["vehiculoMarca", "vehiculoLinea", "vehiculoModelo", "vehiculoMotor", "vehiculoChasis",
                  "vehiculoCilindrada", "vehiculoColor"]


def _single_value(items: List[DocumentItem], fields: List[str]) -> Optional[str]:
    """El valor de esos campos si todos los documentos que lo traen coinciden (con tolerancia a O/0 e I/1)."""
    found = {_fuzzy(_key(str(i.extractedData[f]))): str(i.extractedData[f])
             for i in items for f in fields if i.extractedData.get(f)}
    return next(iter(found.values())) if len(found) == 1 else None


def _fill(items: List[DocumentItem], fields: List[str], value: Optional[str]) -> None:
    """Rellena los campos vacíos (que el documento sí tiene) con `value` y lo anota en `inheritedFields`."""
    if not value:
        return
    for item in items:
        for f in fields:
            if f in item.extractedData and not item.extractedData.get(f):
                logger.info("Campo %s heredado de otra página del mismo archivo", f)
                item.extractedData[f] = value
                item.inheritedFields.append(f)


# campo de identificación -> campo de nombre de la misma persona
ID_TO_NAME = {"numeroDocumento": "nombreCompleto", "compradorIdentificacion": "compradorNombre",
              "propietarioIdentificacion": "propietarioNombre", "otorganteIdentificacion": "otorganteNombre"}


def _names_from_identity(items: List[DocumentItem]) -> None:
    """Misma identificación = misma persona: su nombre impreso en la cédula sustituye al ilegible (manuscrito,
    cortado) de otro documento, o lo completa si está vacío. Solo se usa la cédula, que es la fuente más fiable."""
    official = {_key(str(i.extractedData["numeroDocumento"])): str(i.extractedData["nombreCompleto"])
                for i in items if i.documentType == "cedula"
                and i.extractedData.get("numeroDocumento") and i.extractedData.get("nombreCompleto")}
    for item in items:
        for id_field, name_field in ID_TO_NAME.items():
            ident, name = item.extractedData.get(id_field), item.extractedData.get(name_field)
            if not ident or item.documentType == "cedula" or _key(str(ident)) not in official:
                continue
            real = official[_key(str(ident))]
            if not name or SequenceMatcher(None, _key(str(name)), _key(real)).ratio() >= 0.6:
                if name != real:
                    logger.info("Nombre de %s completado a partir de la cédula (misma identificación)", name_field)
                    item.extractedData[name_field] = real
