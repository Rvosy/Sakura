from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import (
    QCamera,
    QImageCapture,
    QMediaCaptureSession,
    QMediaDevices,
)

from app.agent.screen_observation import CapturedScreenImage

CAM_TOUT_MS=8000
CAM_IDX=0
MIN_TOUT_MS=1000


class CamCap(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, didx: int = CAM_IDX, tout: int = CAM_TOUT_MS) -> None:
        super().__init__()
        self.didx=max(0,int(didx))
        self.tout=max(MIN_TOUT_MS,int(tout))
        self._cncl: list[bool] = [False]

    @Slot()
    def clr(self) -> None:
        self._cncl[0] = True

    @Slot()
    def run(self) -> None:
        if self._cncl[0]:
            self.cancelled.emit()
            return
        try:
            got = _grab(self.didx, self.tout)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if self._cncl[0]:
            self.cancelled.emit()
            return
        self.finished.emit(got)


def _grab(didx: int, tout: int) -> CapturedScreenImage:
    ds = QMediaDevices.videoInputs()
    if not ds:
        raise RuntimeError("未检测到可用的摄像头。")
    if didx >= len(ds):
        didx = 0
    vi = ds[didx]
    cam = QCamera(vi)
    cap = QImageCapture(cam)
    ss = QMediaCaptureSession()
    ss.setCamera(cam)
    ss.setImageCapture(cap)
    loop = QEventLoop()
    o: dict[str, object] = {}

    def on_img(_rid: int, img: QImage) -> None:
        o["image"] = img
        loop.quit()

    def on_err(_rid: int, _e: QImageCapture.Error, msg: str) -> None:
        o["error"] = msg or "摄像头拍摄失败。"
        loop.quit()

    cap.imageCaptured.connect(on_img)
    cap.errorOccurred.connect(on_err)
    QTimer.singleShot(tout, loop.quit)
    cam.start()
    cap.capture()
    loop.exec()
    cam.stop()
    err = o.get("error")
    if err is not None:
        raise RuntimeError(str(err))
    img = o.get("image")
    if not isinstance(img, QImage) or img.isNull():
        raise RuntimeError("摄像头拍摄超时或未返回图像。")
    return CapturedScreenImage(
        image=img.copy(),
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        screen_name=vi.description() or f"camera-{didx}",
    )
