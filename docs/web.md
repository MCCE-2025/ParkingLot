# Web dashboard

A React SPA in [`web/`](../web/) shows live parking occupancy: summary tiles, a clickable per-spot grid (green = free, blue = occupied, matching the OpenCV overlay), and a full-width occupancy-percentage chart over the last 15 minutes with separate **device** (solid) and **manual** (dashed) series. A theme toggle switches between light, dark, and system color schemes.

Spots last updated via the dashboard show a **manual** badge and dashed outline (`source: "web"`). Device-driven spots have no badge (`source: "device"`). Older shadow or DynamoDB rows without `source` are treated as device events in the UI.

The SPA is served from a single CloudFront distribution and uses client-side routing:

- `/` — the live dashboard described above.
- `/?debug=1` — adds an **MQTT debug console**: a live, filterable message log (status / summary / control / system) with pause and manual disconnect/reconnect controls for demonstrating reconnection behavior.
- `/truth` — **ground-truth capture**: the same grid, but clicks are stored with `source: "truth"` (`device_id: truth_capture`) for labelling. Truth events are written to DynamoDB but do **not** update the live Device Shadow or summary, and the live dashboard filters them out.

**Data flow:**

1. On load, `GET /snapshot` reads the `occupancy` Device Shadow (same document shape as in the [Device Shadow section](aws.md#device-shadow-document), including per-spot `source` when present).
2. MQTT-over-WebSocket (SigV4 + Cognito Identity Pool) subscribes to `parkinglot/<lot_id>/status` and `.../summary` for live updates (read-only from the browser).
3. `GET /history` queries `ParkingLotEvents` in DynamoDB for the initial chart; each incoming status event (MQTT or after `POST /control`) is appended locally so the chart updates in real time.
4. Clicking a spot calls `POST /control` with `{ spot_id, occupied, source }`. A **Control** Lambda publishes to `parkinglot/<lot_id>/status`. For `source: "web"` (`device_id: web_control`) it also republishes the summary and updates the Device Shadow; for `source: "truth"` (`device_id: truth_capture`) it only publishes the status event. The grid and chart update when the MQTT status message arrives (or immediately from the API response); DynamoDB records the event via the existing IoT topic rule.

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/snapshot` | Latest occupancy from Device Shadow (DynamoDB fallback) |
| `GET` | `/history?lot_id=&from=&to=` | Time-series events for the occupancy chart |
| `POST` | `/control` | Manual spot override — body: `{ "spot_id": 0, "occupied": true, "source": "web" }` (`source` optional, `"web"` or `"truth"`, default `"web"`); response `{ "ok": true, "ts", "spot_id", "occupied", "source" }` |

Example manual override:

```bash
curl -X POST "$API_URL/control" \
  -H 'content-type: application/json' \
  -d '{"spot_id": 2, "occupied": true}'
```

The published status event uses `source: "web"` and `device_id: "web_control"` (or `source: "truth"` / `device_id: "truth_capture"` when posted from `/truth`) so it is distinguishable from camera events in DynamoDB and in the dashboard. Only `source: "web"` overrides update the Device Shadow and summary; `truth` events are status-only.

The Cognito unauthenticated role has **subscribe-only** MQTT access; writes go through the API so the shadow stays consistent with published status events.

## Prerequisites

- [Node.js](https://nodejs.org/) 20+ (`npm`)
- Device stack deployed (`ParkingLotStack`) and, for live data, the simulator or detector publishing to IoT

## Deploy

From the repository root:

```bash
cd infra
uv sync --all-groups
cdk deploy ParkingLotStack          # if not already deployed
uv run python scripts/deploy_web.py # npm build + cdk deploy DNS + web stacks
```

**Custom domain (`parkinglot.werschlan.at`):** the first deploy creates `ParkingLotDnsStack` in `us-east-1` (Route 53 hosted zone + ACM certificate). Copy the **`NameServers`** output and add NS records for `parkinglot.werschlan.at` at the parent zone (`werschlan.at`). After delegation propagates and the certificate is issued, redeploy (or run `deploy_web.py` again). Subsequent deploys can use `--skip-dns` if only the web stack changed.

Stack outputs include **`WebUrl`** (custom domain when configured), **`CloudFrontUrl`**, **`TruthCaptureUrl`** (the `/truth` ground-truth capture page), and **`ApiUrl`** (HTTP API). Open `WebUrl` in a browser.

To drive live updates without a camera:

```bash
cd parking_lot
uv run python simulator.py --spots 12 --interval 2 \
  --iot-endpoint ... --iot-client-id ... \
  --iot-cert ../certs/device.pem.crt --iot-key ../certs/private.pem.key \
  --iot-ca ../certs/AmazonRootCA1.pem
```

(use `build_simulator_cmd.py` to print the full command).

## Local development

See [`web/README.md`](../web/README.md). After the first deploy, copy stack outputs into `web/public/config.json` (see `web/public/config.example.json`) and run `npm run dev` in `web/`.
