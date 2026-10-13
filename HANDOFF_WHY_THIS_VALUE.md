# Why This Value — Handoff

## 1. Baseline

- Real Git checkout: `YANKEY-CODE/ReValue-AI`, origin `https://github.com/YANKEY-CODE/ReValue-AI.git`.
- Baseline branch: `work`.
- Baseline commit: `8dfe58eea0e79c9e6d2b9b3c9627a41597399c36` (`Batch exterior YOLO inference pipeline`). Read-only origin HEAD also matched this SHA.
- Feature branch: `feature/why-this-value`, created from the current checkout.
- Final commit hash: resolve with `git log -1 --format=%H feature/why-this-value`; also recorded in the final delivery message. This handoff is included in the feature commit.
- Pre-existing uncommitted changes: none. Baseline `git status --short` was empty.
- The uploaded brief mentions a different repository owner; the user's explicit repository link and actual checkout identify YANKEY-CODE, which was used.
- No reset, clean, stash, main push, or merge was performed.

## 2. What Was Implemented

The existing report evidence panel is now opened by **Why this value?**. Live results describe the confirmed device/storage, exterior grade, exact-model and condition-matched evidence counts, evidence strength, evidence origin, selected comparable count, method, available evidence dates, and limitations. Trade-in and repair references appear as separate context.

The backend adds a deterministic explanation to completed valuations, including insufficient-evidence results. Local public-seed evidence and local calibration are clearly distinguished from retrieved market evidence. An older backend response still displays minimal evidence without crashing. Demo Mode retains its existing rich static evidence content and prices. The button is keyboard usable and exposes its state through `aria-expanded` and `aria-controls`.

## 3. Architecture

The actual runtime is `backend.src.server`, serving `app/ReValue-AI-Prototype-v1.16.1-predeployment-preview.html` through `APP_HTML`. Implementation decisions came from runtime source, not historical README/source-lock documents.

Confirmed catalogue device and storage → exterior analysis → exterior grade → `/live-valuation` → existing market or fallback valuation → `with_valuation_explainability` → additive API JSON → `finalState.valuation` → `buildLiveAssessment` → `currentAssessment.valuation` → `renderPriceEvidence` → existing `priceEvidencePanel`.

The helper consumes completed output and request context. It does not select observations, query services, calculate prices, adjust prices, or mutate the original result. The existing local grade-to-condition helper is reused to describe the calibration path correctly. Deep copies protect explanation evidence/provenance from changes to the original nested data.

`buildLiveAssessment` retains the raw payload before applying its existing aliases used by reports, shop offers and chats. Its existing `comparable_count` alias still represents the exact-model count; the new contract distinguishes that from the number actually selected for the range.

The frontend keeps the existing single panel and evidence CSS. Demo rendering delegates to the existing rich renderer; live rendering consumes the new contract. Scoped text wrapping protects evidence content on small screens. Report/chat snapshots retain explainability with the assessment.

## 4. API Contract

Successful market/fallback valuation and insufficient-evidence valuation responses retain all original top-level fields and add `explainability`. Validation-error responses are unchanged. The current contract version is `why_this_value_v1`.

This is an excerpt from an actual local API request: a catalogue-confirmed Samsung Galaxy S24 Ultra 256GB, exterior grade A, without live-provider credentials. It used the existing local public seed and produced RM2,250–RM2,600, midpoint RM2,450, 11 exact-model/storage comparables and 7 condition-matched comparables. Sources and statistics are omitted from this excerpt for brevity; the API retains them.

