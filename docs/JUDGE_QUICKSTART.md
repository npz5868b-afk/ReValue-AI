# ReValue AI — Judge Quickstart

This is the fastest reproducible route through the local demonstration. It describes only capabilities that are present in the repository.

## Start in three steps

1. Download the repository ZIP from GitHub and extract it anywhere, including a path with spaces or non-English characters.
2. Install 64-bit Python **3.10–3.13** if necessary. Enable the Python launcher if the installer offers it.
3. Double-click `START_REVALUE_AI.bat` in the extracted folder.

The first run creates `.venv`, downloads the packages in `backend/requirements.txt`, starts the local server, waits for `http://127.0.0.1:8765/health`, then opens the browser. This requires internet only for first-time package installation. Later launches reuse the environment.

The launcher refuses a busy port, never stops an unrelated process, and does not open the browser until the health endpoint succeeds.

## What to show a judge

1. Select **Demo Mode** for the curated product tour, or identify a supported device in the local catalogue.
2. Walk through capture, condition context, checksum-only IMEI check, report, explainability, decision priority, PDF export/share panel, sample shops, and chats.
3. Point out the qualification labels: indicative market range, illustrative internal profile, checksum-only IMEI, and illustrative sponsored shop listings.

The one-computer demo does not need production credentials. PDF export works locally. Share links and QR codes are local snapshots with seven-day expiry. A `127.0.0.1` QR code is deliberately only usable on the same computer.

## Stop and reset

- Double-click `STOP_REVALUE_AI.bat` to stop only the process recorded by the launcher and confirmed as owner of port 8765.
- Double-click `RESET_DEMO.bat` to remove local report snapshots. It asks for confirmation, asks to stop a launcher-managed server if needed, and changes only `.local-run/report_snapshots.sqlite3` and SQLite sidecar files.

The browser stores demonstration chat history under the `revalueAiChats` key for this app's localhost origin. Reset queues a one-time `reset_local_demo` launch: the next launcher-opened localhost page clears that ReValue AI key only, then removes the query from the address bar. It does not touch storage for any other website.

## Local capability matrix

| Capability | Local result | Important boundary |
| --- | --- | --- |
| Product UI, catalogue, static assets | Included in ZIP | No Render dependency |
| Demo journey | Available | Internal profile is illustrative |
| IMEI | Format + checksum | No blacklist, ownership, lock, or stolen-device lookup |
| Valuation/explainability | Included evidence and safeguards | Indicative MYR range, not a guaranteed offer |
| YOLO exterior path | Included weights and code | Needs installed ML packages and usable photos; unavailable analysis stays pending |
| Gemini/lived grounded retrieval | Optional | Needs a server-side key; no key is needed to launch |
| PDF | Local | Does not create a share link |
| Share link | Local SQLite snapshot, 7 days | Localhost link is same-computer only |
| Shops and chats | Demonstration flow | Listings, distances, fit scores, and estimates are illustrative/sponsored |

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Python version error | Install 64-bit Python 3.10–3.13. Python 3.14 is not validated for this dependency set. |
| Dependency installation fails | Check internet or proxy access; the terminal keeps the real error visible. |
| Port 8765 is busy | Close the program that owns the port, then retry. ReValue will not terminate it. |
| Browser does not open | Visit `http://127.0.0.1:8765` once the terminal says Ready. |
| Exterior result is pending/blocked | Confirm dependencies installed, then use five usable images. The system must not invent a grade. |
| QR does not open on a phone | Expected for localhost. Continue on the same computer or configure a reachable base URL outside the default judge workflow. |
| Need a clean presentation | Stop, run reset, confirm deletion, then start again. |

## Privacy and verification notes

The local launcher clears `REVALUE_REPORT_DATABASE_URL` and `REVALUE_PUBLIC_BASE_URL` for its child process. Local reports use `.local-run` SQLite, so no Supabase credentials are read or written. A genuine clean-machine installation still requires a Windows machine with a supported Python version and package-network access on first launch; that independent machine is not available inside this workspace.
