# Architecture and engineering boundaries

ReValue AI is intentionally compact: one browser UI and one Python `ThreadingHTTPServer`. This keeps the judge experience simple and makes local execution independent of Render.

```mermaid
sequenceDiagram
  participant B as Browser UI
  participant S as Python server
  participant C as Catalogue and data files
  participant Y as YOLO runtime
  participant R as Report storage
  B->>S: Catalogue, exterior, valuation, decision requests
  S->>C: Validate device, evidence and MYR inputs
  S->>Y: Run exterior model when installed and usable
  Y-->>S: Detections or explicit unavailable state
  S-->>B: Qualified grade, value, explanation, next step
  B->>S: PDF or opt-in snapshot request
  S->>R: SQLite locally; PostgreSQL only with explicit URL
  S-->>B: Private PDF or seven-day report and QR references
```

| Area | Primary implementation | Boundary that matters |
| --- | --- | --- |
| Browser journey | `app/ReValue-AI-Prototype-v1.16.1-predeployment-preview.html` | UI presentation cannot make an indicative or sample value verified evidence. |
| HTTP server | `backend/src/server.py` | Serves UI, assets, and API routes on port 8765. |
| Device identity | `backend/src/phone_catalogue.py` | Confirmation precedes live valuation; variants are safety-checked. |
| Exterior analysis | `exterior_analysis.py`, `yolo_detector.py` | Missing model, dependency, or image data returns unavailable/pending. |
| Valuation | `market_evidence.py`, `valuation.py`, `valuation_explainability.py` | MYR evidence is validated; unsupported evidence does not create an estimate. |
| Decision support | `decision_engine.py` | User goals change presentation; unverified offers and illustrative grades cannot become verified inputs. |
| Reports | `report_sharing.py` | Sanitised snapshots, masked IMEI, 7-day expiry, SQLite locally or explicit PostgreSQL. |

## Storage modes

- **Local launcher:** `REVALUE_REPORT_DB=.local-run/report_snapshots.sqlite3`, while `REVALUE_REPORT_DATABASE_URL` is explicitly blank. The process uses local SQLite only.
- **Production:** a valid, explicitly injected PostgreSQL URL activates PostgreSQL. A malformed or failing production configuration never silently falls back to SQLite.

Gemini and live grounded market retrieval are optional server-side integrations. Their absence is a documented capability boundary, not an invitation to substitute fabricated analysis, quotes, or evidence.
