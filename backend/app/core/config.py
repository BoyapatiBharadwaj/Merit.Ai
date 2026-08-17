"""Application configuration loaded from environment variables (.env)."""
import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("app")

# The value shipped in .env.example.
PLACEHOLDER_SECRET_KEY = "change_this_to_a_long_random_secret_key"
# HS256 keys shorter than this are brute-forceable offline from a single
# captured token. 32 bytes matches the digest size of the underlying SHA-256.
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    # "development" | "production". Drives fail-fast validation of anything that is merely a
    # warning locally but must never reach a real deployment (see _validate_secrets).
    ENVIRONMENT: str = "development"
    # Only ever used when DATABASE_URL is unset -- running the app
    # or the tests straight from backend/ with no environment.
    DATABASE_URL: str = "postgresql://merit_ai:change_this_password@localhost:5432/merit_ai"
    SECRET_KEY: str = PLACEHOLDER_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    # Extra life on the attempt-scoped token beyond the attempt's own deadline (see
    # security.create_attempt_token).
    ATTEMPT_TOKEN_GRACE_MINUTES: int = 30

    # --- account policy ---
    # Refuse student self-registration that has not proved control of the email address.
    REQUIRE_EMAIL_VERIFICATION: bool = True

    # Refuse a registration that does not carry the consent the form asks for.
    REQUIRE_CONSENT_ON_SIGNUP: bool = True

    # Length floor for new passwords.
    PASSWORD_MIN_LENGTH: int = 10

    # Networks whose X-Forwarded-For header may be believed, comma separated, as IPs or CIDRs.
    TRUSTED_PROXY_IPS: str = "172.16.0.0/12,10.0.0.0/8,192.168.0.0/16,127.0.0.1/32"

    UPLOAD_DIR: str = "uploads"
    CORS_ORIGINS: str = "http://127.0.0.1:5173,http://localhost:5173"
    AI_SERVICE_URL: str = ""
    AI_SERVICE_TIMEOUT_SECONDS: float = 8.0
    # Cosine distance (1 - dot) between two L2-normalised
    # ArcFace embeddings. Lower is stricter; 0 means identical.
    FACE_MATCH_TOLERANCE: float = 0.38
    # InsightFace model pack for the local (in-process) ArcFace path. buffalo_l.
    FACE_MODEL_NAME: str = "buffalo_l"
    FACE_MODEL_ROOT: str = "models/insightface"

    # Local (in-process) object detection -- phone / book / extra person.
    OBJECT_MODEL_ROOT: str = "models/yolo"
    OBJECT_MODEL_ONNX: str = "yolo11s.onnx"
    OBJECT_MODEL_WEIGHTS: str = "yolo11s.pt"

    # Trained anti-spoofing model (see app/ai/anti_spoof.py).
    ANTISPOOF_MODEL_ROOT: str = "models/antispoof"
    ANTISPOOF_MODEL_ONNX: str = "antispoof.onnx"
    # Probability floor (0-1) from the trained model for a frame to count as
    # live. 0.5 is the natural decision boundary; raise it to be stricter.
    ANTISPOOF_LIVE_THRESHOLD: float = 0.5

    NOISE_DB_THRESHOLD: float = 65.0
    # Coding-question sandboxed execution (app/services/code_runner_service.py).
    LOCKDOWN_STRIKE_LIMIT: int = 3
    # Grace window that collapses a burst of related events (e.g. a single
    # Alt-Tab firing blur + visibilitychange + fullscreenchange together) into
    # one strike, so one physical action never costs three strikes.
    LOCKDOWN_STRIKE_DEBOUNCE_SECONDS: float = 3.0

    CODE_EXECUTION_ENABLED: bool = True
    CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS: int = 6
    CODE_EXECUTION_MEMORY_LIMIT: str = "128m"
    CODE_EXECUTION_CPUS: str = "0.5"
    # Ceiling on simultaneously-running sandbox containers. Each run is a full `docker run`, so
    # without a cap a hall of students all pressing "Run" at once can exhaust host CPU/memory.
    CODE_EXECUTION_MAX_CONCURRENT: int = 4

    # Per-IP rate limiting on the unauthenticated auth endpoints (login, student
    # self-registration) -- see app/core/rate_limit.py.
    AUTH_RATE_LIMIT_MAX_REQUESTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Per-USER ceiling on the CPU-expensive proctoring endpoints
    # (face/verify, id-card/verify, objects/detect, pose/check).
    AI_RATE_LIMIT_MAX_REQUESTS: int = 30
    AI_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # --- Outbound email (app/services/email_service.py) ---
    # Everything email-driven in this app is OFF by default and degrades to a logged no-op
    # rather than an error, which is deliberate.
    EMAIL_ENABLED: bool = False
    SMTP_HOST: str = "smtp.gmail.com"
    # 587 = STARTTLS (upgrade a plaintext connection), 465 = implicit TLS.
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    # Bounded so a wedged or blackholed SMTP host cannot pin a worker thread indefinitely.
    SMTP_TIMEOUT_SECONDS: float = 20.0
    # Defaults to SMTP_USERNAME when blank (see email_service.from_address).
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "Merit.Ai"
    # Where "an examiner requested access" notifications go. Falls back to
    # every active admin account's email when blank.
    ADMIN_NOTIFICATION_EMAIL: str = ""

    # --- Email outbox (app/services/email_service.py, app/models/email_outbox.py) ---
    # Bounded retry budget for a message queued through enqueue_tracked() -- an admin
    # notification, an activation link, an OTP code.
    EMAIL_OUTBOX_MAX_ATTEMPTS: int = 5
    # Exponential backoff base for retries: attempt N waits roughly
    # base * 2^(N-1) seconds, capped at EMAIL_OUTBOX_RETRY_MAX_SECONDS.
    EMAIL_OUTBOX_RETRY_BASE_SECONDS: int = 60
    EMAIL_OUTBOX_RETRY_MAX_SECONDS: int = 3600
    # Used to build absolute links in emails (a relative /login is meaningless
    # in an inbox). Should match the origin students actually load the app on.
    APP_BASE_URL: str = "http://localhost"

    # --- One-time passcodes (app/services/otp_service.py) --------------------
    OTP_LENGTH: int = 6
    OTP_TTL_MINUTES: int = 10
    # Wrong-guess budget per issued code. At 6 digits there are a million possibilities, so this
    # is not really about brute force.
    OTP_MAX_ATTEMPTS: int = 5
    # Minimum gap between issuing two codes to the same address. Stops the
    # "resend" button from being used to mailbomb someone else's inbox.
    OTP_RESEND_COOLDOWN_SECONDS: int = 60
    # Activation links (app/services/otp_service.py:issue_activation_token).
    ACTIVATION_TTL_HOURS: int = 72
    # Registration mode. "open" lets anyone create a student account; "invite" accepts only
    # addresses an examiner has already put on an exam roster.
    REGISTRATION_MODE: str = "open"
    # Email staff (examiner/admin) accounts when a new sign-in happens.
    NOTIFY_STAFF_ON_LOGIN: bool = True
    # Minimum gap between two admin notifications about the SAME pending access request.
    ACCESS_REQUEST_RENOTIFY_SECONDS: int = 900

    # --- Database pool (app/database/session.py) ---
    # PER PROCESS. Total connections to Postgres are (DB_POOL_SIZE + DB_MAX_OVERFLOW) x
    # WEB_CONCURRENCY x replicas, plus the worker and scheduler services.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT_SECONDS: int = 30
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # --- Redis (app/core/redis_client.py) ---
    # The shared, ephemeral-state backend for ONLY four things.
    REDIS_URL: str = "redis://localhost:6379/0"
    # Kept separate from REDIS_URL (rather than embedded as redis://:pass@host) so
    # docker-compose.yml can pass the same POSTGRES-style secret pattern.
    REDIS_PASSWORD: str = ""
    # Bounded so a Redis instance that is down (not just slow) fails a rate
    # limit check, an OTP verification or a lock acquisition in around two
    # seconds, not however long TCP takes to notice a dead peer.
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 2.0

    # --- Distributed locks (app/core/locks.py) ---
    # Default hold time for a lock whose caller doesn't specify one.
    LOCK_DEFAULT_TTL_SECONDS: float = 30.0

    # --- Background job queue (app/core/queues.py, app/worker/) ---
    # RQ over Redis. Three queues: emails, reports, default.
    JOB_MAX_RETRIES: int = 3
    # Ceiling on how long the Worker lets one job run
    # before treating it as stuck and killing it.
    JOB_TIMEOUT_SECONDS: int = 300

    # --- Proctoring signal kill switches ---
    # Each AI signal can be switched off independently, without
    # a redeploy and without stopping exams in progress.
    FACE_MATCHING_ENABLED: bool = True
    OBJECT_DETECTION_ENABLED: bool = True
    POSE_DETECTION_ENABLED: bool = True

    # --- Biometric data lifecycle (app/services/biometric_service.py) ---
    # This platform stores face photos, 512-d ArcFace
    # embeddings, ID-card images and violation screenshots.
    BIOMETRIC_CONSENT_VERSION: str = "2026-08-04.v1"
    # Days after a student's last activity before their
    # biometric data becomes eligible for automatic deletion.
    BIOMETRIC_RETENTION_DAYS: int = 0
    # How often the purge sweep runs, when retention is enabled at all.
    BIOMETRIC_PURGE_INTERVAL_HOURS: int = 24

    # --- Exam reminders (app/services/reminder_service.py) -------------------
    EXAM_REMINDER_ENABLED: bool = True
    EXAM_REMINDER_MINUTES_BEFORE: int = 5
    # How often the background loop looks for exams due a reminder.
    EXAM_REMINDER_POLL_SECONDS: int = 60

    class Config:
        env_file = ".env"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    @model_validator(mode="after")
    def _validate_secrets(self):
        """Refuse to boot a production deployment with a guessable signing key."""
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

        # Requiring verified email while being unable to send any is a closed door with no key.
        if self.REQUIRE_EMAIL_VERIFICATION and not self.EMAIL_ENABLED:
            problems.append(
                "REQUIRE_EMAIL_VERIFICATION is on but EMAIL_ENABLED is off, so no candidate could "
                "ever register: verification is mandatory and no code can be sent. Enable email, or "
                "set REQUIRE_EMAIL_VERIFICATION=false."
            )

        if problems:
            joined = " ".join(f"({i}) {p}" for i, p in enumerate(problems, 1))
            if self.is_production:
                raise ValueError(f"Refusing to start with an insecure production configuration: {joined}")
            logger.warning("Insecure configuration (tolerated because ENVIRONMENT=%s): %s", self.ENVIRONMENT, joined)
        return self

    @property
    def trusted_proxies(self) -> list:
        """TRUSTED_PROXY_IPS parsed into networks. Malformed entries are dropped."""
        import ipaddress

        networks = []
        for entry in self.TRUSTED_PROXY_IPS.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning("Ignoring unparseable TRUSTED_PROXY_IPS entry: %r", entry)
        return networks

    @property
    def cors_origins(self) -> list[str]:
        # Only ever return an explicit allow-list of trusted frontend origins.
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