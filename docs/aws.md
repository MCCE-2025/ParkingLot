# AWS IoT Core (MQTT + Device Shadow)

The detector can optionally publish live occupancy to **AWS IoT Core** over MQTT (TLS 8883, X.509 mTLS) and keep a **named Device Shadow** in sync. When the `--iot-*` flags are omitted, the program behaves exactly as before.

Install dependencies (including the AWS IoT SDK) from the repository root:

```bash
uv sync
```

## AWS setup (one-time)

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

## CLI flags

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

## Example: webcam + AWS IoT

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

## MQTT topics

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

## Device Shadow document

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

## Cloud-side provisioning with CDK

Instead of clicking through the AWS console, you can stand up the Thing,
certificate, IoT policy, DynamoDB event sink, and topic rule with the CDK app
in [`infra/`](../infra/). See [`infra/README.md`](../infra/README.md) for deploy steps,
`fetch_certs.py` usage, `build_simulator_cmd.py`, and teardown.

## Simulating without a camera

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

## Local DynamoDB sink (no IoT)

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
