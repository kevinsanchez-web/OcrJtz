"""OCR general: lee CUALQUIER documento (PDF o imagen) y devuelve su texto con la posición de cada línea.

A diferencia de `analyzer.py` (que busca los campos de unos tipos concretos), aquí no se asume nada del
contenido. Si además el documento es de un tipo conocido (cédula, factura, RUNT, gases, poder), se añaden sus
campos estructurados. Usa la misma lectura robusta: orientación, inclinación, borrosidad y límites de tiempo.
"""
import io
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pymupdf
from PIL import Image

from app import progress
from app.config import MAX_PDF_PAGES, MAX_SECONDS_PER_DOCUMENT, MAX_SECONDS_PER_IMAGE
from app.extraction.pipeline import parse_page_text
from app.ocr.boxes import OcrBox, boxes_to_text, to_boxes
from app.ocr.preprocess import load_image
from app.ocr.quality import is_good, quality, stats
from app.ocr.reader import candidates
from app.schemas import DocumentItem, KeyValue, OcrLine, OcrPage, OcrResponse, Table
from app.llm import client
from app.ocr.engine import use_engine
from app.services.fields import find_fields
from app.services.tables import tables_from_boxes, tables_from_pdf_page
from app.services.office import read_office
from app.services.analyzer import LEGACY_MESSAGE, AnalysisError, _is_empty, _render_dpi, detect_kind
from app.services.output import finalize
from app.services.reconcile import reconcile

logger = logging.getLogger("document-api")

_PAIR = re.compile(r"^\s*([^\W\d_][^:\n]{1,39}?)\s*[:=]\s*(\S.{0,200})$", re.UNICODE)


def _key_values(text: str) -> List[KeyValue]:
    """Pares 'Etiqueta: valor' que aparecen en el texto (una línea cada uno)."""
    out = []
    for line in text.split("\n"):
        m = _PAIR.match(line)
        if m and len(re.findall(r"[^\W\d_]", m.group(1))) >= 2:
            out.append(KeyValue(key=m.group(1).strip(), value=m.group(2).strip()))
    return out


def _split(boxes: List[OcrBox], lo, hi, gap: float) -> List[List[OcrBox]]:
    """Parte las cajas en grupos separados por un hueco vacío de al menos `gap` en un eje (lo/hi = inicio y fin)."""
    groups: List[List[OcrBox]] = []
    end = None
    for b in sorted(boxes, key=lo):
        if end is None or lo(b) >= end + gap:
            groups.append([b])
            end = hi(b)
        else:
            groups[-1].append(b)
            end = max(end, hi(b))
    return groups


def _blocks(boxes: List[OcrBox], med_h: float, depth: int = 0) -> List[List[OcrBox]]:
    """Orden de lectura por bloques (corte XY): columnas de izquierda a derecha y bloques de arriba abajo.
    Así un texto en dos columnas, o una infografía con tarjetas, no se lee mezclando renglones de columnas distintas."""
    if len(boxes) <= 1 or depth > 8:
        return [boxes]
    for lo, hi, gap in ((lambda b: b.x0, lambda b: b.x1, 0.9 * med_h),   # primero columnas
                        (lambda b: b.y0, lambda b: b.y1, 1.3 * med_h)):  # luego bloques de arriba abajo
        parts = _split(boxes, lo, hi, gap)
        if len(parts) > 1:
            return [leaf for p in parts for leaf in _blocks(p, med_h, depth + 1)]
    return [boxes]


def reading_order(boxes: List[OcrBox]) -> List[List[OcrBox]]:
    if not boxes:
        return []
    med_h = float(np.median([b.h for b in boxes])) or 1.0
    return _blocks(boxes, med_h)


def _ordered_text(boxes: List[OcrBox]) -> str:
    return "\n".join(boxes_to_text(leaf) for leaf in reading_order(boxes))


