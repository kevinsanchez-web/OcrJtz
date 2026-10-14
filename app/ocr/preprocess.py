"""Preparación de imágenes: carga, giro y mejoras para fotos borrosas, pequeñas u oscuras."""
import io
import logging
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

from app import imaging  # noqa: F401  (registra el formato HEIC de los iPhone)

logger = logging.getLogger("document-api.ocr")


def load_image(img_bytes: bytes) -> Optional[np.ndarray]:
    """Abre la imagen (respetando la orientación EXIF) y la deja en un tamaño razonable para el OCR."""
    try:
        pil_img = ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))).convert("RGB")
    except Exception:
        logger.exception("No se pudo abrir la imagen")
        return None
    if max(pil_img.size) > 3200:  # acotar el tiempo de proceso
        pil_img.thumbnail((3200, 3200))
    elif 32 <= min(pil_img.size) and max(pil_img.size) < 900:  # imágenes pequeñas: ampliar antes de leer
        k = 1400 / max(pil_img.size)
        pil_img = pil_img.resize((int(pil_img.width * k), int(pil_img.height * k)), Image.BICUBIC)
    return np.array(pil_img)


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    """Gira la imagen `angle` grados en sentido antihorario ampliando el lienzo (sin recortar)."""
    if abs(angle) < 0.05:
        return img
    if abs(angle - 90) < 0.3:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if abs(angle + 90) < 0.3:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    bg = tuple(int(v) for v in np.median(edge, axis=0))
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=bg)


def enhance(img: np.ndarray) -> np.ndarray:
    """Mejora general: ampliado, normalización de sombras, reducción de ruido, contraste y enfoque."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    long_side = max(gray.shape)
    if long_side < 1800:  # ampliar imágenes pequeñas (hasta x3)
        scale = min(3.0, 1800 / long_side)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # Normalizar iluminación (sombras): dividir por el fondo estimado
    bg = cv2.medianBlur(cv2.dilate(gray, np.ones((15, 15), np.uint8)), 31)
    gray = cv2.divide(gray, bg, scale=255)
    gray = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)
    gray = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    blur = cv2.GaussianBlur(gray, (0, 0), 3)
    gray = cv2.addWeighted(gray, 1.8, blur, -0.8, 0)  # enfoque (unsharp mask)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)


def _upscaled_gray(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    k = min(4.0 if max(gray.shape) < 700 else 2.0, 3000 / max(gray.shape))
    return cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_LANCZOS4) if k > 1 else gray


def enhance_clahe(img: np.ndarray) -> np.ndarray:
    """Ampliado Lanczos + contraste local: recupera texto en imágenes muy borrosas."""
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(_upscaled_gray(img))
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)


def enhance_unsharp(img: np.ndarray) -> np.ndarray:
    """Ampliado + enfoque fuerte (compensa el desenfoque)."""
    gray = _upscaled_gray(img)
    gray = cv2.addWeighted(gray, 2.5, cv2.GaussianBlur(gray, (0, 0), 2.5), -1.5, 0)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