```json
{
  "version": "why_this_value_v1",
  "device": {
    "brand": "Samsung",
    "model": "Galaxy S24 Ultra",
    "storage": "256GB"
  },
  "valuation": {
    "currency": "MYR",
    "value_low": 2250.0,
    "value_high": 2600.0,
    "midpoint": 2450.0,
    "status": "estimated_market_value"
  },
  "factors": [
    {
      "key": "device_configuration",
      "label": "Device & Configuration",
      "value": "Samsung Galaxy S24 Ultra · 256GB",
      "role": "market_match"
    },
    {
      "key": "exterior_condition",
      "label": "Exterior Condition",
      "value": "Grade A",
      "role": "condition_match",
      "condition_group": "clean"
    },
    {
      "key": "market_evidence",
      "label": "Malaysia Market Evidence",
      "value": "11 exact-model/storage comparables · 7 condition-matched",
      "role": "evidence"
    },
    {
      "key": "evidence_strength",
      "label": "Evidence Strength",
      "value": "Strong local evidence",
      "role": "evidence_quality"
    }
  ],
  "evidence": {
    "origin": "local_public_seed",
    "origin_label": "Local Malaysian public evidence seed",
    "exact_model_count": 11,
    "condition_matched_count": 7,
    "selected_comparable_count": 7,
    "evidence_status": "strong_local_evidence",
    "strength_label": "Strong local evidence",
    "observed_dates": [
      "2026-09-08"
    ]
  },
  "method": {
    "type": "resale_comparable_distribution",
    "range_method": "Q1–Q3 resale range, rounded to RM50",
    "summary": "The range uses the 25th–75th percentiles of Malaysian condition-matched exact model/storage resale comparables, rounded to RM50.",
    "main_value_drivers": [],
    "explanation": ""
  },
  "separate_context": {
    "trade_in_benchmark_count": 0,
    "repair_reference_count": 0,
    "summary": "Shown separately and not included in the resale market range."
  },
  "caveats": [
    "Final merchant offer may differ after physical inspection.",
    "The current internal diagnostic grade is not used to calculate this range.",
    "Exterior grade guides comparable matching; no separate per-damage RM deduction is calculated.",
    "Trade-in benchmarks and repair references are separate context, not included in the resale range.",
    "Local evidence was used; listings were not refreshed through live market retrieval for this result.",
    "Listing and retail prices are evidence of advertised prices, not a guaranteed sale or shop offer."
  ]
}
```

`evidence.sources` copies the original reported sources without asserting they were newly retrieved or verified. `statistics` copies existing distribution fields; `selected_observation_ids`, `main_value_drivers`, `range_method` and `explanation` preserve calibration provenance when available. Raw source URLs are not rendered as verified links, and no external request is needed to display the panel.

For insufficient evidence, the original null price fields remain null, the method type is `unavailable`, and the explanation states why no range can be produced. Counts and evidence strength describe the existing result; no confidence percentage or invented adjustment is emitted.

## 5. Files Changed

- `backend/src/valuation_explainability.py`: new non-mutating explanation builder and additive attachment helper; isolates explanation from calculation.
- `backend/src/server.py`: attaches the contract at the two existing valuation response points, using canonical query identity; preserves validation behavior.
- `app/ReValue-AI-Prototype-v1.16.1-predeployment-preview.html`: retains valuation data, upgrades the existing panel and toggle, restores the existing rich Demo evidence renderer, escapes backend text, and adds scoped wrapping.
- `backend/tests/test_explainable_valuation.py`: eight focused backend tests with standalone execution and pytest compatibility.
- `backend/tests/why_this_value_browser_smoke.cjs`: browser regression/safety checks using already available Playwright and Chromium; no dependency declarations added.
- `HANDOFF_WHY_THIS_VALUE.md`: baseline, contract, validation and integration handoff.

No historical documentation, dependency manifest, model, dataset or calibration file was edited.

## 6. Valuation Logic Safety

All answers below are **NO**:

- `value_low` changed: NO.
- `value_high` changed: NO.
- `midpoint` changed: NO.
- Comparable selection changed: NO.
- Condition mapping changed: NO.
- Market prices changed: NO.
- Rounding changed: NO.
- Valuation algorithm changed: NO.

`valuation.py`, `local_valuation.py`, `phase3_valuation_engine.py`, `market_evidence.py`, `schemas.py`, model weights and datasets are unchanged. Invariance tests compare the entire pre-attachment result with every non-explainability field after attachment, including nested distribution/evidence data, not only the price range. Coverage includes strong/moderate/limited/insufficient market evidence and all four supported grades across three existing fallback devices.

## 7. Truthfulness / Claims