def _lines(boxes: List[OcrBox], width: float, height: float, with_lines: bool) -> Optional[List[OcrLine]]:
    if not with_lines:
        return None
    clamp = lambda v: round(min(max(v, 0.0), 1.0), 4)  # noqa: E731
    ordered = [b for leaf in reading_order(boxes) for b in sorted(leaf, key=lambda b: (round(b.cy / max(b.h, 1.0)), b.x0))]
    return [OcrLine(text=b.text, confidence=round(min(max(b.score, 0.0), 1.0), 3),
                    box=[clamp(b.x0 / width), clamp(b.y0 / height), clamp(b.x1 / width), clamp(b.y1 / height)])
            for b in ordered]


def pages_from_reads(reads: dict, items: List[DocumentItem]) -> List[OcrPage]:
    """Páginas con la posición de cada línea leída (reconocida como documento de tránsito o no), para la vista previa."""
    types = {it.page: it.documentType for it in items if it.page}
    out: List[OcrPage] = []
    for n in sorted(reads):
        r = reads[n]
        out.append(OcrPage(page=n, source=r.get("source", "ocr"), text=_ordered_text(r["boxes"]),
                           confidence=round(min(max(r["score"], 0.0), 1.0), 3),
                           lines=_lines(r["boxes"], r["w"], r["h"], True), rotations=[round(x, 2) for x in r["rotations"]],
                           documentType=types.get(n)))
    return out


def _safe_tables(boxes: List[OcrBox], w: float, h: float) -> List[Table]:
    try:
        return tables_from_boxes(boxes, w, h)
    except Exception:
        logger.exception("Falló la detección de tablas")
        return []


def _read_image(img_bytes: bytes, deadline: float):
    """(cajas, score medio, ancho, alto, imagen leída, giros aplicados) de la mejor lectura, o None si no hay nada que leer."""
    base = load_image(img_bytes)
    if base is None or _is_empty(base):
        return None
    best, best_q = None, -1.0
    t0 = time.monotonic()
    empty = 0
    try:
        for res, _, read, rots in candidates(base):
            boxes = to_boxes(res)
            if not boxes:
                empty += 1
                if empty >= 2:
                    break
                continue
            q = quality(res)
            if q > best_q:
                best, best_q = (boxes, stats(res)[1], float(read.shape[1]), float(read.shape[0]), read, list(rots)), q
            chars, mean = stats(res)
            confident = chars >= 10 and mean >= 0.93  # poco texto pero leído con seguridad: no hay nada que mejorar
            if is_good(res) or confident or time.monotonic() - t0 > MAX_SECONDS_PER_IMAGE or time.monotonic() > deadline:
                break
    except Exception:
        logger.exception("Falló la lectura general de la imagen")
    return best


def _native_page(page: "pymupdf.Page", with_lines: bool):
    """Texto digital de la página con la posición de cada línea, o None si no tiene texto útil."""
    raw = page.get_text()
    if not raw or len(raw.strip()) <= 30:
        return None
    w, h = page.rect.width or 1.0, page.rect.height or 1.0
    boxes = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                boxes.append(OcrBox(text, 1.0, x0, y0, x1, y1))
    return raw, boxes, w, h


def ocr_file(contents: bytes, rid: str = "-", structured: bool = True, with_lines: bool = True,
             transit_shape: bool = True, fields: Optional[List[str]] = None, deep: bool = False) -> OcrResponse:
    """OCR general. Usa el modelo de lectura latino (acentos, eñes, espacios) si está instalado."""
    with use_engine("latin"):
        return _ocr_file(contents, rid, structured, with_lines, transit_shape, fields, deep)


