"""Orquesta la lectura de imágenes y PDFs y la extracción de documentos. No conoce HTTP.

Principios: nunca lanzar una excepción por un archivo raro, no dejar que una página dañada tumbe el resto,
respetar un tiempo máximo por archivo (devolviendo lo ya procesado) y avisar de todo lo que no se pudo leer.
"""
import io
import logging
import time
from typing import List, Optional, Tuple

import pymupdf  # PyMuPDF
from PIL import Image

from app.config import (ENOUGH_CONFIDENCE, EXCELLENT_OCR_SCORE, MAX_PDF_PAGES, MAX_RENDER_PIXELS,
                        MAX_SECONDS_PER_DOCUMENT, MAX_SECONDS_PER_IMAGE, PDF_RENDER_DPI, REFOCUS)
from app.extraction.pipeline import parse_page_text
from app.llm.refine import refine_item
from app.ocr.boxes import boxes_to_text, to_boxes
from app.ocr.preprocess import load_image
from app.ocr.quality import is_good, quality, stats
from app.ocr.reader import candidates
from app.ocr.refocus import refocus
from app.schemas import DocumentItem
from app.services.handwriting import recover_contact
from app.services.output import finalize
from app.services.reconcile import reconcile

logger = logging.getLogger("document-api")

VLM_MIN_SECONDS = 90  # si queda menos tiempo que esto, no se llama al modelo de visión (tarda ~60 s)


