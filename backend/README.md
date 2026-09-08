# ReValue AI Phase 6A.3 Backend

This backend is the server-side foundation for real exterior intelligence.

Working locally without external dependencies:

- deterministic exterior grading from supplied atomic damage
- hybrid merge logic for supplied YOLO and Gemini verification results
- descriptive Malaysia valuation calculation from supplied evidence rows
- secret-safe Gemini interface that requires a backend environment key
- YOLO11n v0.2 detector integration endpoint and model path

Blocked in this package:

- live Gemini image identification, because no backend Gemini credential is present
- YOLO inference in this local environment, because Ultralytics/Torch are not installed
- live market retrieval, because no production evidence source or search-grounded backend credential is configured

Installed YOLO model:

- `backend/models/revalue_exterior_yolo11n_v0.2.pt`

Exterior analysis endpoint:

- `POST /analyze-exterior`

Run a simple stdlib HTTP server:

```powershell
python -m backend.src.server
```

The frontend must not receive any Gemini API key.