- Market valuation uses model/storage matching and the exterior-grade-derived condition group. At least five condition-matched observations are selected when available; otherwise the existing exact-device evidence thresholds apply. The existing Q1–Q3 range and RM50 rounding are described.
- The local calibrated path uses existing device, condition and applicable grade-derived defect matching. Its existing limited-evidence min–max/widened-range method is preserved rather than falsely describing every fallback as simple Q1–Q3.
- The current user diagnostic/internal grade does not affect live valuation. The calibration adapter supplies a fixed B+ value, but the calibration selection/calculation does not use that field. The panel does not list it as a price driver.
- Individual YOLO findings are not passed to `/live-valuation`. They influence exterior grading upstream; the calibrated fallback also derives defect flags from that grade. No separate per-damage RM deduction is calculated or claimed.
- Trade-in/repair evidence remains outside resale-range calculations and is labelled separate context. Calibration's original explanation/driver text remains available without reinterpreting it as additive price contributions.
- Local seeds are not labelled fresh live retrieval. Available observation dates and a not-refreshed caveat are shown. Stale/unknown selected market dates receive explicit caveats.
- Evidence labels follow existing status/tier values; no new weighting, confidence percentages or neural-network price claims were introduced.

## 8. Tests Added

Backend tests:

1. `test_normal_market_evidence_and_full_invariance`: correct known range/counts/identity/grade/strength/statistics, complete output invariance, and non-mutation of copied sources.
2. `test_moderate_and_limited_evidence_explain_actual_selection`: actual selection labels when condition evidence is limited; no valuation changes.
3. `test_local_fallbacks_preserve_prices_and_method_provenance`: three fallback devices × four grades, original ranges/methods/drivers/explanations/selected IDs and truthful origins.
4. `test_insufficient_evidence_has_no_invented_price`: market and unsupported fallback null prices, honest insufficiency and JSON-safe contract.
5. `test_internal_grade_and_damage_are_not_numeric_price_drivers`: no invented internal or per-damage price factors/deltas.
6. `test_trade_in_and_repair_remain_separate_from_distribution`: counts and labels are separate and resale distribution remains identical.
7. `test_http_contract_for_market_fallback_insufficient_and_invalid_requests`: real handler responses for all response paths, complete compatibility, unchanged invalid requests, and ignoring extra internal-grade pricing input.
8. `test_canonical_identity_when_request_omits_brand_model`: canonical catalogue identity and normalized storage survive explanation construction.

Browser scenarios cover local public seed, calibrated fallback, unsupported/insufficient evidence, older responses, escaped malicious text, and rich Demo evidence. They also test keyboard expand/collapse, price invariance on toggle, preserved assessment payload, shop/report/chat sharing, single-panel behavior and mobile panel bounds.

## 9. Test Commands and Results

Commands were run from the checkout root using the already prepared Python environment. No new dependency was installed for this task.

- Baseline: `.venv-yolo/bin/python -m pytest backend/tests -q` → **48 passed**.
- Implementation: `.venv-yolo/bin/python -m pytest backend/tests -q` → **56 passed**, including all 48 existing tests and 8 new tests.
- Focused: `.venv-yolo/bin/python -m pytest backend/tests/test_explainable_valuation.py -q` → **8 passed**.
- `.venv-yolo/bin/python backend/tests/run_backend_self_test.py` → **backend self-test passed**.
- `for test_file in backend/tests/test_*.py; do .venv-yolo/bin/python "$test_file"; done` with fail-fast shell mode → all **7 standalone test files passed**, including the 8 new explainability tests and all 6 original scripts.
- Backend started with its existing command, `PORT=8766 .venv-yolo/bin/python -m backend.src.server`, with local writable YOLO/matplotlib config paths.
- `node backend/tests/why_this_value_browser_smoke.cjs` → all **6 browser scenarios passed**, zero page errors; keyboard toggle, report/chat sharing and mobile panel bounds passed.
- Real API smoke, without provider stubbing: `/catalogue/search` → `/confirm-device` → `/live-valuation` for S24 Ultra 256GB grade A → **estimated_market_value**, low **2250**, high **2600**, midpoint **2450**, counts **11 / 7**, explanation **why_this_value_v1**.
- Extracted both inline frontend scripts and ran `node --check` on each → **2 script blocks passed**.
- `git diff --check` → **passed**, no whitespace errors.

The first browser runs identified test synchronization and heading-case assumptions, which were corrected. A full-page width assertion also failed: comparison against baseline HTML showed an existing small page overflow. The final check verifies the panel stays in the mobile viewport and that long escaped evidence adds no page overflow. Scoped wrapping is retained. These were investigated rather than skipped.

## 10. Manual Verification Steps

