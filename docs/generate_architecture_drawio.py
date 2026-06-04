#!/usr/bin/env python3
"""Generate docs/architecture.drawio from docs/architecture.py structure."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

OUTPUT = Path(__file__).resolve().parent / "architecture.drawio"

FILL_COMPUTE = "#ED7100"
FILL_DATABASE = "#C925D1"
FILL_IOT = "#7AA116"
FILL_NETWORK = "#8C4FFF"
FILL_SECURITY = "#DD344C"
FILL_STORAGE = "#7AA116"
FILL_GENERIC = "#232F3E"

AWS_ICON = (
    "sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor={fill};strokeColor=#ffffff;"
    "dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
    "fontSize=11;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
    "resIcon=mxgraph.aws4.{icon};"
)

GROUP_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#666666;"
    "dashed=1;dashPattern=8 8;verticalAlign=top;align=left;spacingLeft=10;"
    "spacingTop=28;fontStyle=1;fontSize=13;fontColor=#333333;container=1;"
    "collapsible=0;recursiveResize=0;"
)

GENERIC_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#F0F0F0;strokeColor=#666666;"
    "verticalAlign=middle;align=center;fontSize=11;fontColor=#333333;"
)

ICON_SIZE = 72


class Builder:
    def __init__(self) -> None:
        self._id = 2
        self.cells: list[dict] = []
        # parent id -> (abs_x, abs_y) of parent's top-left on canvas
        self._origin: dict[str, tuple[float, float]] = {"1": (0.0, 0.0)}

    def nid(self) -> str:
        i = str(self._id)
        self._id += 1
        return i

    def _abs(self, parent: str, x: float, y: float) -> tuple[float, float, float, float]:
        px, py = self._origin[parent]
        return px + x, py + y, x, y

    def add(
        self,
        *,
        value: str = "",
        style: str,
        x: float,
        y: float,
        w: float,
        h: float,
        parent: str = "1",
        edge: bool = False,
        source: str | None = None,
        target: str | None = None,
    ) -> str:
        cid = self.nid()
        ax, ay, rx, ry = self._abs(parent, x, y)
        cell: dict = {
            "id": cid,
            "value": value,
            "style": style,
            "parent": parent,
            "vertex": "0" if edge else "1",
            "edge": "1" if edge else "0",
            "x": rx,
            "y": ry,
            "w": w,
            "h": h,
        }
        if edge:
            cell["source"] = source
            cell["target"] = target
        else:
            self._origin[cid] = (ax, ay)
        self.cells.append(cell)
        return cid

    def group(self, label: str, x: float, y: float, w: float, h: float, parent: str = "1") -> str:
        return self.add(value=label, style=GROUP_STYLE, x=x, y=y, w=w, h=h, parent=parent)

    def aws(
        self, label: str, icon: str, x: float, y: float, parent: str, fill: str = FILL_COMPUTE
    ) -> str:
        return self.add(
            value=label,
            style=AWS_ICON.format(fill=fill, icon=icon),
            x=x,
            y=y,
            w=ICON_SIZE,
            h=ICON_SIZE,
            parent=parent,
        )

    def generic(
        self, label: str, x: float, y: float, parent: str, w: float = 120, h: float = 56
    ) -> str:
        return self.add(value=label, style=GENERIC_STYLE, x=x, y=y, w=w, h=h, parent=parent)

    def connect(
        self,
        source: str,
        target: str,
        label: str = "",
        *,
        color: str = "#555555",
        width: int = 1,
        dashed: bool = False,
        dotted: bool = False,
    ) -> str:
        dash = "1" if dashed or dotted else "0"
        pattern = "dashPattern=1 4;" if dotted else ""
        style = (
            f"edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
            f"html=1;strokeColor={color};strokeWidth={width};fontColor={color};"
            f"fontSize=10;labelBackgroundColor=#ffffff;endArrow=classic;startArrow=none;"
            f"dashed={dash};{pattern}"
        )
        return self.add(
            value=label,
            style=style,
            x=0,
            y=0,
            w=0,
            h=0,
            parent="1",
            edge=True,
            source=source,
            target=target,
        )


def build_graph() -> Builder:
    b = Builder()

    edge = b.group("Edge Device", 40, 40, 300, 300)
    browser_c = b.group("Browser (React SPA)", 40, 380, 220, 140)
    aws = b.group("AWS Cloud", 380, 40, 1680, 920)

    iot_c = b.group("AWS IoT Core  (ParkingLotStack)", 600, 480, 420, 300, parent=aws)
    cert_c = b.group("Certificate Provisioning  (ParkingLotStack)", 1040, 480, 300, 300, parent=aws)
    data_c = b.group("Data  (ParkingLotStack)", 240, 680, 300, 180, parent=aws)
    api_c = b.group("API  (ParkingLotWebStack)", 240, 40, 720, 300, parent=aws)
    host_c = b.group("Static Hosting  (ParkingLotWebStack)", 20, 480, 280, 220, parent=aws)
    dns_c = b.group("DNS & TLS  (ParkingLotDnsStack · us-east-1)", 20, 40, 300, 200, parent=aws)

    camera = b.aws("Webcam / Video", "camera", 30, 60, edge, FILL_IOT)
    detector = b.generic("Motion Detector\n(OpenCV · Laplacian)", 30, 160, edge)
    sim = b.aws("Simulator\n(synthetic events)", "iot_core", 160, 160, edge, FILL_IOT)

    viewer = b.aws("Viewer", "user", 60, 50, browser_c, FILL_GENERIC)

    broker = b.aws("MQTT Broker\n(port 8883 · mTLS)", "iot_core", 40, 120, iot_c, FILL_IOT)
    shadow = b.aws("Device Shadow\n(named: occupancy)", "shadow", 200, 200, iot_c, FILL_IOT)
    rule = b.aws("Topic Rule\nparkinglot/+/status", "iot_events", 40, 200, iot_c, FILL_IOT)

    cert_fn = b.aws("Cert Provisioner\n(Custom Resource)", "lambda", 40, 120, cert_c, FILL_COMPUTE)
    sm = b.aws("Secrets Manager\n(device cert + key)", "secrets_manager", 160, 200, cert_c, FILL_SECURITY)

    ddb = b.aws("ParkingLotEvents\nPK: lot_id  SK: ts", "dynamodb", 100, 80, data_c, FILL_DATABASE)

    cognito = b.aws(
        "Cognito Identity Pool\n(unauthenticated)", "cognito", 40, 80, api_c, FILL_SECURITY
    )
    apigw = b.aws("HTTP API\n(API Gateway v2)", "api_gateway", 280, 80, api_c, FILL_NETWORK)
    snap_fn = b.aws("GetSnapshot\n(shadow + DDB fallback)", "lambda", 460, 180, api_c, FILL_COMPUTE)
    hist_fn = b.aws("GetHistory\n(DDB time-range query)", "lambda", 280, 180, api_c, FILL_COMPUTE)
    ctrl_fn = b.aws(
        "Control\n(source: web | truth\nMQTT + summary + shadow)",
        "lambda",
        640,
        180,
        api_c,
        FILL_COMPUTE,
    )

    cf = b.aws("CloudFront CDN", "cloudfront", 40, 100, host_c, FILL_NETWORK)
    s3 = b.aws("S3 Bucket\n(React SPA)", "s3", 160, 100, host_c, FILL_STORAGE)

    zone = b.aws("Public Hosted Zone\n(web_domain_name)", "route_53", 40, 70, dns_c, FILL_NETWORK)
    acm = b.aws("ACM Certificate\n(DNS validated)", "certificate_manager", 160, 70, dns_c, FILL_SECURITY)

    b.connect(camera, detector)
    b.connect(detector, broker, "MQTT\nsource: device", color="#1a9c3e", width=3)
    b.connect(sim, broker, "MQTT\nsource: device", color="#1a9c3e", width=3)
    b.connect(broker, rule, "DynamoDBv2 action", color="#6c4a9e", width=2)
    b.connect(broker, shadow, "shadow update", color="#1a9c3e", dashed=True)
    b.connect(rule, ddb)
    b.connect(cert_fn, sm, "store cert", color="#d13212", dotted=True)
    b.connect(cert_fn, broker, "CreateKeysAndCertificate", color="#d13212", dotted=True)
    b.connect(apigw, snap_fn)
    b.connect(apigw, hist_fn)
    b.connect(apigw, ctrl_fn)
    b.connect(snap_fn, shadow, "GetThingShadow", color="#1a9c3e", dashed=True)
    b.connect(snap_fn, ddb, "fallback Query", dotted=True)
    b.connect(hist_fn, ddb, "Query")
    b.connect(ctrl_fn, broker, "Publish status\nsource: web | truth", color="#1a9c3e", width=3)
    b.connect(ctrl_fn, broker, "republish summary\n(web only)", color="#1a9c3e", width=2)
    b.connect(ctrl_fn, shadow, "shadow update\n(web only)", color="#1a9c3e", dashed=True)
    b.connect(cf, s3)
    b.connect(acm, cf, "viewer cert", color="#d13212", dotted=True)
    b.connect(zone, cf, "alias A record")
    b.connect(zone, acm, "validation", dotted=True)
    b.connect(viewer, cf, "HTTPS", color="#0078d4", width=2)
    b.connect(viewer, cognito, "temp credentials", color="#c47700", dotted=True)
    b.connect(viewer, broker, "MQTT / WSS\n(SigV4)", color="#1a9c3e", dashed=True)
    b.connect(viewer, apigw, "HTTPS", color="#0078d4", width=2)
    b.connect(viewer, apigw, "POST /control", color="#0078d4", width=3)

    return b


def build_xml() -> str:
    b = build_graph()

    root_el = ET.Element(
        "mxfile",
        {
            "host": "app.diagrams.net",
            "modified": "2026-06-04T00:00:00.000Z",
            "agent": "generate_architecture_drawio.py",
            "version": "24.7.0",
            "type": "device",
            "compressed": "false",
        },
    )
    diagram = ET.SubElement(
        root_el,
        "diagram",
        {
            "id": "parking-lot-arch",
            "name": "Parking Lot Detector - Architecture",
        },
    )
    model = ET.SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "1422",
            "dy": "794",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": "2200",
            "pageHeight": "1100",
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    for cell in b.cells:
        attrs: dict[str, str] = {
            "id": cell["id"],
            "parent": cell["parent"],
            "style": cell["style"],
            "vertex": cell["vertex"],
            "edge": cell["edge"],
        }
        if cell.get("value"):
            attrs["value"] = cell["value"]
        if cell.get("source"):
            attrs["source"] = cell["source"]
            attrs["target"] = cell["target"]
        mx = ET.SubElement(root, "mxCell", attrs)
        if cell["edge"] == "1":
            ET.SubElement(mx, "mxGeometry", {"relative": "1", "as": "geometry"})
        else:
            ET.SubElement(
                mx,
                "mxGeometry",
                {
                    "x": str(cell["x"]),
                    "y": str(cell["y"]),
                    "width": str(cell["w"]),
                    "height": str(cell["h"]),
                    "as": "geometry",
                },
            )

    rough = ET.tostring(root_el, encoding="unicode")
    parsed = minidom.parseString(rough)
    pretty = parsed.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")
    # minidom adds an extra xml declaration line; keep a single header
    pretty = re.sub(r"<\?xml[^?]+\?>\s*", "", pretty, count=1)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + pretty.strip() + "\n"


def main() -> None:
    OUTPUT.write_text(build_xml(), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
