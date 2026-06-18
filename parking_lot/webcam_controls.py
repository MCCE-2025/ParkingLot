"""Hardware-level webcam controls.

This module is a thin wrapper around ``cv2.VideoCapture.set`` for the
``CAP_PROP_*`` properties that correspond to V4L2 controls (brightness,
contrast, exposure, etc.). Setting a value here is roughly equivalent to
running, on Linux::

    v4l2-ctl --set-ctrl=brightness=192

before opening the camera. On other platforms, OpenCV routes the same
calls through the platform's native camera API (DirectShow on Windows,
AVFoundation on macOS).

Note that not every camera/driver exposes every control. ``set`` returns
``False`` when the device rejects a property; we log a warning in that
case rather than failing the run.
"""

import logging
import sys
import time

import cv2 as open_cv
import numpy as np

# After releasing a V4L2 device, some drivers need a moment before the same
# index can be opened again without ioctl(VIDIOC_QBUF) errors.
WEBCAM_REOPEN_SETTLE_SECONDS = 1.0


def open_webcam(device_index):
    """Open a webcam capture, preferring the V4L2 backend on Linux."""
    if sys.platform.startswith("linux"):
        capture = open_cv.VideoCapture(device_index, open_cv.CAP_V4L2)
        if capture.isOpened():
            return capture
        capture.release()
    return open_cv.VideoCapture(device_index)


def release_webcam(capture, settle=False):
    """Release a webcam device, optionally waiting for the driver to settle."""
    if capture is not None:
        capture.release()
    if settle and WEBCAM_REOPEN_SETTLE_SECONDS > 0:
        time.sleep(WEBCAM_REOPEN_SETTLE_SECONDS)

# (CLI/dict name, OpenCV property, human label).
# The order here is also the order of attempted application.
CONTROLS = [
    ("auto_exposure", open_cv.CAP_PROP_AUTO_EXPOSURE, "Auto-exposure mode"),
    ("brightness", open_cv.CAP_PROP_BRIGHTNESS, "Brightness"),
    ("contrast", open_cv.CAP_PROP_CONTRAST, "Contrast"),
    ("saturation", open_cv.CAP_PROP_SATURATION, "Saturation"),
    ("gain", open_cv.CAP_PROP_GAIN, "Gain"),
    ("exposure", open_cv.CAP_PROP_EXPOSURE, "Exposure"),
]


def apply_controls(capture, values):
    """Apply hardware controls to an already-opened ``cv2.VideoCapture``.

    Parameters
    ----------
    capture : cv2.VideoCapture
        Open capture device. Should be a webcam; passing a video file
        capture is a no-op since file backends ignore these properties.
    values : dict
        Map from control name (see :data:`CONTROLS`) to numeric value.
        Entries whose value is ``None`` are skipped.
    """
    if not values:
        return

    for name, prop, label in CONTROLS:
        value = values.get(name)
        if value is None:
            continue
        ok = capture.set(prop, float(value))
        if ok:
            logging.info("Webcam control: set %s=%s", label, value)
        else:
            logging.warning(
                "Webcam control: device rejected %s=%s "
                "(driver may not expose this control)",
                label,
                value,
            )


def has_any(values):
    """Return True if at least one control value is not None."""
    if not values:
        return False
    return any(values.get(name) is not None for name, _, _ in CONTROLS)


# Default property a camera exposes that we can use as the "exposure knob".
# The auto-adjustment loop tries these in order and uses the first one the
# device accepts a write on.
_AUTO_TARGET_PROPS = [
    ("exposure", open_cv.CAP_PROP_EXPOSURE, "Exposure"),
    ("gain", open_cv.CAP_PROP_GAIN, "Gain"),
    ("brightness", open_cv.CAP_PROP_BRIGHTNESS, "Brightness"),
]


