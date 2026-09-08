from __future__ import annotations

import base64
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schemas import DamageType, ImageInput, ViewType, YoloDetection


YOLO_MODEL_NAME = "revalue_exterior_yolo11n_v0.2"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "revalue_exterior_yolo11n_v0.2.pt"
ULTRALYTICS_CONFIG_DIR = Path(__file__).resolve().parents[2] / "Ultralytics"
DEFAULT_IMGSZ = 512
DEFAULT_CONF = 0.25
RAW_CLASS_NAMES = {
    0: "generic_crack",
    1: "scratch",
}


class YoloDetectorUnavailable(RuntimeError):
    """Raised when YOLO dependencies or model weights are unavailable."""


def map_yolo_class_to_damage_type(raw_class: str, view: ViewType) -> DamageType | None:
    """Map raw YOLO class names to ReValue atomic damage only when safe."""

    raw = raw_class.strip().lower()
    if raw == "scratch":
        return "scratch"
    if raw == "generic_crack" and view == "front":
        return "screen_crack"
    if raw == "generic_crack" and view == "back":
        return "back_glass_crack"
    return None


@lru_cache(maxsize=1)
def load_yolo_model(model_path: str = str(DEFAULT_MODEL_PATH)) -> Any:
    path = Path(model_path)
    if not path.exists():
        raise YoloDetectorUnavailable(f"YOLO model weights not found: {path}")
    try:
        ULTRALYTICS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(ULTRALYTICS_CONFIG_DIR))
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise YoloDetectorUnavailable(
            "Ultralytics YOLO is not installed. Install backend requirements before real inference."
        ) from exc
    return YOLO(str(path))


def _suffix_for_mime(mime_type: str | None) -> str:
    mime = (mime_type or "").lower()
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    return ".jpg"


def _write_temp_image(image: ImageInput) -> Path:
    if not image.bytes_b64:
        raise ValueError(f"Image for view '{image.view}' does not include bytes_b64.")
    try:
        raw = base64.b64decode(image.bytes_b64, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 image payload for view '{image.view}'.") from exc
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=_suffix_for_mime(image.mime_type))
    with handle:
        handle.write(raw)
    return Path(handle.name)


def _to_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _detections_from_result(result: Any, view: ViewType) -> list[YoloDetection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    names = getattr(result, "names", None) or RAW_CLASS_NAMES
    class_ids = _to_list(boxes.cls)
    confidences = _to_list(boxes.conf)
    xyxy_values = _to_list(boxes.xyxy)

    detections: list[YoloDetection] = []
    for class_id_raw, confidence_raw, xyxy_raw in zip(class_ids, confidences, xyxy_values):
        class_id = int(class_id_raw)
        raw_class = str(names.get(class_id, RAW_CLASS_NAMES.get(class_id, f"class_{class_id}")))
        mapped = map_yolo_class_to_damage_type(raw_class, view)
        detections.append(
            YoloDetection(
                raw_class=raw_class,
                damage_type=mapped,
                confidence=float(confidence_raw),
                view=view,
                bounding_box_xyxy=[float(v) for v in xyxy_raw],
                source=YOLO_MODEL_NAME,
                requires_gemini_resolution=mapped is None,
            )
        )
    return detections


def detect_exterior_damage(
    images: list[ImageInput],
    *,
    model_path: str = str(DEFAULT_MODEL_PATH),
    imgsz: int = DEFAULT_IMGSZ,
    conf: float = DEFAULT_CONF,
) -> list[YoloDetection]:
    model = load_yolo_model(model_path)
    all_detections: list[YoloDetection] = []
    temp_paths: list[Path] = []
    try:
        for image in images:
            path = _write_temp_image(image)
            temp_paths.append(path)
            results = model.predict(source=str(path), imgsz=imgsz, conf=conf, verbose=False)
            for result in results:
                all_detections.extend(_detections_from_result(result, image.view))
    finally:
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
    return all_detections