class AnalysisError(Exception):
    """Error atribuible al archivo recibido; la capa HTTP lo traduce a un código de estado."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _is_empty(img) -> bool:
    """Imágenes en las que no puede haber un documento: diminutas o casi de un solo color."""
    h, w = img.shape[:2]
    return min(h, w) < 32 or max(h, w) / min(h, w) > 25 or float(img.std()) < 6.0  # ni un documento ni un recorte útil


def detect_kind(contents: bytes) -> Optional[str]:
    """'pdf', 'image' o None según el CONTENIDO del archivo (la extensión puede estar mal)."""
    if b"%PDF-" in contents[:1024]:
        return "pdf"
    try:
        with Image.open(io.BytesIO(contents)) as im:
            im.verify()
        return "image"
    except Exception:
        return None


def analyze_image(img_bytes: bytes, deadline: Optional[float] = None) -> Optional[DocumentItem]:
    """Lee una imagen y extrae el documento. Si faltan campos clave, reintenta con más mejoras
    de imagen y se queda con el intento que más campos extrae. `deadline`: instante (monotonic) límite."""
    base = load_image(img_bytes)
    if base is None or _is_empty(base):
        return None  # imagen diminuta, en blanco o de un solo color: no hay nada que leer
    best_item, best_key = None, (-1.0, -1.0)
    best_text, best_score, best_img = "", 1.0, None
    best_boxes, best_read = [], None
    t0 = time.monotonic()
    empty_reads = 0
    try:
        for res, oriented, read_img in candidates(base):
            boxes = to_boxes(res)
            text = boxes_to_text(boxes)
            if not text:
                empty_reads += 1
                if empty_reads >= 2:  # dos lecturas seguidas sin texto: seguir mejorando la imagen no ayudará
                    break
                continue
            item = parse_page_text(text, stats(res)[1], boxes)
            key = (item.confidence if item else 0.0, quality(res))
            if key > best_key:
                best_item, best_key = item, key
                best_text, best_score, best_img = text, stats(res)[1], oriented
                best_boxes, best_read = boxes, read_img
            if item and item.confidence >= ENOUGH_CONFIDENCE:
                break
            if is_good(res) and stats(res)[1] >= EXCELLENT_OCR_SCORE:
                break  # lectura excelente: si faltan campos, el límite es la extracción, no la imagen
            if time.monotonic() - t0 > MAX_SECONDS_PER_IMAGE or (deadline and time.monotonic() > deadline):
                logger.warning("Tope de tiempo alcanzado; se deja de reintentar")
                break
    except Exception:
        logger.exception("Falló el análisis de la imagen")
    if REFOCUS and best_item and best_read is not None and (deadline is None or deadline - time.monotonic() > 30):
        best_item, best_text = _refocus_item(best_item, best_text, best_score, best_boxes, best_read)
    if best_item and best_read is not None:  # contacto manuscrito (celular, correo, dirección) de cédula/RUNT
        recover_contact(best_item, best_boxes, best_read,
                        use_vlm=deadline is None or deadline - time.monotonic() > VLM_MIN_SECONDS)
    if best_item and (deadline is None or deadline - time.monotonic() > VLM_MIN_SECONDS):
        try:  # respaldo opcional con modelo local si faltan campos (no hace nada si LLM_URL está vacío)
            best_item = refine_item(best_item, best_text, best_score, best_img)
        except Exception:
            logger.exception("Falló el respaldo con modelo local; se conserva el resultado del OCR")
    return best_item


def _filled(item: DocumentItem) -> int:
    return sum(1 for v in item.extractedData.values() if v)


def _refocus_item(item: DocumentItem, text: str, score: float, boxes, img) -> Tuple[DocumentItem, str]:
    """Relee los fragmentos dudosos ampliados y vuelve a extraer; se conserva el resultado solo si no empeora."""
    try:
        new_boxes, changed = refocus(img, boxes)
        if not changed:
            return item, text
        new_text = boxes_to_text(new_boxes)
        new_item = parse_page_text(new_text, score, new_boxes)
        if (new_item and new_item.documentType == item.documentType
                and (_filled(new_item), new_item.confidence) >= (_filled(item), item.confidence)):
            logger.info("Relectura de recortes: %d fragmentos corregidos", changed)
            return new_item, new_text
    except Exception:
        logger.exception("Falló la relectura de recortes; se conserva la lectura original")
    return item, text


def _render_dpi(page: "pymupdf.Page") -> int:
    """DPI (entero) de render acotado: una página enorme no debe agotar la memoria."""
    longest_pt = max(page.rect.width, page.rect.height) or 1.0
    return int(max(40, min(PDF_RENDER_DPI, MAX_RENDER_PIXELS / longest_pt * 72)))


def analyze_pdf(contents: bytes, rid: str = "-",
                deadline: Optional[float] = None) -> Tuple[List[DocumentItem], List[str]]:
    """Analiza cada página: usa el texto nativo del PDF y, si no basta, hace OCR de la página."""
    try:
        doc = pymupdf.open(stream=contents, filetype="pdf")
    except Exception:
        raise AnalysisError(422, "PDF inválido o corrupto")

    detected: List[DocumentItem] = []
    warnings: List[str] = []
    try:
        if doc.needs_pass:
            raise AnalysisError(422, "PDF protegido con contraseña")
        total = doc.page_count
        if total == 0:
            raise AnalysisError(422, "El PDF no tiene páginas")
        last = min(total, MAX_PDF_PAGES)
        if total > MAX_PDF_PAGES:
            warnings.append(f"El PDF tiene {total} páginas; solo se procesaron las primeras {MAX_PDF_PAGES}.")
        for n in range(1, last + 1):
            if deadline and time.monotonic() > deadline:
                warnings.append(f"Tiempo máximo agotado: se procesaron {n - 1} de {last} páginas.")
                break
            try:  # una página dañada no debe tumbar el resto del archivo
                item = _analyze_pdf_page(doc[n - 1], n, last, rid, deadline)
            except Exception:
                logger.exception("[%s]   página %d/%d: error inesperado, se omite", rid, n, last)
                warnings.append(f"Página {n}: no se pudo procesar.")
                continue
            if item:
                item.page = n
                detected.append(item)
            else:
                warnings.append(f"Página {n}: no se reconoció ningún documento.")
    finally:
        doc.close()
    return detected, warnings


def _analyze_pdf_page(page: "pymupdf.Page", n: int, total: int, rid: str,
                      deadline: Optional[float]) -> Optional[DocumentItem]:
    # 1. Texto nativo (PDFs digitales)
    raw_text = page.get_text()
    if raw_text and len(raw_text.strip()) > 30:
        item = parse_page_text(raw_text)
        item = refine_item(item, raw_text) if item else None
        if item:
            logger.info("[%s]   página %d/%d: texto nativo -> %s (conf %.2f)",
                        rid, n, total, item.documentType, item.confidence)
            return item

    # 2. Fallback a OCR (páginas escaneadas)
    logger.info("[%s]   página %d/%d: ejecutando OCR...", rid, n, total)
    item = analyze_image(page.get_pixmap(dpi=_render_dpi(page)).tobytes("png"), deadline)
    logger.info("[%s]   página %d/%d: OCR -> %s", rid, n, total,
                f"{item.documentType} (conf {item.confidence:.2f})" if item else "sin documento reconocido")
    return item


def analyze_file(contents: bytes, rid: str = "-") -> Tuple[List[DocumentItem], List[str]]:
    """Punto de entrada: detecta el tipo por contenido y devuelve (documentos, avisos)."""
    deadline = time.monotonic() + MAX_SECONDS_PER_DOCUMENT
    kind = detect_kind(contents)
    if kind == "pdf":
        items, warnings = analyze_pdf(contents, rid, deadline)
    elif kind == "image":
        item = analyze_image(contents, deadline)
        items = [item] if item else []
        warnings = [] if item else ["No se reconoció ningún documento en la imagen."]
        if item:
            item.page = 1
    else:
        raise AnalysisError(400, "El archivo no es un PDF ni una imagen válida.")
    reconcile(items)  # corrige errores de OCR de un documento con los demás del mismo archivo
    finalize(items)  # forma final para el frontend (importes numéricos, contacto anidado)
    for it in items:
        it.needsReview = it.confidence < ENOUGH_CONFIDENCE
    return items, warnings
