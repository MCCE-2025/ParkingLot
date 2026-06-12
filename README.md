# Parking Space Detection in OpenCV
For a fun weekend project, I decided to play around with the OpenCV (Open Source Computer Vision) library in python.

OpenCV is an extensive open source library (available in python, Java, and C++) that's used for image analysis and is pretty neat.

The lofty goal for my OpenCV experiment was to take any static image or video of a parking lot and be able to automatically detect whenever a parking space was available or occupied.

Through research and exploration, I discovered how lofty of a goal that was (at least for the scope of a weekend). What I was able accomplish was to detect how many spots were available in a parking lot, with just a bit of upfront work by the user.

This page is a walkthrough of my process and what I learned along the way.

I'll start with an overview, then talk about my process, and end with some ideas for future work.

## Setup

Install [uv](https://docs.astral.sh/uv/) if needed (`curl -LsSf https://astral.sh/uv/install.sh | sh`), then from the repository root:

```bash
uv sync
```

This creates `.venv/` and installs OpenCV, NumPy, PyYAML, and the AWS IoT SDK (`awsiotsdk`). Dependencies are declared in `pyproject.toml` with a lockfile (`uv.lock`) for reproducible installs.

Run commands with `uv run` from `parking_lot/` (as in the examples below), or from the repo root with paths such as `uv run python parking_lot/main.py`.

## Overview
[![Unedited parking lot](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/parking_shot.png)](https://www.youtube.com/watch?v=SszV59YBn_o)

The above link takes you to a video of the parking space detection program in action.

The marking frame always comes from `--video` itself — either a live webcam frame or a still extracted from a video file — so the layout you mark on is the same view detection will run against.

Against a connected webcam, pass the camera's device index (an integer such as `0` for the default camera) to `--video`. Just give the program a data file path and a webcam, and it will automatically grab a frame for you to mark spots on, then start live detection:

```bash
cd parking_lot
uv run python main.py --data data/coordinates_webcam.yml --video 0
```

Against a recorded video file, point `--video` at the file. If the data file is empty (or doesn't exist yet), a still is pulled from the video at `--start-frame` so you can mark spots on the same frame detection will start at:

```bash
uv run python main.py --data data/coordinates_1.yml --video videos/parking_lot_1.mp4 --start-frame 400
```

The marking step runs automatically when the `--data` file is missing or contains no spots (e.g. a freshly-created file or one whose contents are `[]`). Once spots are saved, subsequent runs skip marking and go straight to detection.

Useful extras:

```bash
# Save the captured marking frame to disk, e.g. for debugging or as a
# visual reference of how the spots were laid out.
uv run python main.py --snapshot images/snapshot.png \
    --data data/coordinates_webcam.yml --video 0

# Re-mark spots even though the coordinates file already exists. A fresh
# frame is captured from --video (webcam or file) automatically.
uv run python main.py --remark --data data/coordinates_webcam.yml --video 0
```

If you have multiple cameras connected, try `--video 1`, `--video 2`, etc. The `--start-frame` flag is ignored when reading from a webcam (live streams are not seekable).

### Keyboard shortcuts

`main.py` delegates keyboard handling to two interactive OpenCV phases. While the **Parking Lot** window is focused:

**Spot marking** (runs when the `--data` file is empty or you pass `--remark`; the same legend is drawn in the top-left corner of the window):

- **Left-click × 4** — mark the corners of a parking spot
- **u** — undo the last spot (or any in-progress clicks)
- **r** — reset and clear all spots
- **q** — quit marking and save the current spots to the `--data` file

**Live detection** (runs after marking, or immediately if spots already exist):

- **q** — quit the detection loop and close the window

Spots are only written to the `--data` file when you press **q** during marking. To stop the program from the terminal at any time, use **Ctrl+C**.

### Webcam hardware controls

Live webcam feeds drift in brightness as lighting changes. Rather than software-correcting after the fact, you can set the camera's hardware controls directly — the same V4L2 controls you'd otherwise tweak with `v4l2-ctl --set-ctrl=brightness=192` on Linux. Under the hood this uses `cv2.VideoCapture.set(CAP_PROP_BRIGHTNESS, ...)` etc., which OpenCV routes to V4L2 on Linux, DirectShow on Windows, and AVFoundation on macOS.

Available flags (all optional, all only apply when `--video` is a webcam):

- `--brightness <value>`
- `--contrast <value>`
- `--saturation <value>`
- `--gain <value>`
- `--exposure <value>`
- `--auto-exposure <value>` (on most V4L2 UVC cameras: `3` = auto, `1` = manual)

Value ranges are camera/driver specific — run `v4l2-ctl -L` (Linux) or check the camera's docs for valid values. Example:

```bash
# Pin brightness to 192 and switch to manual exposure at 250
uv run python main.py --data data/coordinates_webcam.yml --video 0 \
    --auto-exposure 1 --exposure 250 --brightness 192
```

If the driver rejects a control (some cameras don't expose all of them), the program logs a warning and continues with the rest.

#### Closed-loop auto-brightness

If you don't want to pin a fixed value, `--auto-brightness` runs a simple feedback loop: it samples the mean luminance of each frame and nudges a hardware control up or down to keep it near `--auto-brightness-target` (default `128`, the neutral midtone). It auto-detects which control to drive (tries `exposure`, then `gain`, then `brightness`), or you can pin one with `--auto-brightness-prop`.

```bash
# Default: target mean luminance 128, auto-detect which control to drive.
uv run python main.py --data data/coordinates_webcam.yml --video 0 \
    --auto-exposure 1 --auto-brightness

# Drive the brightness control specifically and aim for slightly brighter
# (mean=160) than midtone.
uv run python main.py --data data/coordinates_webcam.yml --video 0 \
    --auto-brightness --auto-brightness-prop brightness \
    --auto-brightness-target 160
```

Notes:
- On most V4L2 UVC cameras you'll want `--auto-exposure 1` (manual mode) before driving `exposure` yourself; otherwise the camera's own AE loop will fight ours.
- The controller has a small dead-zone and rate-limits adjustments to roughly twice a second, which prevents oscillation but means it takes a few seconds to settle after a big lighting change.

Program flow is as follows:
- User inputs a video source (a webcam device index or a video file path) and a path for the output file of parking space coordinates. When the data file is empty, a still frame is pulled from that same video source for the user to mark spots on.
- User marks spots interactively (see [Keyboard shortcuts](#keyboard-shortcuts) above).
- Video begins with the user provided boxes overlayed the video. Occupied spots initialized with red boxes, available spots with green.
    - Car leaves a space, the red box turns green.
    - Car drives into a free space, the green box turns red.

To remove individual spots after the fact, edit the YAML file by hand or re-run with `--remark` to start over.

The data on the entering and exiting of these cars can be used for a number of purposes: closest spot detection, analytics on parking lot usage, and for those counters outside of parking garages that tell you how many cars are on each level (to name a few).

This project was my first tour through computer vision, so to get it working in a weekend, I went the "express learning" route. That consisted of auditing this [Computer Vision and Image Analytics course](https://www.edx.org/course/computer-vision-and-image-analysis), reading through [OpenCV documentation](https://docs.opencv.org/2.4/modules/refman.html), querying the net, and toggling OpenCV function parameters to see what happened. Overall, a lot of learning and a ton of fun.

## Process
### The beginning
My first thought was how can I tell whether a parking space is empty?

Well, if a space is empty, it would be the color of the pavement. Otherwise, it wouldn't be.

I also knew that I needed a way to mark the boundaries of the space, so that I could return the number of spots available.

Let's grab an image and head to the OpenCV docs!

### Line Detection
To detect the parking spots, I knew I could take advantage of the lines demarking the boundaries.

The Hough Transform is a popular feature extraction technique for detecting lines. OpenCV encapsulates the math of the Hough Transform into HoughLines(). Further abstraction in captured in HoughLinesP(), which is the probabilistic model of creating lines with the points that HoughLines() returns. For more info, check out the [OpenCV Hough Lines tutorial.](https://docs.opencv.org/3.0-beta/doc/py_tutorials/py_imgproc/py_houghlines/py_houghlines.html)

The following is a walkthrough to prepare an image to detect lines with the Hough Transform. Links point to OpenCV documentation for each function. Arguments for each function are given as keyword args for clarity.

[Reading](https://docs.opencv.org/master/d4/da8/group__imgcodecs.html#ga288b8b3da0892bd651fce07b3bbd3a56) in this image:
```python
img = cv2.imread(filename='examples/hough_lines/p_lots.jpg')
```
![Org_hough](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/org.png)



I [converted it to gray scale](https://docs.opencv.org/master/d7/d1b/group__imgproc__misc.html#ga397ae87e1288a81d2363b61574eb8cab) to reduce the info in the photo:
```python
gray = cv2.cvtColor(src=img, code=cv2.COLOR_BGR2GRAY)
```

![Gray_hough](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/s_gray.png)



Gave it a good [Gaussian blur](https://docs.opencv.org/master/d4/d86/group__imgproc__filter.html#gaabe8c836e97159a9193fb0b11ac52cf1) to remove even more unnecessary noise:
```python
blur_gray = cv2.GaussianBlur(src=gray, ksize=(5, 5), sigmaX=0)
```
![Blur_hough](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/s_blur.png)



Detected the edges with [Canny](https://docs.opencv.org/master/dd/d1a/group__imgproc__feature.html#ga04723e007ed888ddf11d9ba04e2232de):
```python
edges = cv2.Canny(image=blur_gray, threshold1=50, threshold1=150, apertureSize=3)
```
![Canny_hough](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/s_canny.png)


And then, a few behind-the-scenes rhos and thetas later, we have our [Hough Line](https://docs.opencv.org/master/dd/d1a/group__imgproc__feature.html#ga8618180a5948286384e3b7ca02f6feeb) results.

```python
lines = cv2.HoughLinesP(image=edges, rho=1, theta=np.pi/180, threshold=80, minLineLength=15, maxLineGap=5)
for x1,y1,x2,y2 in lines[0]:
    cv2.line(img,(x1,y1),(x2,y2),(0,255,0),2)
```
![Hough_transform](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/s_line.png)




Well that wasn't quite what I expected.

I experimented a bit with the hough line, but toggling the parameters kept getting me the same one line.

A bit of digging and I found a [promising post on StackOverflow](https://stackoverflow.com/questions/45322630/how-to-detect-lines-in-opencv)

After following the directions of the top answer, I got this:

![SO_transform](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/stack_overflow_lines.png)


Which gave me more lines, but I still had to figure out which lines were part of the parking space and which weren't. Then, I would also need to detect when a car moved from a spot.

I was running into a challenge; with this approach, I needed an empty parking lot to overlay with an image of a non-empty lot. Which would also call for a mask to cover unimportant information (trees, light posts, etc.)

Given my scope for the weekend, it was time to find another approach.

### Drawing Rectangles

If my program wasn't able to detect parking spots on it's own, maybe it was reasonable to expect that the user give positions for each of the parking spots.

Now, the goal was to find a way to click on the parking lot image and to store the 4 points that made up a parking space for all of the spaces in the lot.

I discovered that I could do this using a [mouse as a "paintbrush"](https://docs.opencv.org/3.0-beta/doc/py_tutorials/py_gui/py_mouse_handling/py_mouse_handling.html)

After some calculations for the center of the rectangle (to label each space), I got this:

![Drawn Rectangles](https://s3-us-west-2.amazonaws.com/parkinglot-opencv/draw_rectangles.png)

### Finishing touches

After drawing the rectangles, all there was left to do was examine the area of each rectangle to see if there was a car in there or not.

By taking each (filtered and blurred) rectangle, determining the area, and doing an average on the pixels, I was able to tell when there wasn't a car in the spot if the average was high (more dark pixels). I changed the color of the bounding box accordingly and viola, a parking detection program!

The code for drawing the rectangles and motion detection is pretty generic. It's seperated out into classes and should be reusable outside of the context of a parking lot. I have tested this with two different parking lot videos and it worked pretty well. I plan to make other improvements and try to seperate OpenCV references to make code easier to test. I'm open to ideas and feedback.

Check out [the code](https://github.com/olgarose/ParkingLot) for more!

## AWS IoT Core (MQTT + Device Shadow)

The detector can optionally publish live occupancy to **AWS IoT Core** over MQTT (TLS 8883, X.509 mTLS) and keep a **named Device Shadow** in sync. When the `--iot-*` flags are omitted, the program behaves exactly as before.

Install dependencies (including the AWS IoT SDK) from the repository root:

```bash
uv sync
```

### AWS setup (one-time)

1. **Create a Thing** in the AWS IoT Core console (or CLI), e.g. `parking_lot_camera_01`. The Thing name must match `--iot-client-id`.

2. **Create and download certificates** for the Thing:
   - `device.pem.crt` (device certificate)
   - `private.pem.key` (private key)
   - `AmazonRootCA1.pem` ([Amazon Root CA](https://www.amazontrust.com/repository/AmazonRootCA1.pem))

   Store them in a local `certs/` directory (this folder is gitignored).

3. **Attach an IoT policy** to the certificate. Example (replace account ID, region, and client ID):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iot:Connect",
      "Resource": "arn:aws:iot:eu-central-1:123456789012:client/parking_lot_camera_01"
    },
    {
      "Effect": "Allow",
      "Action": "iot:Publish",
      "Resource": [
        "arn:aws:iot:eu-central-1:123456789012:topic/parkinglot/*/status",
        "arn:aws:iot:eu-central-1:123456789012:topic/parkinglot/*/summary"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "iot:GetThingShadow",
        "iot:UpdateThingShadow"
      ],
      "Resource": "arn:aws:iot:eu-central-1:123456789012:thing/parking_lot_camera_01"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iot:Publish",
        "iot:Subscribe",
        "iot:Receive"
      ],
      "Resource": [
        "arn:aws:iot:eu-central-1:123456789012:topic/$aws/things/parking_lot_camera_01/shadow/name/occupancy/*",
        "arn:aws:iot:eu-central-1:123456789012:topicfilter/$aws/things/parking_lot_camera_01/shadow/name/occupancy/*"
      ]
    }
  ]
}
```

4. **Note your data endpoint**:

```bash
aws iot describe-endpoint --endpoint-type iot:Data-ATS
```

### CLI flags

| Flag | Description |
|------|-------------|
| `--iot-endpoint` | AWS IoT data endpoint (enables integration when set) |
| `--iot-client-id` | MQTT client ID / Thing name (required with endpoint) |
| `--iot-cert` | Path to device certificate PEM |
| `--iot-key` | Path to device private key PEM |
| `--iot-ca` | Path to Amazon Root CA PEM |
| `--iot-lot-id` | Lot identifier in MQTT topics (default: `lot_1`) |
| `--iot-shadow-name` | Named shadow (default: `occupancy`) |
| `--iot-summary-interval` | Summary heartbeat interval in seconds (default: 30) |

### Example: webcam + AWS IoT

```bash
cd parking_lot
uv run python main.py \
  --video 0 \
  --data data/coordinates_webcam.yml \
  --iot-endpoint a1b2c3d4e5f6-ats.iot.eu-central-1.amazonaws.com \
  --iot-client-id parking_lot_camera_01 \
  --iot-cert ../certs/device.pem.crt \
  --iot-key ../certs/private.pem.key \
  --iot-ca ../certs/AmazonRootCA1.pem \
  --iot-lot-id lot_1
```

### MQTT topics

- `parkinglot/<lot_id>/status` (QoS 1) — one message per confirmed spot state change
- `parkinglot/<lot_id>/summary` (QoS 1) — periodic `{free, occupied, total}` heartbeat

Example `status` payload:

```json
{
  "lot_id": "lot_1",
  "spot_id": 2,
  "occupied": true,
  "ts": "2026-05-17T19:34:21.123456Z",
  "epoch": 1747517661123456,
  "device_id": "parking_lot_camera_01",
  "source": "device"
}
```

`device_id` identifies the publisher (camera Thing name, `web_control` for manual overrides, or `truth_capture` for ground-truth labels). `source` distinguishes how the event was produced: `"device"` (detector/simulator), `"web"` (dashboard `POST /control`), or `"truth"` (ground-truth capture at `/truth`). The IoT topic rule persists all fields to DynamoDB via `SELECT *`.

### Device Shadow document

Named shadow `occupancy` (configurable via `--iot-shadow-name`):

```json
{
  "state": {
    "reported": {
      "lot_id": "lot_1",
      "device_id": "parking_lot_camera_01",
      "spots": {
        "0": {"occupied": true, "ts": "2026-05-17T19:34:21Z", "source": "device"},
        "1": {"occupied": false, "ts": "2026-05-17T19:34:21Z", "source": "device"}
      },
      "summary": {"free": 3, "occupied": 5, "total": 8},
      "ts": "2026-05-17T19:34:21Z"
    }
  }
}
```

The shadow is updated on startup (full snapshot after the first detection pass), on each confirmed state change (delta for that spot + summary), and on each summary heartbeat.

Use the **MQTT test client** in the AWS IoT console to subscribe to `parkinglot/#` and verify messages while the detector runs.

### Cloud-side provisioning with CDK

Instead of clicking through the AWS console, you can stand up the Thing,
certificate, IoT policy, DynamoDB event sink, and topic rule with the CDK app
in [`infra/`](infra/). See [`infra/README.md`](infra/README.md) for deploy steps,
`fetch_certs.py` usage, `build_simulator_cmd.py`, and teardown.

### Simulating without a camera

To validate your AWS IoT wiring (topics, payloads, Device Shadow, IoT Rules) without a webcam, video file, or working image recognition, use the standalone simulator. It drives the same `IoTPublisher` as the real detector, so cloud-side rules and dashboards see **identical** MQTT and shadow traffic.

After CDK deploy and `fetch_certs.py`, you can print a ready-to-run command (stack endpoint, Thing name, and cert paths filled in) with:

```bash
cd infra
uv sync --all-groups
uv run python scripts/fetch_certs.py --stack ParkingLotStack --output ../certs
uv run python scripts/build_simulator_cmd.py --stack ParkingLotStack --certs ../certs
```

The script reads CloudFormation outputs, checks that the PEM files exist, introspects `simulator.py` for flag names, and prints a multiline `uv run python simulator.py ...` line to run from `parking_lot/`. Use `--list-required` to see which flags are mandatory for IoT (`--iot-endpoint` plus cert paths and `--iot-client-id`). Override simulator behaviour with the same flags as the real CLI (for example `--spots 12 --max-events 20`); pass `--one-line` for a single-line command.

Manual run (replace placeholders with your endpoint, Thing name, and cert paths):

```bash
cd parking_lot
uv run python simulator.py \
  --spots 12 \
  --interval 3 \
  --flip-prob 0.25 \
  --max-events 20 \
  --iot-endpoint a1b2c3d4e5f6-ats.iot.eu-central-1.amazonaws.com \
  --iot-client-id parking_lot_camera_01 \
  --iot-cert ../certs/device.pem.crt \
  --iot-key ../certs/private.pem.key \
  --iot-ca ../certs/AmazonRootCA1.pem \
  --iot-lot-id lot_1
```

| Flag | Description |
|------|-------------|
| `--spots` | Number of simulated spots (default: 8) |
| `--interval` | Seconds between ticks in random mode (default: 5) |
| `--flip-prob` | Random mode: probability a spot toggles each tick (default: 0.2) |
| `--initial-occupancy-prob` | Fraction of spots that start occupied (default: 0) |
| `--script` | YAML file of timed events (see below); `--flip-prob` is ignored |
| `--seed` | RNG seed for reproducible random runs |
| `--max-events` | Stop after N spot state changes (useful for smoke tests) |

**Scripted replay** (`--script`): each event has `t` (seconds from start), `spot` (index), and `occupied` (boolean):

```yaml
- {t: 0,  spot: 0, occupied: true}
- {t: 5,  spot: 0, occupied: false}
- {t: 12, spot: 3, occupied: true}
```

```bash
uv run python simulator.py --script data/sim_events.yml \
  --spots 8 --iot-endpoint ... --iot-client-id ... \
  --iot-cert ... --iot-key ... --iot-ca ...
```

Subscribe to `parkinglot/#` in the IoT console MQTT test client while the simulator runs to confirm messages arrive.

### Local DynamoDB sink (no IoT)

To test the same event records the CDK IoT Topic Rule writes to `ParkingLotEvents`, without AWS IoT Core or device certificates, use `--sink dynamodb` and point at [DynamoDB Local](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBLocal.html):

```bash
docker run -d --name dynamodb-local -p 8000:8000 amazon/dynamodb-local
```

Create the table once (schema matches the CDK stack: `lot_id` + `ts` keys):

```bash
export AWS_ACCESS_KEY_ID=local
export AWS_SECRET_ACCESS_KEY=local
aws dynamodb create-table \
  --table-name ParkingLotEvents \
  --attribute-definitions \
    AttributeName=lot_id,AttributeType=S \
    AttributeName=ts,AttributeType=S \
  --key-schema \
    AttributeName=lot_id,KeyType=HASH \
    AttributeName=ts,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region eu-central-1 \
  --endpoint-url http://localhost:8000
```

Run the simulator:

```bash
cd parking_lot
uv run python simulator.py \
  --sink dynamodb \
  --dynamodb-endpoint http://localhost:8000 \
  --spots 12 \
  --interval 3 \
  --flip-prob 0.25 \
  --max-events 20
```

Verify items:

```bash
aws dynamodb scan \
  --table-name ParkingLotEvents \
  --endpoint-url http://localhost:8000 \
  --region eu-central-1
```

Each row matches the MQTT `status` payload (`lot_id`, `ts`, `epoch`, `spot_id`, `occupied`, `device_id`, `source`). `ts` is the ISO-8601 UTC sort key (with fractional seconds); `epoch` is the same instant as microseconds since Unix epoch. The initial occupancy snapshot and periodic summary heartbeats are **not** written to DynamoDB in this mode, matching what the cloud IoT rule persists today (only `parkinglot/+/status` events).

| Flag | Description |
|------|-------------|
| `--sink` | `iot` (default) or `dynamodb` |
| `--dynamodb-endpoint` | DynamoDB API URL (required for `dynamodb` sink) |
| `--dynamodb-table` | Table name (default: `ParkingLotEvents`) |
| `--dynamodb-region` | boto3 region (default: `eu-central-1`) |
| `--dynamodb-lot-id` | `lot_id` field in items (default: `lot_1`) |
| `--dynamodb-device-id` | `device_id` field in items (default: `parking_lot_camera_01`) |

## Web dashboard

A React SPA in [`web/`](web/) shows live parking occupancy: summary tiles, a clickable per-spot grid (green = free, blue = occupied, matching the OpenCV overlay), and a full-width occupancy-percentage chart over the last 15 minutes with separate **device** (solid) and **manual** (dashed) series. A theme toggle switches between light, dark, and system color schemes.

Spots last updated via the dashboard show a **manual** badge and dashed outline (`source: "web"`). Device-driven spots have no badge (`source: "device"`). Older shadow or DynamoDB rows without `source` are treated as device events in the UI.

The SPA is served from a single CloudFront distribution and uses client-side routing:

- `/` — the live dashboard described above.
- `/?debug=1` — adds an **MQTT debug console**: a live, filterable message log (status / summary / control / system) with pause and manual disconnect/reconnect controls for demonstrating reconnection behavior.
- `/truth` — **ground-truth capture**: the same grid, but clicks are stored with `source: "truth"` (`device_id: truth_capture`) for labelling. Truth events are written to DynamoDB but do **not** update the live Device Shadow or summary, and the live dashboard filters them out.

**Data flow:**

1. On load, `GET /snapshot` reads the `occupancy` Device Shadow (same document shape as in the IoT section above, including per-spot `source` when present).
2. MQTT-over-WebSocket (SigV4 + Cognito Identity Pool) subscribes to `parkinglot/<lot_id>/status` and `.../summary` for live updates (read-only from the browser).
3. `GET /history` queries `ParkingLotEvents` in DynamoDB for the initial chart; each incoming status event (MQTT or after `POST /control`) is appended locally so the chart updates in real time.
4. Clicking a spot calls `POST /control` with `{ spot_id, occupied, source }`. A **Control** Lambda publishes to `parkinglot/<lot_id>/status`. For `source: "web"` (`device_id: web_control`) it also republishes the summary and updates the Device Shadow; for `source: "truth"` (`device_id: truth_capture`) it only publishes the status event. The grid and chart update when the MQTT status message arrives (or immediately from the API response); DynamoDB records the event via the existing IoT topic rule.

### HTTP API

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

### Prerequisites

- [Node.js](https://nodejs.org/) 20+ (`npm`)
- Device stack deployed (`ParkingLotStack`) and, for live data, the simulator or detector publishing to IoT

### Deploy

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

### Local development

See [`web/README.md`](web/README.md). After the first deploy, copy stack outputs into `web/public/config.json` (see `web/public/config.example.json`) and run `npm run dev` in `web/`.

## Future work
- Hook up a webcam to a Raspberry Pi and have live parking monitoring at home! (Live webcam input is now supported via `--video <device_index>` — see the Overview section.)
- [Transform parking lot video to have overview perspective](http://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_imgproc/py_geometric_transformations/py_geometric_transformations.html) (for clearer rectangles)
- Experiment with [HOG descriptors](https://gurus.pyimagesearch.com/lesson-sample-histogram-of-oriented-gradients-and-car-logo-recognition/) to detect people or other objects of interest


