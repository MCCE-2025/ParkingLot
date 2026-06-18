import logging
import time

import cv2 as open_cv
import numpy as np
from colors import COLOR_BLUE, COLOR_GREEN, COLOR_WHITE
from display_window import setup_display_window
from drawing_utils import draw_contours
from webcam_controls import apply_controls, open_webcam, release_webcam


class MotionDetector:
    LAPLACIAN = 1.4
    DETECT_DELAY = 1

    def __init__(
        self,
        video,
        coordinates,
        start_frame,
        cam_controls=None,
        auto_brightness=None,
        window_name=None,
        laplacian=None,
        detect_delay=None,
        publisher=None,
        show_laplacian=False,
        capture=None,
    ):
        self.video = video
        # Optional pre-opened webcam capture (e.g. kept alive across the
        # marking phase) so we don't close and reopen the V4L2 device.
        self._capture = capture
        self.coordinates_data = coordinates
        self.start_frame = start_frame
        self.cam_controls = cam_controls or {}
        self.auto_brightness = auto_brightness
        # When True, a second feed visualising the Laplacian response used
        # for occupancy detection is stacked below the main feed.
        self.show_laplacian = show_laplacian
        # When provided, render into this existing window instead of one
        # named after the video source. Lets callers reuse the marking
        # window so it doesn't close and reopen between phases.
        self.window_name = window_name if window_name is not None else str(video)
        self.laplacian_threshold = (
            laplacian if laplacian is not None else MotionDetector.LAPLACIAN
        )
        self.detect_delay = (
            detect_delay if detect_delay is not None else MotionDetector.DETECT_DELAY
        )
        self.publisher = publisher
        self.contours = []
        self.bounds = []
        self.mask = []

    def detect_motion(self):
        owns_capture = self._capture is None
        if owns_capture:
            if isinstance(self.video, int):
                capture = open_webcam(self.video)
            else:
                capture = open_cv.VideoCapture(self.video)
        else:
            capture = self._capture

        if not capture.isOpened():
            raise CaptureReadError(
                "Could not open video source %r for detection." % (self.video,)
            )
        # When self.video is an int we are reading from a webcam, which has no
        # seekable timeline, so skip frame seeking and CAP_PROP_POS_MSEC.
        is_webcam = isinstance(self.video, int)
        if is_webcam:
            if owns_capture:
                apply_controls(capture, self.cam_controls)
            if self.auto_brightness is not None:
                self.auto_brightness.attach(capture)
            # Some UVC drivers need a few reads before the first usable frame
            # when we opened the device ourselves in this phase.
            if owns_capture:
                self._warmup_capture(capture)
        else:
            capture.set(open_cv.CAP_PROP_POS_FRAMES, self.start_frame)
        start_time = time.time()

        coordinates_data = self.coordinates_data
        logging.debug("coordinates data: %s", coordinates_data)

        for p in coordinates_data:
            coordinates = self._coordinates(p)
            logging.debug("coordinates: %s", coordinates)

            rect = open_cv.boundingRect(coordinates)
            logging.debug("rect: %s", rect)

            new_coordinates = coordinates.copy()
            new_coordinates[:, 0] = coordinates[:, 0] - rect[0]
            new_coordinates[:, 1] = coordinates[:, 1] - rect[1]
            logging.debug("new_coordinates: %s", new_coordinates)

            self.contours.append(coordinates)
            self.bounds.append(rect)

            mask = open_cv.drawContours(
                np.zeros((rect[3], rect[2]), dtype=np.uint8),
                [new_coordinates],
                contourIdx=-1,
                color=255,
                thickness=-1,
                lineType=open_cv.LINE_8,
            )

            mask = mask == 255
            self.mask.append(mask)
            logging.debug("mask: %s", self.mask)

        statuses = [False] * len(coordinates_data)
        times = [None] * len(coordinates_data)
        initial_snapshot_sent = False
        window_initialized = False

        empty_frames = 0
        max_empty_frames = 30  # ~1–2 seconds of dropped reads on a webcam
        while capture.isOpened():
            result, frame = capture.read()
            if not result or frame is None:
                if is_webcam:
                    # Webcams can drop a frame here and there; only bail
                    # if it keeps happening, in which case the device
                    # likely went away.
                    empty_frames += 1
                    if empty_frames >= max_empty_frames:
                        logging.error(
                            "Webcam returned %d empty frames in a row; giving up.",
                            empty_frames,
                        )
                        break
                    open_cv.waitKey(30)
                    continue
                # For a video file, an empty read means EOF.
                break
            empty_frames = 0

            if is_webcam and self.auto_brightness is not None:
                self.auto_brightness.update(capture, frame, time.time())

            blurred = open_cv.GaussianBlur(frame.copy(), (5, 5), 3)
            grayed = open_cv.cvtColor(blurred, open_cv.COLOR_BGR2GRAY)
            new_frame = frame.copy()
            logging.debug("new_frame: %s", new_frame)

            if is_webcam:
                position_in_seconds = time.time() - start_time
            else:
                position_in_seconds = capture.get(open_cv.CAP_PROP_POS_MSEC) / 1000.0

            frame_measured = []
            frame_magnitudes = [0.0] * len(coordinates_data)
            for index, c in enumerate(coordinates_data):
                status, magnitude = self.__apply(grayed, index, c)
                frame_measured.append(status)
                frame_magnitudes[index] = magnitude

                if times[index] is not None and self.same_status(
                    statuses, index, status
                ):
                    times[index] = None
                    continue

                if times[index] is not None and self.status_changed(
                    statuses, index, status
                ):
                    if position_in_seconds - times[index] >= self.detect_delay:
                        statuses[index] = status
                        times[index] = None
                        if self.publisher is not None:
                            self.publisher.publish_spot(
                                spot_id=index,
                                occupied=not status,
                                statuses=statuses,
                            )
                    continue

                if times[index] is None and self.status_changed(
                    statuses, index, status
                ):
                    times[index] = position_in_seconds

            if not initial_snapshot_sent and self.publisher is not None:
                self.publisher.publish_initial_snapshot(frame_measured)
                initial_snapshot_sent = True

            for index, p in enumerate(coordinates_data):
                coordinates = self._coordinates(p)

                color = COLOR_GREEN if statuses[index] else COLOR_BLUE
                draw_contours(
                    new_frame, coordinates, str(p["id"]), COLOR_WHITE, color
                )

            if self.publisher is not None:
                self.publisher.publish_summary_if_due(statuses, time.time())

            display_frame = new_frame
            if self.show_laplacian:
                laplacian_view = self._laplacian_view(grayed, frame_magnitudes)
                display_frame = np.vstack((new_frame, laplacian_view))

            if not window_initialized:
                frame_h, frame_w = display_frame.shape[:2]
                setup_display_window(self.window_name, frame_w, frame_h)
                window_initialized = True

            open_cv.imshow(self.window_name, display_frame)
            k = open_cv.waitKey(1)
            if k == ord("q"):
                break
        if owns_capture:
            release_webcam(capture)
        open_cv.destroyAllWindows()

    def _laplacian_view(self, grayed, magnitudes=None):
        """Build a BGR visualisation of the Laplacian response.

        Mirrors the per-spot detection input by running the same Laplacian
        operator on the full (blurred, grayed) frame, then rescaling the
        absolute response to 0–255 so faint edges stay visible. The spot
        outlines are overlaid so the second feed lines up with the main
        feed above it. When ``magnitudes`` is given, the measured mean
        Laplacian value per spot is printed at its centre, coloured by
        whether it falls below the occupancy threshold (green = empty,
        blue = occupied), matching the colours used in the main feed.
        """
        laplacian = open_cv.Laplacian(grayed, open_cv.CV_64F)
        magnitude = np.absolute(laplacian)
        scaled = open_cv.normalize(
            magnitude, None, 0, 255, open_cv.NORM_MINMAX
        ).astype(np.uint8)
        view = open_cv.cvtColor(scaled, open_cv.COLOR_GRAY2BGR)

        for index, p in enumerate(self.coordinates_data):
            coordinates = self._coordinates(p)
            open_cv.drawContours(
                view,
                [coordinates],
                contourIdx=-1,
                color=COLOR_WHITE,
                thickness=1,
                lineType=open_cv.LINE_8,
            )

            if magnitudes is None or index >= len(magnitudes):
                continue

            value = magnitudes[index]
            is_empty = value < self.laplacian_threshold
            label_color = COLOR_GREEN if is_empty else COLOR_BLUE
            moments = open_cv.moments(coordinates)
            if moments["m00"] == 0:
                continue
            center = (
                int(moments["m10"] / moments["m00"]) - 12,
                int(moments["m01"] / moments["m00"]) + 4,
            )
            open_cv.putText(
                view,
                "%.1f" % value,
                center,
                open_cv.FONT_HERSHEY_SIMPLEX,
                0.4,
                label_color,
                1,
                open_cv.LINE_AA,
            )

        open_cv.putText(
            view,
            "Laplacian (threshold %.1f)" % self.laplacian_threshold,
            (10, 25),
            open_cv.FONT_HERSHEY_SIMPLEX,
            0.7,
            COLOR_WHITE,
            2,
            open_cv.LINE_AA,
        )
        return view

    def __apply(self, grayed, index, p):
        coordinates = self._coordinates(p)
        logging.debug("points: %s", coordinates)

        rect = self.bounds[index]
        logging.debug("rect: %s", rect)

        roi_gray = grayed[rect[1] : (rect[1] + rect[3]), rect[0] : (rect[0] + rect[2])]
        laplacian = open_cv.Laplacian(roi_gray, open_cv.CV_64F)
        logging.debug("laplacian: %s", laplacian)

        coordinates[:, 0] = coordinates[:, 0] - rect[0]
        coordinates[:, 1] = coordinates[:, 1] - rect[1]

        magnitude = float(np.mean(np.abs(laplacian * self.mask[index])))
        status = magnitude < self.laplacian_threshold
        logging.debug("status: %s (magnitude: %.3f)", status, magnitude)

        return status, magnitude

    @staticmethod
    def _coordinates(p):
        return np.array(p["coordinates"])

    @staticmethod
    def same_status(coordinates_status, index, status):
        return status == coordinates_status[index]

    @staticmethod
    def status_changed(coordinates_status, index, status):
        return status != coordinates_status[index]

    @staticmethod
    def _warmup_capture(capture, max_attempts=15, delay_ms=30):
        """Read and discard frames until one comes through, or we give up."""
        for attempt in range(max_attempts):
            ok, frame = capture.read()
            if ok and frame is not None:
                logging.debug(
                    "Webcam warm-up: usable frame after %d attempt(s).",
                    attempt + 1,
                )
                return
            open_cv.waitKey(delay_ms)
        logging.warning(
            "Webcam warm-up: no usable frame after %d attempts; "
            "detection loop will keep trying.",
            max_attempts,
        )


class CaptureReadError(Exception):
    pass