1. Start the existing backend from the checkout root with `.venv-yolo/bin/python -m backend.src.server` using the prepared environment. Inject any existing provider credential securely if testing actual live retrieval.
2. Open the app in the supported browser/runtime; select Value Another Phone, a catalogue model and storage, then complete the five-view exterior scan and internal workflow.
3. On Phone Value, record the market range, then press **Why this value?** with mouse or keyboard. Confirm the range does not change.
4. Confirm device/storage, exterior grade, evidence counts/strength/origin, selected count, method and limitations match the returned API contract.
5. If live retrieval is unavailable, use a supported local device: Samsung S24 Ultra 256GB or iPhone 13 Pro/S23 Ultra 256GB. Verify the origin is local evidence, not newly retrieved market evidence.
6. For a calibrated fallback with benchmarks, verify additional context explicitly excludes trade-in/repair from the resale range.
7. Test an unsupported local model/storage without provider access: no price is invented and insufficiency remains clear.
8. Close the panel; verify `aria-expanded` becomes false. Reopen and confirm only one evidence panel exists.
9. Continue to shops and share the report/contact a shop. Confirm device, range and report behavior remain consistent.
10. Try Demo Mode: its richer static evidence and existing demo ranges remain available.
11. For automated browser validation, start on port 8766 and run `node backend/tests/why_this_value_browser_smoke.cjs`. `REVALUE_TEST_BASE` and `CHROMIUM_PATH` can override the backend URL and browser executable.

## 11. Known Limitations

- Actual Gemini/search-grounded retrieval was not exercised because no credential is configured. The live market calculation/API branch is covered with deterministic provider-result fixtures; the actual keyless fallback API was exercised.
- Browser regression tests fixture the analysis response and mark diagnostics complete to isolate this feature. They exercise five-photo upload and the report/shop/chat path but do not claim new validation of inference or diagnostic timing.
- The current internal diagnostic grade is not a live valuation driver. This feature explains that behavior and does not change it.
- The local public seed retains its original dates and flags; this feature does not refresh or repair datasets.
- Calibration's reported source list can include separate benchmark/repair sources. It is preserved internally and is not presented as a count of verified resale sources.
- The toggle supports English/Chinese labels, while the new live explanation headings/content are English. Existing Demo language content remains unchanged.
- Baseline HTML has a small full-page mobile width overflow in Chromium; the explanation panel fits the viewport and long escaped values add no overflow. No unrelated global layout redesign was made.
- No remote push, PR, merge or deployment was performed.

## 12. Merge / Integration Notes

Likely conflict files are `backend/src/server.py` and the served v1.16.1 HTML. Review the current main's served entrypoint and valuation branches before cherry-picking or merging. Preserve whichever current response paths main uses and attach the builder only after its existing valuation calculation.

The contract is additive; validation-error fields/semantics remain unchanged. The frontend tolerates missing or unknown-version explainability using minimal evidence. Retain the raw valuation payload and existing aliases needed by shops/chats. Keep the selected-range count separate from the legacy assessment exact-model count.

The helper reuses `_condition_from_grade` from the existing local adapter; if main renames or changes that adapter, update this reference against actual behavior. Do not replace its condition mapping with assumptions.

Demo evidence restoration deliberately reuses the existing renderer rather than a second panel. Keep the legacy `renderPriceEvidence` capture before the later window override. Verify this against any newer frontend overrides.

No Decision Engine/IMEI work was added. Integrators should keep those independent and preserve the truthful distinction between valuation drivers and assessment context.

DO NOT MERGE BLINDLY.
REVIEW AGAINST THE CURRENT MAIN BRANCH FIRST.

## 13. Git Diff Summary

Implementation diff (excluding this handoff):

```text
...AI-Prototype-v1.16.1-predeployment-preview.html |  43 ++++-
 backend/src/server.py                              |  10 +-
 backend/src/valuation_explainability.py            | 127 +++++++++++++
 backend/tests/test_explainable_valuation.py        | 205 +++++++++++++++++++++
 backend/tests/why_this_value_browser_smoke.cjs     | 108 +++++++++++
 5 files changed, 485 insertions(+), 8 deletions(-)
```

The feature changes six files: one served frontend and one backend route file, plus a new builder, backend tests, browser tests and this handoff. No valuation engine, dataset, model or dependency declaration changed. The committed diff summary can be reproduced with:

```bash
git diff --stat 8dfe58eea0e79c9e6d2b9b3c9627a41597399c36 feature/why-this-value
```
