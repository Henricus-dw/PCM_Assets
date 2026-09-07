# TabletForm

A standalone, installable web app (PWA) for warehouse pickers to capture stock movements on a
tablet. It is fully independent from PCM_Tracer's UI — it only talks to it over HTTP to submit
data into the same database.

## What it captures

Date and Time are set automatically. The picker fills in:

- Bin Location
- SKU / Barcode
- Qty
- Picker Detail
- Shipment #

Submissions are stored in the `TabletForm` table in the same database PCM_Tracer uses
(`TabletFormEntry` model in `PCM_Tracer/models.py`), via `POST /api/tablet-form`
(`PCM_Tracer/main.py`).

## Running / installing on a tablet

This app lives inside the PCM_Tracer repo (`PCM_Tracer/tablet_form/`) and is served directly by
PCM_Tracer at `/tablet-form` (mounted as static files in `PCM_Tracer/main.py`), so it deploys and
runs on the same VM/process — a normal `git pull` + restart ships it along with everything else.

1. Start PCM_Tracer as usual (make sure it's reachable on the VM's network address, e.g.
   `uvicorn main:app --host 0.0.0.0 --port 8000`).
2. On the tablet's browser, go to `http://<vm-ip>:8000/tablet-form/`.
3. Requests go to the same origin automatically — no server URL setup needed. The gear icon
   still lets you point at a different server URL if you ever host this app elsewhere.
4. Use the browser menu → "Add to Home screen" / "Install app" to install it like a native app.

## Offline behaviour

If the PCM_Tracer server is unreachable, submissions are queued in the browser's local storage
and automatically retried when the connection is restored (or every 30 seconds).

## Backend requirements

- `PCM_Tracer` must be running and reachable from the tablet's network.
- CORS is already open (`allow_origins=["*"]`) in `PCM_Tracer/main.py`, so no server changes are
  needed to allow requests from this app's origin.
