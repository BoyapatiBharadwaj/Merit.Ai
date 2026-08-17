"""Download the ArcFace model pack ahead of time."""
import argparse
import sys
import time
from pathlib import Path

from app.core.config import settings


def run(model_name: str, model_root: str) -> int:
    print(f"Model pack : {model_name}")
    print(f"Cache root : {Path(model_root).resolve()}")

    try:
        from insightface.app import FaceAnalysis
    except ImportError as error:
        print(f"\nFAILED: insightface is not installed ({error}).")
        print("        pip install insightface onnxruntime")
        return 1

    try:
        import onnxruntime  # noqa: F401
    except ImportError as error:
        print(f"\nFAILED: onnxruntime is not installed ({error}).")
        print("        pip install onnxruntime")
        return 1

    print("\nPreparing the model (first run downloads the weights; this can take a few minutes)...")
    started = time.time()
    try:
        model = FaceAnalysis(name=model_name, root=model_root, providers=["CPUExecutionProvider"])
        model.prepare(ctx_id=-1, det_size=(640, 640))
    except Exception as error:
        print(f"\nFAILED after {time.time() - started:.1f}s: {type(error).__name__}: {error}")
        print("\nMost likely causes:")
        print("  * no route to github.com / release-assets.githubusercontent.com")
        print("    (a proxy or firewall will show up as a ProxyError or timeout here)")
        print(f"  * '{model_name}' is not a valid pack name -- try buffalo_l or buffalo_s")
        print(f"  * no write permission for {Path(model_root).resolve()}")
        print("\nIf this host genuinely cannot reach GitHub, either download the pack")
        print("elsewhere and copy it into the cache root, or run the ai_worker container")
        print("and point AI_SERVICE_URL at it instead.")
        return 1

    elapsed = time.time() - started
    # A loaded pack should expose a recognition model; if it does not, the download succeeded
    # but this pack cannot do identity, which would fail later in a much more confusing place.
    has_recognition = any(getattr(m, "taskname", "") == "recognition" for m in model.models.values())
    print(f"\nOK -- model ready in {elapsed:.1f}s")
    print(f"   submodels: {', '.join(sorted(model.models))}")
    if not has_recognition:
        print("\nWARNING: this pack has no recognition submodel, so face *matching* will not work.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-download the ArcFace model pack.")
    parser.add_argument("--model", default=settings.FACE_MODEL_NAME,
                        help=f"insightface pack name (default: {settings.FACE_MODEL_NAME})")
    parser.add_argument("--root", default=settings.FACE_MODEL_ROOT,
                        help=f"cache directory (default: {settings.FACE_MODEL_ROOT})")
    args = parser.parse_args()
    return run(args.model, args.root)


if __name__ == "__main__":
    sys.exit(main())
