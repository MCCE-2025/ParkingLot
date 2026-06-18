import argparse
import logging
import os
import time

# Silence OpenCV's native (C++) log spam such as the harmless
# "GStreamer: pipeline have not been created" warning that fires on the
# first frame from some webcams on Linux. The env var must be set
# *before* ``cv2`` is imported so the C++ side picks it up; the runtime
# call below covers the case where ``cv2`` was already imported earlier.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2 as open_cv  # noqa: E402  (import after env var on purpose)
import numpy as np  # noqa: E402
import yaml  # noqa: E402

try:
    open_cv.utils.logging.setLogLevel(open_cv.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    # Older OpenCV builds don't expose cv2.utils.logging; the env var
    # above is enough on those.
    pass
from colors import *
from coordinates_generator import CoordinatesGenerator
from iot_publisher import add_iot_args, build_iot_publisher
from motion_detector import MotionDetector
from webcam_controls import (
    CONTROLS,
    AutoBrightnessController,
    apply_controls,
    has_any,
    open_webcam,
    release_webcam,
)

# Single OpenCV window title shared between the spot-marking phase and the
# live detection phase, so the window stays put across the transition.
WINDOW_NAME = "Parking Lot"


def main():
    logging.basicConfig(level=logging.INFO)

    args = parse_args()

    data_file = args.data_file
    start_frame = args.start_frame
    snapshot_path = args.snapshot
    remark = args.remark

    # Allow --video to be either a file path or a webcam device index (e.g. 0).
    video_source = _coerce_source(args.video_file)
    is_webcam = isinstance(video_source, int)

    # Collect hardware webcam controls from the CLI (e.g. --brightness 192).
    cam_controls = {name: getattr(args, name) for name, _, _ in CONTROLS}
    if has_any(cam_controls) and not isinstance(video_source, int):
        logging.warning(
            "Webcam controls (--brightness etc.) only apply to webcams; "
            "ignoring them for video file input."
        )
        cam_controls = {}

    # Build the auto-brightness controller if requested.
    auto_brightness = None
    if args.auto_brightness:
        if isinstance(video_source, int):
            auto_brightness = AutoBrightnessController(
                target=args.auto_brightness_target,
                prop=args.auto_brightness_prop,
            )
        else:
            logging.warning(
                "--auto-brightness only applies to webcams; ignoring for "
                "video file input."
            )

    # Decide whether to run the spot-marking step. Marking happens when
    # the data file has no spots yet, or when --remark is forced. The
    # marking frame is always grabbed live from the webcam.
    data_missing = _is_data_file_empty(data_file)
    should_mark = data_missing or remark

    # When marking from a webcam, keep the capture open through the marking
    # phase so detection can reuse the same device handle instead of closing
    # and reopening it (which triggers VIDIOC_QBUF errors on some V4L2
    # drivers).
    shared_webcam_capture = None

    if should_mark:
        action = "re-mark" if (remark and not data_missing) else "mark"
        if is_webcam:
            logging.info(
                "%s: capturing a frame from webcam %d to %s spots.",
                data_file,
                video_source,
                action,
            )
            shared_webcam_capture = open_webcam(video_source)
            if not shared_webcam_capture.isOpened():
                raise RuntimeError(
                    "Could not open webcam device %d for snapshot." % video_source
                )
            apply_controls(shared_webcam_capture, cam_controls)
            frame = _read_usable_webcam_frame(shared_webcam_capture)
        else:
            logging.info(
                "%s: extracting a still from video file %s (frame %s) to %s spots.",
                data_file,
                video_source,
                start_frame,
                action,
            )
            frame = _capture_frame_from_video(video_source, int(start_frame))

        if snapshot_path:
            open_cv.imwrite(snapshot_path, frame)
            logging.info("Saved marking snapshot to %s", snapshot_path)

        with open(data_file, "w+") as points:
            generator = CoordinatesGenerator(
                frame,
                points,
                COLOR_RED,
                window_name=WINDOW_NAME,
            )
            # Keep the marking window open so the detection phase below
            # can render into the same window instead of spawning a new
            # one (which would otherwise look like a close+reopen flash).
            generator.generate(keep_window_open=True)

    publisher = build_iot_publisher(args)

    with open(data_file, "r") as data:
        points = yaml.safe_load(data)
        detector = MotionDetector(
            video_source,
            points,
            int(start_frame),
            cam_controls=cam_controls,
            auto_brightness=auto_brightness,
            window_name=WINDOW_NAME,
            laplacian=args.laplacian,
            detect_delay=args.detect_delay,
            publisher=publisher,
            show_laplacian=args.show_laplacian,
            capture=shared_webcam_capture,
        )
        try:
            detector.detect_motion()
        finally:
            if shared_webcam_capture is not None:
                release_webcam(shared_webcam_capture)
                shared_webcam_capture = None
            if publisher is not None:
                publisher.disconnect()


def _coerce_source(value):
    """Convert a string CLI value to an int if it looks like a device index."""
    if value is None:
        return None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def _capture_frame_from_video(path, start_frame=1):
    """Grab a single still frame from a video file for spot marking.

    Seeks to ``start_frame`` (1-based, matching ``--start-frame``) so the
    user marks spots on the same frame the detection loop will start at,
    which keeps the layout aligned with what they actually see during
    playback. Falls back to the first decodable frame if seeking fails.
    """
    capture = open_cv.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError("Could not open video file %r for marking." % path)

    # ``CAP_PROP_POS_FRAMES`` is 0-based; the CLI's --start-frame is 1-based
    # by historical convention, so subtract one (clamped to 0).
    target = max(0, int(start_frame) - 1)
    if target > 0:
        capture.set(open_cv.CAP_PROP_POS_FRAMES, float(target))

    ok, frame = capture.read()
    if not ok or frame is None:
        # Seeking can fail on some containers/codecs; rewind and try again
        # from the very beginning so we still get *something* to mark on.
        capture.set(open_cv.CAP_PROP_POS_FRAMES, 0.0)
        ok, frame = capture.read()

    capture.release()

    if not ok or frame is None:
        raise RuntimeError(
            "Could not read any frame from video file %r for marking." % path
        )
    return frame


def _is_data_file_empty(data_file):
    """Return True if the spots data file is missing or has no spots.

    The file is considered "empty" if any of the following hold:

    * it doesn't exist on disk,
    * it has zero bytes,
    * its YAML payload parses to ``None`` (e.g. blank file or only
      comments),
    * its YAML payload parses to an empty sequence (``[]``) or empty
      mapping (``{}``),
    * it fails to parse as YAML at all (treated as empty so the user
      can re-mark instead of crashing later when we try to iterate it).
    """
    if not os.path.exists(data_file) or os.path.getsize(data_file) == 0:
        return True
    try:
        with open(data_file, "r") as handle:
            parsed = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        logging.warning(
            "Could not parse %s as YAML (%s); treating it as empty.",
            data_file,
            exc,
        )
        return True
    if parsed is None:
        return True
    # Both list and dict have a meaningful len(); anything else (e.g. a
    # scalar) is unexpected and we'd rather re-mark than misinterpret.
    if hasattr(parsed, "__len__"):
        return len(parsed) == 0
    return True


def _read_usable_webcam_frame(
    capture,
    warmup_frames=30,
    min_mean_luminance=10.0,
    max_attempts=60,
    settle_seconds=0.5,
):
    """Read a single non-black frame from an already-open webcam capture.

    Most webcams return solid-black (or near-black) frames for the first
    handful of reads after being opened, especially when auto-exposure
    has just been (re)engaged or hardware controls were changed. We
    therefore:

    1. Sleep briefly so the driver can settle.
    2. Discard at least ``warmup_frames`` reads.
    3. Keep reading until we get a frame whose mean luminance is above
       ``min_mean_luminance`` (i.e. not effectively black), up to
       ``max_attempts`` total reads.
    """
    if settle_seconds > 0:
        time.sleep(settle_seconds)

    frame = None
    last_frame = None
    for attempt in range(max_attempts):
        ok, current = capture.read()
        if not ok or current is None:
            continue
        last_frame = current
        if attempt < warmup_frames:
            continue
        gray = open_cv.cvtColor(current, open_cv.COLOR_BGR2GRAY)
        mean = float(np.mean(gray))
        if mean >= min_mean_luminance:
            frame = current
            logging.info(
                "Webcam snapshot captured after %d frames (mean luminance %.1f).",
                attempt + 1,
                mean,
            )
            break
        logging.debug(
            "Discarding dark webcam frame %d (mean luminance %.1f < %.1f).",
            attempt + 1,
            mean,
            min_mean_luminance,
        )

    if frame is None:
        if last_frame is not None:
            logging.warning(
                "Webcam only produced dark frames after %d attempts; "
                "using the last one anyway. Try increasing brightness/exposure "
                "or pointing the camera at a better-lit scene.",
                max_attempts,
            )
            return last_frame
        raise RuntimeError("Could not read a frame from the open webcam device.")
    return frame


def parse_args():
    parser = argparse.ArgumentParser(description="Generates Coordinates File")

    parser.add_argument(
        "--video",
        dest="video_file",
        required=True,
        help=(
            "Video source to detect motion on. Either a path to a video "
            "file or a webcam device index (e.g. 0)."
        ),
    )

    parser.add_argument(
        "--data",
        dest="data_file",
        required=True,
        help="Data file to be used with OpenCV",
    )

    parser.add_argument(
        "--start-frame",
        dest="start_frame",
        required=False,
        default=1,
        help="Starting frame on the video (ignored for webcams)",
    )

    parser.add_argument(
        "--snapshot",
        dest="snapshot",
        required=False,
        default=None,
        help=(
            "Optional path to save the webcam snapshot used for marking "
            "spots (e.g. for debugging or as a reference image)."
        ),
    )

    parser.add_argument(
        "--remark",
        dest="remark",
        action="store_true",
        help=(
            "Force the spot-marking step to run even if the coordinates "
            "file already exists. When --video is a webcam, a fresh "
            "snapshot is captured automatically."
        ),
    )

    cam_group = parser.add_argument_group(
        "webcam hardware controls",
        "Set V4L2-style camera properties (equivalent to "
        "`v4l2-ctl --set-ctrl=<name>=<value>` on Linux). Only applied "
        "when --video is a webcam device index. Value ranges depend on "
        "the camera/driver.",
    )
    cam_group.add_argument(
        "--brightness",
        dest="brightness",
        type=float,
        default=None,
        help="Webcam brightness (e.g. 192).",
    )
    cam_group.add_argument(
        "--contrast",
        dest="contrast",
        type=float,
        default=None,
        help="Webcam contrast.",
    )
    cam_group.add_argument(
        "--saturation",
        dest="saturation",
        type=float,
        default=None,
        help="Webcam saturation.",
    )
    cam_group.add_argument(
        "--gain",
        dest="gain",
        type=float,
        default=None,
        help="Webcam gain.",
    )
    cam_group.add_argument(
        "--exposure",
        dest="exposure",
        type=float,
        default=None,
        help=(
            "Webcam exposure (manual). Many V4L2 drivers require "
            "--auto-exposure 1 first to take effect."
        ),
    )
    cam_group.add_argument(
        "--auto-exposure",
        dest="auto_exposure",
        type=float,
        default=None,
        help=(
            "Auto-exposure mode. On most V4L2 UVC cameras, 3 = auto and "
            "1 = manual; check `v4l2-ctl -L` for your specific device."
        ),
    )
    cam_group.add_argument(
        "--auto-brightness",
        dest="auto_brightness",
        action="store_true",
        help=(
            "Continuously adjust a hardware control (exposure, gain, or "
            "brightness) to keep the mean frame luminance near "
            "--auto-brightness-target. Pair with --auto-exposure 1 if "
            "you want to drive --exposure manually."
        ),
    )
    cam_group.add_argument(
        "--auto-brightness-target",
        dest="auto_brightness_target",
        type=float,
        default=128.0,
        help=(
            "Target mean luminance (0–255) for --auto-brightness. "
            "Default: 128 (neutral midtone)."
        ),
    )
    cam_group.add_argument(
        "--auto-brightness-prop",
        dest="auto_brightness_prop",
        choices=["exposure", "gain", "brightness"],
        default=None,
        help=(
            "Which hardware control --auto-brightness should drive. "
            "Default: auto-detect the first one the camera accepts."
        ),
    )

    detection_group = parser.add_argument_group(
        "detection tuning",
        "Fine-tune the occupancy detection algorithm.",
    )
    detection_group.add_argument(
        "--laplacian",
        dest="laplacian",
        type=float,
        default=None,
        help=(
            "Laplacian threshold for occupancy detection. "
            "Lower values make detection more sensitive (fewer edges needed "
            "to count a spot as occupied). Default: %.1f."
        )
        % MotionDetector.LAPLACIAN,
    )
    detection_group.add_argument(
        "--detect-delay",
        dest="detect_delay",
        type=float,
        default=None,
        help=(
            "Seconds a status change must remain stable before it is "
            "accepted. Higher values reduce flicker from shadows or "
            "passing pedestrians. Default: %.1f."
        )
        % MotionDetector.DETECT_DELAY,
    )
    detection_group.add_argument(
        "--show-laplacian",
        dest="show_laplacian",
        action="store_true",
        help=(
            "Show the Laplacian operator response (the input used for "
            "occupancy detection) as a second video feed stacked below "
            "the main feed."
        ),
    )

    add_iot_args(parser)

    return parser.parse_args()


if __name__ == "__main__":
    main()
