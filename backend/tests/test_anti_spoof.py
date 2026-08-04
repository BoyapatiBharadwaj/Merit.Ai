"""Liveness / anti-spoofing detector.

Two things need proving here, and they are different in kind:

1. That the cues *discriminate* -- a synthetic screen-replay pattern must
   score materially lower than natural, broadband texture. Asserting a
   direction and a gap is meaningful; asserting exact magnitudes on synthetic
   images would just be pinning down arbitrary numbers.
2. That the *contract* holds -- fail-open on error, model preferred over
   classical, weird model output degrades rather than throwing.
"""
import numpy as np
import pytest
from PIL import Image, ImageFilter

from app.ai import anti_spoof


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """The model decision is process-cached by design; tests must not inherit
    each other's answer."""
    anti_spoof.reset_model_cache()
    yield
    anti_spoof.reset_model_cache()


def _pink_noise(size, seed, exponent=1.2):
    """Noise with a 1/f^exponent spectrum -- the statistical signature of a
    natural photograph.

    This matters more than it looks. The earlier fixtures used near-white
    noise, whose spectrum is flat, and the detector was calibrated against
    them. Real images fall off steeply with frequency, and the original moire
    metric turned out to be measuring that falloff rather than periodicity --
    so it passed every synthetic test and then rejected the first real face it
    saw. Testing against a spectrally realistic image is what makes these
    tests mean anything.
    """
    rng = np.random.default_rng(seed)
    white = rng.normal(0, 1, (size, size))
    freqs_y = np.fft.fftfreq(size)[:, None]
    freqs_x = np.fft.fftfreq(size)[None, :]
    radius = np.sqrt(freqs_y ** 2 + freqs_x ** 2)
    radius[0, 0] = 1e-6
    shaped = np.fft.ifft2(np.fft.fft2(white) / radius ** exponent).real
    shaped -= shaped.mean()
    return shaped / (shaped.std() or 1.0)


def _natural_texture(size=480, seed=0, blur=0.6, noise=11.0) -> np.ndarray:
    """A stand-in for a real face: smooth skin-toned shading plus fine detail
    with a photograph-like 1/f spectrum."""
    ys, xs = np.mgrid[0:size, 0:size]
    base = 150 + 40 * np.sin(2 * np.pi * ys / (size * 1.7)) + 25 * np.cos(2 * np.pi * xs / (size * 1.3))
    detail = _pink_noise(size, seed) * noise
    rgb = np.stack([(base + detail) * 1.14, (base + detail) * 0.92, (base + detail) * 0.78], axis=-1)
    return np.asarray(
        Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur)))


def _screen_replay(size=480, seed=0, period=3, amplitude=14.0) -> np.ndarray:
    """Natural texture with a regular pixel grid over it -- the moire
    signature a camera picks up when pointed at an LCD/OLED panel.

    `period` is in pixels and must not be exactly 2: sin(2*pi*x/2) sampled at
    integer x is identically zero, so a "period 2" grid adds literally nothing
    to the image and tests built on it silently assert nothing at all.
    """
    assert period != 2, "a period-2 grid vanishes under integer sampling"
    base = _natural_texture(size, seed).astype(np.float64)
    ys, xs = np.mgrid[0:size, 0:size]
    grid = amplitude * (np.sin(2 * np.pi * xs / period) + np.sin(2 * np.pi * ys / period))
    return np.clip(base + grid[:, :, None], 0, 255).astype(np.uint8)


def _printed_photo(size=480, seed=0) -> np.ndarray:
    """A print: soft, desaturated, stripped of micro-texture."""
    soft = np.asarray(
        Image.fromarray(_natural_texture(size, seed)).filter(ImageFilter.GaussianBlur(3.0)),
        dtype=np.float64)
    mean = soft.mean(axis=2, keepdims=True)
    return np.clip(mean + (soft - mean) * 0.25, 0, 255).astype(np.uint8)


# Conditions a real webcam actually produces. Blur is capped at 1.0 -- the
# earlier 2.2 was far blurrier than any working camera and made "soft focus"
# a test of the degenerate-frame clamp rather than of ordinary conditions.
_REAL_FRAMES = {
    "ordinary": _natural_texture(),
    "grainy webcam": _natural_texture(seed=10, blur=0.2, noise=20.0),
    "soft focus": _natural_texture(seed=4, blur=1.0, noise=9.0),
    "dim room": np.clip(_natural_texture(seed=9, noise=10.0).astype(np.float64) * 0.55,
                        0, 255).astype(np.uint8),
}


# ---------------------------------------------------------------------------
# Cue discrimination
# ---------------------------------------------------------------------------

