"""
Download and prepare every AI model the proctoring stack uses, ahead of time.

Run once after `pip install -r requirements.txt`, as part of deployment:

    python -m app.utils.fetch_models                # everything
    python -m app.utils.fetch_models --only yolo    # just one
    python -m app.utils.fetch_models --list

Why this exists: every model here lazy-downloads its weights on first use. Left
alone, that download happens *inside a student's request* -- slow at best, and
on a host that cannot reach GitHub it fails outright and surfaces as a 503 that
looks like a broken install. Doing it deliberately at deploy time turns a
mid-exam failure into a setup-time one.

Everything is idempotent: already-present weights are verified, not refetched.

    face       ArcFace identity matching (InsightFace). ~280MB for buffalo_l.
    yolo       YOLO11s object detection, plus an ONNX export. ~19MB.
    antispoof  Optional trained liveness model. Reports how to install it.
    ocr        EasyOCR detection + recognition weights for ID cards. ~100MB.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

from app.core.config import settings


class Step:
    """One model's outcome, so main() can print a single honest summary."""

    def __init__(self, name: str):
        self.name = name
        self.status = "skipped"
        self.detail = ""
        self.seconds = 0.0

    def ok(self, detail: str):
        self.status, self.detail = "ok", detail
        return self

    def fail(self, detail: str):
        self.status, self.detail = "FAILED", detail
        return self

    def warn(self, detail: str):
        self.status, self.detail = "note", detail
        return self


def _network_hint() -> str:
    return ("      Most likely: no route to github.com (a proxy or firewall shows up here\n"
            "      as a ProxyError or a timeout), or no write permission for the cache dir.")


# ---------------------------------------------------------------------------
# ArcFace
# ---------------------------------------------------------------------------

def fetch_face(step: Step) -> Step:
    print(f"[face]      pack '{settings.FACE_MODEL_NAME}' -> {Path(settings.FACE_MODEL_ROOT).resolve()}")
    try:
        from insightface.app import FaceAnalysis
    except ImportError as error:
        return step.fail(f"insightface is not installed ({error}). pip install insightface onnxruntime")

    try:
        model = FaceAnalysis(name=settings.FACE_MODEL_NAME, root=settings.FACE_MODEL_ROOT,
                             providers=["CPUExecutionProvider"])
        model.prepare(ctx_id=-1, det_size=(640, 640))
    except Exception as error:
        print(_network_hint())
        return step.fail(f"{type(error).__name__}: {error}")

    # A pack without a recognition submodel downloads fine but cannot do
    # identity matching -- which would otherwise fail much later, somewhere
    # far less obvious.
    if not any(getattr(m, "taskname", "") == "recognition" for m in model.models.values()):
        return step.fail(f"pack '{settings.FACE_MODEL_NAME}' has no recognition submodel; "
                         "face matching would not work")
    return step.ok(f"submodels: {', '.join(sorted(model.models))}")


# ---------------------------------------------------------------------------
# YOLO
# ---------------------------------------------------------------------------

def fetch_yolo(step: Step) -> Step:
    root = Path(settings.OBJECT_MODEL_ROOT)
    onnx_path = root / settings.OBJECT_MODEL_ONNX
    print(f"[yolo]      {settings.OBJECT_MODEL_WEIGHTS} -> {root.resolve()}")

    if onnx_path.is_file():
        return step.ok(f"{onnx_path.name} already present "
                       f"({onnx_path.stat().st_size / 1e6:.1f}MB)")

    try:
        from ultralytics import YOLO
    except ImportError:
        return step.warn(
            "ultralytics is not installed, so the ONNX export cannot be produced.\n"
            "            Object detection still works if you install it "
            "(`pip install ultralytics`),\n"
            "            but the faster onnxruntime path needs this export.")

    root.mkdir(parents=True, exist_ok=True)
    weights = root / settings.OBJECT_MODEL_WEIGHTS
    try:
        # Passing the bare name lets ultralytics fetch it into the CWD; move it
        # under our own root afterwards so everything lives in one place.
        model = YOLO(str(weights) if weights.is_file() else settings.OBJECT_MODEL_WEIGHTS)
        downloaded = Path(settings.OBJECT_MODEL_WEIGHTS)
        if downloaded.is_file() and not weights.is_file():
            shutil.move(str(downloaded), str(weights))

        print("            exporting to ONNX (one-off, takes a minute)...")
        exported = Path(model.export(format="onnx", imgsz=640, simplify=False))
        if exported.resolve() != onnx_path.resolve():
            shutil.move(str(exported), str(onnx_path))
    except Exception as error:
        print(_network_hint())
        return step.fail(f"{type(error).__name__}: {error}")

    if not onnx_path.is_file():
        return step.fail("export reported success but no .onnx file was produced")
    return step.ok(f"{onnx_path.name} ready ({onnx_path.stat().st_size / 1e6:.1f}MB)")