class AutoBrightnessController:
    """Closed-loop adjuster for a webcam's hardware brightness/exposure.

    Computes the mean luminance of each incoming frame and nudges a single
    hardware control up or down to keep that mean near ``target``. This is
    essentially a software auto-exposure that drives V4L2 controls instead
    of doing it in pixels.

    Parameters
    ----------
    target : float
        Desired mean luminance (0–255). 128 is neutral midtone.
    tolerance : float
        Dead-zone around ``target`` where no adjustment happens. Prevents
        oscillation on noisy frames.
    step : float
        Maximum change to the control value per adjustment. Multiplied by
        the normalized error, so the actual step shrinks as we approach
        the target.
    interval : float
        Minimum number of seconds between adjustments. Webcams need a few
        frames after a control change to settle, so adjusting every frame
        causes overshoot.
    prop : str or None
        Which control to drive. One of ``"exposure"``, ``"gain"``,
        ``"brightness"``, or ``None`` to auto-detect on the first frame.
    value_range : tuple of (float, float) or None
        Min/max value the chosen control accepts. Auto-detected if None.
    """

    def __init__(
        self,
        target=128.0,
        tolerance=8.0,
        step=20.0,
        interval=0.5,
        prop=None,
        value_range=None,
    ):
        self.target = float(target)
        self.tolerance = float(tolerance)
        self.step = float(step)
        self.interval = float(interval)
        self.requested_prop = prop
        self.value_range = value_range

        self._prop_name = None
        self._prop_id = None
        self._prop_label = None
        self._current_value = 0.0
        self._last_adjust_time = None
        self._initialized = False

    def attach(self, capture):
        """Pick a writable control on ``capture`` and remember its range."""
        candidates = _AUTO_TARGET_PROPS
        if self.requested_prop is not None:
            candidates = [c for c in _AUTO_TARGET_PROPS if c[0] == self.requested_prop]
            if not candidates:
                logging.warning(
                    "Auto-brightness: requested control %r is not adjustable; "
                    "falling back to defaults.",
                    self.requested_prop,
                )
                candidates = _AUTO_TARGET_PROPS

        for name, prop_id, label in candidates:
            current = capture.get(prop_id)
            # Probe by writing the current value back. Drivers that don't
            # support the property typically return False here.
            if not capture.set(prop_id, current):
                continue
            self._prop_name = name
            self._prop_id = prop_id
            self._prop_label = label
            self._current_value = float(current) if current is not None else 0.0
            self._initialized = True
            logging.info(
                "Auto-brightness: driving %s (current=%s) toward target=%.0f",
                label,
                self._current_value,
                self.target,
            )
            return True

        logging.warning(
            "Auto-brightness: no writable exposure/gain/brightness control "
            "found on this camera; auto-adjustment disabled."
        )
        return False

    def update(self, capture, frame, now):
        """Possibly adjust the control based on the frame's luminance."""
        if not self._initialized or frame is None:
            return
        if (
            self._last_adjust_time is not None
            and now - self._last_adjust_time < self.interval
        ):
            return

        # Mean luminance from the V channel of HSV is a cheap, robust proxy.
        gray = open_cv.cvtColor(frame, open_cv.COLOR_BGR2GRAY)
        mean = float(np.mean(gray))
        error = self.target - mean
        if abs(error) <= self.tolerance:
            self._last_adjust_time = now
            return

        # Proportional step, capped by self.step.
        delta = max(-self.step, min(self.step, error / 128.0 * self.step))
        new_value = self._current_value + delta
        if self.value_range is not None:
            lo, hi = self.value_range
            new_value = max(lo, min(hi, new_value))

        if not capture.set(self._prop_id, new_value):
            logging.warning(
                "Auto-brightness: failed to set %s=%.2f; disabling.",
                self._prop_label,
                new_value,
            )
            self._initialized = False
            return

        logging.debug(
            "Auto-brightness: mean=%.1f target=%.1f -> %s %.2f→%.2f",
            mean,
            self.target,
            self._prop_label,
            self._current_value,
            new_value,
        )
        self._current_value = new_value
        self._last_adjust_time = now