def _moire(image: np.ndarray) -> float:
    patch = anti_spoof._centre_face_patch(image)
    gray = np.asarray(Image.fromarray(patch).convert("L"), dtype=np.float32)
    return anti_spoof._moire_score(gray)


def test_moire_score_is_higher_for_a_screen_than_for_natural_texture():
    """The core claim of the FFT cue. If this gap does not exist, the whole
    screen-replay branch is decoration."""
    natural = _moire(_natural_texture())
    screen = _moire(_screen_replay())

    assert screen > natural * 1.3, f"natural={natural:.2f} screen={screen:.2f}"


def test_moire_score_measures_periodicity_not_spectral_slope():
    """The bug that rejected a real student.

    The old metric compared each frequency bin against the global median of a
    wide band. Natural images have a steep 1/f falloff, so that ratio tracked
    spectral *slope*: a real photograph scored 46 while flat synthetic noise
    scored 4.7, and every real face was reported as a display replay. After
    whitening against the per-radius envelope, a smooth gradient (steep
    falloff, zero periodicity) must score no worse than flat noise.
    """
    size = 256
    # Broadband but very steeply decaying -- a smooth, natural-looking image
    # with no periodic component. NOT a single sinusoid: that is by definition
    # maximally periodic and would (correctly) score enormously high.
    steep = np.clip(128 + _pink_noise(size, 3, exponent=2.2) * 40, 0, 255).astype(np.float32)
    flat = np.random.default_rng(0).integers(0, 255, (size, size)).astype(np.float32)

    steep_score = anti_spoof._moire_score(steep)
    flat_score = anti_spoof._moire_score(flat)
    assert steep_score < flat_score * 2.0, (
        f"steep 1/f gradient scored {steep_score:.2f} vs flat noise {flat_score:.2f} -- "
        "the metric is tracking spectral slope again")
    assert steep_score < anti_spoof.MOIRE_CLEAN, "a smooth gradient must not read as a screen"


def test_no_realistic_live_frame_trips_the_moire_threshold():
    """The direction that actually matters.

    False positives here cost a real student their exam; false negatives cost
    one weak signal out of several. So this asserts the safe direction hard,
    and the detection direction only for an unambiguous grid (below).
    """
    for name, frame in _REAL_FRAMES.items():
        assert _moire(frame) < anti_spoof.MOIRE_CLEAN, (
            f"{name} scored {_moire(frame):.2f}, at or above the 'nothing periodic' "
            f"threshold of {anti_spoof.MOIRE_CLEAN} -- it would be flagged as a screen")


def test_only_a_strong_grid_registers_as_periodic():
    """The honest limit of this cue.

    Measured against spectrally realistic frames, a *faint* display grid is
    not separable from ordinary content -- earlier numbers suggesting
    otherwise came from white-noise fixtures whose flat spectra flattered the
    metric. A strong, clearly visible grid does register. That gap is exactly
    why the classical tier is advisory and a trained model is recommended.
    """
    strong = _moire(_screen_replay(period=3, amplitude=14.0))
    ordinary = _moire(_natural_texture())

    assert strong > ordinary * 2.0, f"strong grid {strong:.2f} vs ordinary {ordinary:.2f}"
    assert strong > anti_spoof.MOIRE_CLEAN


def test_a_screen_replay_is_rejected_even_though_it_looks_sharp_and_colourful():
    """The reason moire gets a veto rather than a vote: a display shows crisp,
    saturated, glare-free content, so every *other* cue actively vouches for
    it. A plain weighted average lets a screen through."""
    natural = anti_spoof.assess_liveness(_natural_texture())
    screen = anti_spoof.assess_liveness(_screen_replay())

    assert natural["live"] is True
    assert screen["live"] is False
    assert "screen-pixel pattern" in screen["reason"]
    # Classical results are advisory -- they warn, they do not refuse. See
    # anti_spoof.CLASSICAL_IS_ADVISORY.
    assert screen["blocking"] is False


def test_a_printed_photo_is_rejected():
    result = anti_spoof.assess_liveness(_printed_photo())

    assert result["live"] is False
    assert result["score"] < anti_spoof.LIVE_SCORE_THRESHOLD


def test_a_blank_frame_is_not_treated_as_live():
    """Without the degenerate clamp this scores *well*: no texture also means
    no screen pattern and no glare, so three of four cues vouch for it."""
    for blank in (np.full((400, 400, 3), 150, dtype=np.uint8),
                  np.zeros((400, 400, 3), dtype=np.uint8),
                  np.full((400, 400, 3), 255, dtype=np.uint8)):
        result = anti_spoof.assess_liveness(blank)
        assert result["live"] is False
        assert result["score"] <= anti_spoof.DEGENERATE_SCORE_CAP