# ---------------------------------------------------------------------------
# Anti-spoofing
# ---------------------------------------------------------------------------

def fetch_antispoof(step: Step) -> Step:
    """The trained liveness model is opt-in and must be supplied deliberately.

    There is no single canonical, stable download URL for a MiniFASNet ONNX
    export the way there is for ArcFace and YOLO -- the widely-used weights are
    scattered across forks and re-uploads. Baking one of those URLs in would
    mean the setup step silently pulls an unverified binary from a third party,
    which is a bad trade for a system that decides whether a student is
    cheating. So this reports what to install and where, and the built-in
    classical detector runs until you do.
    """
    root = Path(settings.ANTISPOOF_MODEL_ROOT)
    path = root / settings.ANTISPOOF_MODEL_ONNX
    print(f"[antispoof] {path.resolve()}")

    if path.is_file():
        try:
            import onnxruntime as ort

            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            spec = session.get_inputs()[0]
            outputs = session.run(None, {spec.name: _dummy_input(spec)})
            classes = int(len(outputs[0].reshape(-1)))
        except Exception as error:
            return step.fail(f"model file is present but will not load: "
                             f"{type(error).__name__}: {error}")
        return step.ok(f"trained model loaded (input {spec.shape}, {classes}-class head)")

    root.mkdir(parents=True, exist_ok=True)
    return step.warn(
        "no trained model installed -- the built-in classical detector is in use.\n"
        f"            To upgrade, drop a MiniFASNet-style ONNX classifier at\n"
        f"              {path.resolve()}\n"
        "            It should take an NCHW float image in 0-1 and return either a\n"
        "            single P(live) or a softmax head whose index 1 is 'live'\n"
        "            (both 2- and 3-class MiniFASNet layouts work). No restart or\n"
        "            config change is needed beyond placing the file.")


def _dummy_input(spec):
    import numpy as np

    dims = [d if isinstance(d, int) and d > 0 else fallback
            for d, fallback in zip(spec.shape, (1, 3, 80, 80))]
    return np.zeros(dims, dtype=np.float32)


# ---------------------------------------------------------------------------
# EasyOCR
# ---------------------------------------------------------------------------

def fetch_ocr(step: Step) -> Step:
    print("[ocr]       EasyOCR english_g2 + craft detector")
    try:
        import easyocr
    except ImportError as error:
        return step.fail(f"easyocr is not installed ({error}). pip install easyocr")
    try:
        easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception as error:
        print(_network_hint())
        return step.fail(f"{type(error).__name__}: {error}")
    return step.ok("reader initialised")


# ---------------------------------------------------------------------------

FETCHERS = {
    "face": fetch_face,
    "yolo": fetch_yolo,
    "antispoof": fetch_antispoof,
    "ocr": fetch_ocr,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-download every AI model used by proctoring.")
    parser.add_argument("--only", action="append", choices=sorted(FETCHERS), metavar="NAME",
                        help=f"fetch just this model (repeatable): {', '.join(sorted(FETCHERS))}")
    parser.add_argument("--list", action="store_true", help="list the models and exit")
    args = parser.parse_args()

    if args.list:
        for name in FETCHERS:
            print(name)
        return 0

    selected = args.only or list(FETCHERS)
    print(f"Preparing: {', '.join(selected)}\n")

    steps = []
    for name in selected:
        step = Step(name)
        started = time.time()
        try:
            FETCHERS[name](step)
        except Exception as error:  # a fetcher itself blowing up must not hide the rest
            step.fail(f"unexpected {type(error).__name__}: {error}")
        step.seconds = time.time() - started
        steps.append(step)
        print()

    print("-" * 68)
    for step in steps:
        marker = {"ok": "  OK  ", "note": " NOTE ", "FAILED": "FAILED"}[step.status]
        print(f"[{marker}] {step.name:<10} {step.seconds:6.1f}s  {step.detail}")
    print("-" * 68)

    failures = [s.name for s in steps if s.status == "FAILED"]
    if failures:
        print(f"\n{len(failures)} model(s) could not be prepared: {', '.join(failures)}")
        print("Proctoring will still start -- each signal degrades independently -- but")
        print("the affected checks will report themselves as unavailable.")
        return 1
    print("\nAll selected models are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
