"""Application configuration loaded from environment variables (.env)."""
import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("app")

# The value shipped in .env.example. Public by definition -- anyone who has
# seen this repository can forge a valid token (including an admin one)
# against any deployment still signing with it, so `_validate_secrets` below
# refuses to boot with it once ENVIRONMENT is "production".
PLACEHOLDER_SECRET_KEY = "change_this_to_a_long_random_secret_key"
# HS256 keys shorter than this are brute-forceable offline from a single
# captured token. 32 bytes matches the digest size of the underlying SHA-256.
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    # "development" | "production". Drives fail-fast validation of anything
    # that is merely a warning locally but must never reach a real
    # deployment (see _validate_secrets). Kept as a plain string rather than
    # an Enum so an unrecognised value is treated as non-production and
    # simply warns, instead of crashing the app on a config typo.
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql://exam_user:exam_pass@localhost:5432/exam_proctor"
    SECRET_KEY: str = PLACEHOLDER_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    UPLOAD_DIR: str = "uploads"
    CORS_ORIGINS: str = "http://127.0.0.1:5173,http://localhost:5173"
    AI_SERVICE_URL: str = ""
    AI_SERVICE_TIMEOUT_SECONDS: float = 8.0
    # Cosine distance (1 - dot) between two L2-normalised ArcFace embeddings.
    # Lower is stricter; 0 means identical. Both the in-process model and the
    # ai_worker use ArcFace, so this one number means the same thing on both
    # paths -- which was not the case when the local fallback was dlib, whose
    # Euclidean distances live on a completely different scale.
    #
    # Was 0.5 (cosine similarity 0.5), which is loose enough to false-accept a
    # different person often enough to matter on a proctoring platform -- a
    # false accept here means someone else can sit the exam undetected, which
    # is a far worse failure than a false reject (which just means "try the
    # capture again"). 0.38 is a meaningfully stricter operating point for
    # ArcFace-family embeddings while still tolerating normal lighting/angle/
    # expression variation between registration and exam day.
    FACE_MATCH_TOLERANCE: float = 0.38
    # InsightFace model pack for the local (in-process) ArcFace path.
    #   buffalo_l -- ~280MB, best accuracy (same pack the ai_worker uses)
    #   buffalo_s -- ~16MB, noticeably faster to download and load
    # Weights are fetched on first use and cached under FACE_MODEL_ROOT.
    FACE_MODEL_NAME: str = "buffalo_l"
    FACE_MODEL_ROOT: str = "models/insightface"

    # Local (in-process) object detection -- phone / book / extra person.
    # Previously this only existed inside the optional ai_worker container, so
    # with AI_SERVICE_URL blank it was switched off entirely; app/ai/object_service.py
    # now runs it locally. YOLO11s is the "small" tier: ~19MB, clearly stronger
    # than the yolov8n the worker used, still comfortably fast on CPU for a
    # signal polled every few seconds.
    #
    # The .onnx export is the preferred runtime (onnxruntime is already a hard
    # dependency for ArcFace, and is several times faster than the torch graph);
    # the .pt is what `python -m app.utils.fetch_models` downloads and exports
    # from, and what the ultralytics fallback loads if no export exists.
    OBJECT_MODEL_ROOT: str = "models/yolo"
    OBJECT_MODEL_ONNX: str = "yolo11s.onnx"
    OBJECT_MODEL_WEIGHTS: str = "yolo11s.pt"

    # Trained anti-spoofing model (see app/ai/anti_spoof.py). Optional: when no
    # ONNX file is present at this path the built-in classical detector runs
    # instead, so liveness checking never simply stops working.
    ANTISPOOF_MODEL_ROOT: str = "models/antispoof"
    ANTISPOOF_MODEL_ONNX: str = "antispoof.onnx"
    # Probability floor (0-1) from the trained model for a frame to count as
    # live. 0.5 is the natural decision boundary; raise it to be stricter.
    ANTISPOOF_LIVE_THRESHOLD: float = 0.5

    NOISE_DB_THRESHOLD: float = 65.0
    # Coding-question sandboxed execution (app/services/code_runner_service.py).
    # Runs student code in an ephemeral, network-disabled Docker container via
    # the `docker` CLI already on the host -- no separate microservice needed.
    # Exam lockdown. A "strike" is a fullscreen exit / tab switch / window
    # blur during a proctored attempt. Strikes are counted server-side from
    # persisted ProctorEvent rows, so refreshing the page cannot reset them.
    # On reaching the limit the attempt is auto-submitted and closed.
    LOCKDOWN_STRIKE_LIMIT: int = 3
    # Grace window that collapses a burst of related events (e.g. a single
    # Alt-Tab firing blur + visibilitychange + fullscreenchange together) into
    # one strike, so one physical action never costs three strikes.
    LOCKDOWN_STRIKE_DEBOUNCE_SECONDS: float = 3.0

    CODE_EXECUTION_ENABLED: bool = True
    CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS: int = 6
    CODE_EXECUTION_MEMORY_LIMIT: str = "128m"
    CODE_EXECUTION_CPUS: str = "0.5"
    # Ceiling on simultaneously-running sandbox containers. Each run is a
    # full `docker run`, so without a cap a hall of students all pressing
    # "Run" at once can exhaust host CPU/memory. Size this to the host, not
    # to the class: roughly (cores / CODE_EXECUTION_CPUS) is a sane start.
    CODE_EXECUTION_MAX_CONCURRENT: int = 4

    # Per-IP rate limiting on the unauthenticated auth endpoints (login,
    # student self-registration) -- see app/core/rate_limit.py. Login has no
    # account-lockout mechanism otherwise, so without this a brute-force
    # credential-stuffing run has no friction at all beyond bcrypt's own cost.
    AUTH_RATE_LIMIT_MAX_REQUESTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 60

    class Config:
        env_file = ".env"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    @model_validator(mode="after")
    def _validate_secrets(self):
        """Refuse to boot a production deployment with a guessable signing key.

        This is the single highest-severity misconfiguration the project can
        ship with: SECRET_KEY signs every JWT, and the default value lives in
        .env.example, so a deployment that never overrode it accepts tokens
        forged by anyone who has read the repo -- including `{"role":
        "admin"}`. It was previously guarded only by a comment in
        .env.example, which the code did nothing to enforce.

        Deliberately a hard failure rather than a warning in production: a
        warning in a startup log is exactly the kind of thing that scrolls
        past unnoticed, and the failure mode it precedes is silent, total
        authentication bypass. Locally it stays a warning so a fresh clone
        still runs with zero setup.
        """
        problems = []
        if self.SECRET_KEY == PLACEHOLDER_SECRET_KEY:
            problems.append(
                "SECRET_KEY is still the public placeholder from .env.example. Generate one with: "
                'python3 -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        elif len(self.SECRET_KEY) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"SECRET_KEY is only {len(self.SECRET_KEY)} characters; use at least {MIN_SECRET_KEY_LENGTH}."
            )
        if self.is_production and not self.cors_origins:
            problems.append("CORS_ORIGINS is empty; no frontend will be able to reach this API.")

        if problems:
            joined = " ".join(f"({i}) {p}" for i, p in enumerate(problems, 1))
            if self.is_production:
                raise ValueError(f"Refusing to start with an insecure production configuration: {joined}")
            logger.warning("Insecure configuration (tolerated because ENVIRONMENT=%s): %s", self.ENVIRONMENT, joined)
        return self

    @property
    def cors_origins(self) -> list[str]:
        # Only ever return an explicit allow-list of trusted frontend origins.
        # A bare "*" is rejected even if misconfigured in the environment, since
        # combining a wildcard origin with credentialed requests is unsafe and
        # this API is meant to be reachable only from known frontends.
        origins = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        return [origin for origin in origins if origin != "*"]

    @property
    def FACES_DIR(self) -> str:
        return f"{self.UPLOAD_DIR}/faces"

    @property
    def ID_CARDS_DIR(self) -> str:
        return f"{self.UPLOAD_DIR}/id_cards"

    @property
    def VIOLATIONS_DIR(self) -> str:
        return f"{self.UPLOAD_DIR}/violations"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()