def test_ordinary_webcam_conditions_are_not_flagged():
    """False positives here are expensive -- they interrupt a real exam, or
    stop a student registering at all -- so grainy, soft and dim frames must
    all still pass."""
    for name, frame in _REAL_FRAMES.items():
        result = anti_spoof.assess_liveness(frame)
        assert result["live"] is True, f"{name} was wrongly flagged: {result['reason']}"


def test_scores_are_always_reported_in_the_zero_to_one_range():
    for image in (_natural_texture(), _screen_replay(), _printed_photo(),
                  np.zeros((300, 300, 3), np.uint8), np.full((300, 300, 3), 255, np.uint8)):
        result = anti_spoof.assess_liveness(image)
        assert 0.0 <= result["score"] <= 1.0


def test_laplacian_border_artefact_does_not_inflate_sharpness():
    """PIL's Kernel filter leaves the outer 1px ring unfiltered. Counting it
    gave a perfectly flat patch a sharpness of 676 -- the blankest possible
    fake measuring as richly textured."""
    flat = np.full((anti_spoof.PATCH_MIN, anti_spoof.PATCH_MIN, 3), 150, dtype=np.uint8)
    luma = Image.fromarray(flat).convert("L")

    cropped = np.asarray(luma.filter(anti_spoof._LAPLACIAN_KERNEL), dtype=np.float32)[1:-1, 1:-1]

    assert cropped.var() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# The face-crop fix
# ---------------------------------------------------------------------------

def test_liveness_measures_the_centre_not_the_background():
    """The bug this replaced: statistics were computed over the whole frame, so
    a flat fake face surrounded by a rich, sharp room scored as live off the
    background alone. The measurement must follow the centre crop instead."""
    frame = np.array(_natural_texture(600, seed=3))  # PIL output is read-only
    frame[120:480, 120:480] = 150  # flat, dead-centre "photo held up to camera"

    assert anti_spoof.assess_liveness(frame)["live"] is False


def test_centre_patch_is_square_and_within_bounds_for_any_input_size():
    for shape in ((480, 640, 3), (720, 720, 3), (200, 900, 3), (60, 80, 3)):
        patch = anti_spoof._centre_face_patch(np.zeros(shape, dtype=np.uint8))
        assert patch.shape[0] == patch.shape[1]
        assert patch.shape[2] == 3
        assert anti_spoof.PATCH_MIN <= patch.shape[0] <= anti_spoof.PATCH_MAX


def test_the_analysis_window_covers_the_face_not_a_sliver_of_cheek():
    """The false positive that rejected a real student.

    A fixed 128px crop from the centre of a 574x430 webcam frame landed on
    smooth cheek and chin -- almost no texture -- and was reported as "low
    texture detail (possible printed photo)". The window has to scale with the
    frame so it reaches eyes, brows and hairline, where a face has detail.
    """
    patch = anti_spoof._centre_face_patch(np.zeros((430, 574, 3), dtype=np.uint8))
    assert patch.shape[0] >= 250, f"window {patch.shape[0]}px is too small to cover a face"


def test_centre_patch_does_not_resample_a_large_frame():  # noqa: D401
    """Resizing destroys the very signal the moire cue reads: downscaling a
    360px crop to 128px aliases a display's pixel grid away completely, which
    measured against real data collapsed the natural-vs-screen gap to nothing.
    A big frame must be cropped at native resolution, not resized."""
    frame = _screen_replay(600)
    patch = anti_spoof._centre_face_patch(frame)
    side = patch.shape[0]
    height, width = frame.shape[:2]
    top, left = (height - side) // 2, (width - side) // 2

    assert np.array_equal(patch, frame[top:top + side, left:left + side])


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

def test_result_always_carries_the_documented_keys():
    result = anti_spoof.assess_liveness(_natural_texture())
    assert set(result) == {"live", "score", "reason", "source", "blocking"}


def test_the_classical_tier_never_blocks_on_its_own():
    """The policy that stopped a real student being locked out at registration.

    A hand-written heuristic that has already false-positived on a genuine
    face should log and warn, not refuse. Only a trained model gets to refuse.
    """
    for image in (_screen_replay(), _printed_photo(),
                  np.full((400, 400, 3), 150, dtype=np.uint8)):
        result = anti_spoof.assess_liveness(image)
        assert result["source"] == "classical"
        assert result["blocking"] is False


def test_a_trained_model_verdict_does_block(monkeypatch):
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: _FakeSession([[5.0, 0.0]]))
    result = anti_spoof.assess_liveness(_natural_texture())
    assert result["live"] is False
    assert result["blocking"] is True


def test_classical_detector_is_used_when_no_model_is_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(anti_spoof.settings, "ANTISPOOF_MODEL_ROOT", str(tmp_path))
    assert anti_spoof.assess_liveness(_natural_texture())["source"] == "classical"


