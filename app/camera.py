from threading import Condition, Event, Lock, Thread
from time import monotonic

import cv2

FRAME_INTERVAL_SECONDS = 0.16


class CameraUnavailable(Exception):
    pass


class Camera:
    """One reader continuously drains the device and publishes its latest frame."""

    def __init__(self):
        self._control = Lock()
        self._changed = Condition()
        self._stop = Event()
        self._thread = None
        self._latest = None
        self._active = False
        self._device_index = None

    @property
    def running(self):
        with self._changed:
            return self._active and self._latest is not None and not self._stop.is_set()

    def start(self, device_index):
        with self._control:
            if self.running and self._device_index == device_index:
                return
            self._stop_reader()
            with self._changed:
                self._stop.clear()
                self._device_index = device_index
                self._active = True
                self._thread = Thread(target=self._read, args=(device_index,), daemon=True)
                self._thread.start()
                self._changed.wait_for(lambda: self._latest is not None or not self._active, 5)
                ready = self._latest is not None
            if not ready:
                self._stop_reader()
                raise CameraUnavailable("Could not get frames. Check camera permissions and device index.")

    def stop(self):
        with self._control:
            self._stop_reader()

    def _stop_reader(self):
        with self._changed:
            self._stop.set()
            self._latest = None
            self._changed.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise CameraUnavailable("The camera driver is still releasing the device.")
            self._thread = None

    def _read(self, device_index):
        capture = None
        try:
            capture = cv2.VideoCapture(device_index)
            if not capture.isOpened() or self._stop.is_set():
                return
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            discarded = 0
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok or frame is None or frame.size == 0:
                    break
                if discarded < 3:
                    discarded += 1
                    continue
                with self._changed:
                    if self._stop.is_set():
                        break
                    self._latest = (monotonic(), frame)
                    self._changed.notify_all()
        finally:
            try:
                if capture is not None:
                    capture.release()
            finally:
                with self._changed:
                    self._latest = None
                    self._active = False
                    self._changed.notify_all()

    def _next(self, after, deadline):
        with self._changed:
            while self._active and not self._stop.is_set():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if self._latest is not None and self._latest[0] > after:
                    return self._latest
                self._changed.wait(remaining)
        raise CameraUnavailable("Start the camera before capturing frames.")

    def latest_frame(self):
        with self._changed:
            if self._latest is None or self._stop.is_set() or monotonic() - self._latest[0] > 0.5:
                return None
            return self._latest[1]

    def sample(self, count, deadline):
        frames = []
        after = monotonic()
        for _ in range(count):
            timestamp, frame = self._next(after, deadline)
            frames.append(frame)
            after = timestamp + FRAME_INTERVAL_SECONDS
        return frames

    def preview(self):
        after = 0
        while self.running:
            try:
                timestamp, frame = self._next(after, monotonic() + 0.5)
            except TimeoutError:
                continue
            except CameraUnavailable:
                return
            after = timestamp + 0.08
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