def _ocr_file(contents: bytes, rid: str = "-", structured: bool = True, with_lines: bool = True,
             transit_shape: bool = True, fields: Optional[List[str]] = None, deep: bool = False) -> OcrResponse:
    deadline = time.monotonic() + MAX_SECONDS_PER_DOCUMENT
    kind = detect_kind(contents)
    if kind == "legacy":
        raise AnalysisError(415, LEGACY_MESSAGE)
    if kind is None:
        raise AnalysisError(400, "El archivo no es un PDF, una imagen ni un documento de Office válido.")
    pages: List[OcrPage] = []
    items: List[DocumentItem] = []
    warnings: List[str] = []
    images: Dict[int, np.ndarray] = {}  # imagen de cada página, solo si hay campos pedidos y modelo de visión
    want_images = bool(fields) and deep and client.enabled()

    def add(n: int, source: str, text: str, score: float, boxes: List[OcrBox], w: float, h: float,
            tables: Optional[List[Table]] = None, rotations: Optional[List[float]] = None) -> None:
        item: Optional[DocumentItem] = None
        if structured:
            try:
                # los extractores esperan el texto por renglones (no el orden de lectura por bloques)
                item = parse_page_text(boxes_to_text(boxes) if source == "ocr" else text, score,
                                       boxes if source == "ocr" else None)
            except Exception:
                logger.exception("[%s] falló la extracción de campos de la página %d", rid, n)
        if item:
            item.page = n
            items.append(item)
        pages.append(OcrPage(page=n, source=source, text=text, confidence=round(min(max(score, 0.0), 1.0), 3),
                             lines=_lines(boxes, w, h, with_lines), keyValues=_key_values(text),
                             tables=tables if tables else _safe_tables(boxes, w, h),
                             documentType=item.documentType if item else None,
                             rotations=[round(r, 2) for r in rotations or []]))

    if kind in ("docx", "xlsx"):  # Word/Excel: el texto se lee directo, sin OCR
        try:
            office_pages = read_office(contents, kind)
        except Exception:
            logger.exception("[%s] no se pudo leer el archivo de Office", rid)
            raise AnalysisError(422, "Archivo de Office dañado o protegido")
        for n, (text, tabs) in enumerate(office_pages, 1):
            add(n, "native", text, 1.0, [], 1.0, 1.0, tabs)
        if not pages:
            warnings.append("El documento no tiene texto.")
    elif kind == "image":
        read = _read_image(contents, deadline)
        if read:
            boxes, score, w, h, img, rots = read
            add(1, "ocr", _ordered_text(boxes), score, boxes, w, h, rotations=rots)
            if want_images:
                images[1] = img
        else:
            warnings.append("No se pudo leer texto en la imagen.")
    else:
        try:
            doc = pymupdf.open(stream=contents, filetype="pdf")
        except Exception:
            raise AnalysisError(422, "PDF inválido o corrupto")
        try:
            if doc.needs_pass:
                raise AnalysisError(422, "PDF protegido con contraseña")
            if doc.page_count == 0:
                raise AnalysisError(422, "El PDF no tiene páginas")
            last = min(doc.page_count, MAX_PDF_PAGES)
            if doc.page_count > MAX_PDF_PAGES:
                warnings.append(f"El PDF tiene {doc.page_count} páginas; solo se procesaron las primeras {MAX_PDF_PAGES}.")
            for n in range(1, last + 1):
                progress.report(n - 1, last)
                if time.monotonic() > deadline:
                    warnings.append(f"Tiempo máximo agotado: se procesaron {n - 1} de {last} páginas.")
                    break
                try:
                    page = doc[n - 1]
                    native = _native_page(page, with_lines)
                    if native:
                        raw, boxes, w, h = native
                        add(n, "native", raw.strip(), 1.0, boxes, w, h, tables_from_pdf_page(page))
                        if want_images and len(images) < 4:
                            images[n] = np.array(Image.open(io.BytesIO(page.get_pixmap(dpi=100).tobytes("png"))).convert("RGB"))
                        continue
                    read = _read_image(page.get_pixmap(dpi=_render_dpi(page)).tobytes("png"), deadline)
                    if read:
                        boxes, score, w, h, img, rots = read
                        add(n, "ocr", _ordered_text(boxes), score, boxes, w, h, rotations=rots)
                        if want_images and len(images) < 4:
                            images[n] = img
                    else:
                        warnings.append(f"Página {n}: no se encontró texto.")
                except AnalysisError:
                    raise
                except Exception:
                    logger.exception("[%s] página %d: error inesperado, se omite", rid, n)
                    warnings.append(f"Página {n}: no se pudo procesar.")
        finally:
            doc.close()

    if items:
        reconcile(items)
        finalize(items, nest_contact=transit_shape)
        for it in items:
            it.needsReview = it.confidence < 0.8
    found = find_fields(fields, pages, images, deadline) if fields and pages else None  # images vacío = sin modelo de visión
    return OcrResponse(text="\n\n".join(p.text for p in pages), pages=pages, documents=items, warnings=warnings,
                       fields=found)