def test_liveness_fails_open_when_the_detector_itself_errors(monkeypatch):
    """A bug in liveness must never be what locks a real student out."""
    monkeypatch.setattr(anti_spoof, "_centre_face_patch",
                        lambda image: (_ for _ in ()).throw(RuntimeError("boom")))

    result = anti_spoof.assess_liveness(_natural_texture())

    assert result["live"] is True
    assert result["source"] == "unavailable"


# ---------------------------------------------------------------------------
# Trained-model path
# ---------------------------------------------------------------------------

class _FakeInput:
    def __init__(self, shape, name="input"):
        self.shape = shape
        self.name = name


class _FakeSession:
    """Stands in for an onnxruntime session with a configurable output."""
    def __init__(self, output, shape=(1, 3, 80, 80)):
        self._output = np.asarray(output, dtype=np.float32)
        self._shape = shape
        self.received = None

    def get_inputs(self):
        return [_FakeInput(list(self._shape))]

    def run(self, _outputs, feed):
        self.received = next(iter(feed.values()))
        return [self._output]


def test_model_output_is_preferred_over_the_classical_detector(monkeypatch):
    # A 2-class head strongly asserting "live" (index 1) on an image the
    # classical detector would reject outright.
    session = _FakeSession([[-4.0, 4.0]])
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: session)

    result = anti_spoof.assess_liveness(np.full((400, 400, 3), 150, dtype=np.uint8))

    assert result["source"] == "model"
    assert result["live"] is True


def test_model_is_fed_its_own_declared_input_size_as_nchw_float(monkeypatch):
    session = _FakeSession([[0.1, 0.9]], shape=(1, 3, 128, 128))
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: session)

    anti_spoof.assess_liveness(_natural_texture())

    assert session.received.shape == (1, 3, 128, 128)
    assert session.received.dtype == np.float32
    assert 0.0 <= session.received.min() and session.received.max() <= 1.0


def test_dynamic_input_dimensions_fall_back_to_a_sane_default(monkeypatch):
    """Exports often leave the spatial dims symbolic ('height'/'width' strings
    or -1). That must not crash or produce a zero-sized resize."""
    session = _FakeSession([[0.2, 0.8]], shape=(1, 3, "height", "width"))
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: session)

    result = anti_spoof.assess_liveness(_natural_texture())

    assert session.received.shape == (1, 3, 80, 80)
    assert result["source"] == "model"


def test_three_class_minifasnet_head_reads_index_one_as_live(monkeypatch):
    """MiniFASNet's layout is [print-spoof, live, replay-spoof]."""
    spoof = _FakeSession([[5.0, 0.0, 1.0]])
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: spoof)
    assert anti_spoof.assess_liveness(_natural_texture())["live"] is False

    live = _FakeSession([[0.0, 5.0, 1.0]])
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: live)
    assert anti_spoof.assess_liveness(_natural_texture())["live"] is True


def test_single_logit_head_is_squashed_through_a_sigmoid(monkeypatch):
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: _FakeSession([3.0]))
    result = anti_spoof.assess_liveness(_natural_texture())
    assert result["live"] is True
    assert result["score"] == pytest.approx(0.953, abs=0.01)


def test_an_uninterpretable_model_output_degrades_to_classical(monkeypatch):
    """An empty output must not throw and must not silently pass everyone."""
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: _FakeSession(np.zeros((0,))))

    result = anti_spoof.assess_liveness(_natural_texture())

    assert result["source"] == "classical"


def test_live_threshold_is_configurable(monkeypatch):
    session = _FakeSession([[0.0, 0.4]])  # softmax -> ~0.60 live
    monkeypatch.setattr(anti_spoof, "_load_model", lambda: session)

    monkeypatch.setattr(anti_spoof.settings, "ANTISPOOF_LIVE_THRESHOLD", 0.5)
    assert anti_spoof.assess_liveness(_natural_texture())["live"] is True

    monkeypatch.setattr(anti_spoof.settings, "ANTISPOOF_LIVE_THRESHOLD", 0.9)
    assert anti_spoof.assess_liveness(_natural_texture())["live"] is False


def test_a_broken_model_file_does_not_take_down_liveness(monkeypatch, tmp_path):
    """Corrupt weights should log and fall back, not raise on every frame."""
    broken = tmp_path / "antispoof.onnx"
    broken.write_bytes(b"not an onnx file")
    monkeypatch.setattr(anti_spoof.settings, "ANTISPOOF_MODEL_ROOT", str(tmp_path))
    monkeypatch.setattr(anti_spoof.settings, "ANTISPOOF_MODEL_ONNX", "antispoof.onnx")

    result = anti_spoof.assess_liveness(_natural_texture())

    assert result["source"] == "classical"
