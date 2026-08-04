"""
Tests for the per-IP rate limiter (app/core/rate_limit.py) and its wiring
into POST /auth/login and POST /auth/register/student.

The unit tests call `rate_limit()` directly against a fake Request so window
timing can be exercised in milliseconds instead of the real 60-second
default. The end-to-end tests go through the actual HTTP endpoints, using
the suite's real configured default (10 requests / 60s window) -- fast
enough to run in-process without any sleep, since a loop of plain in-memory
requests finishes in well under a second.
"""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.rate_limit import rate_limit


def _fake_request(ip: str = "1.2.3.4"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_requests_under_the_limit_all_succeed():
    checker = rate_limit("unit-test-a", max_requests=3, window_seconds=60)
    request = _fake_request()
    for _ in range(3):
        checker(request)  # must not raise


def test_the_request_that_crosses_the_limit_is_rejected_with_429():
    checker = rate_limit("unit-test-b", max_requests=3, window_seconds=60)
    request = _fake_request()
    for _ in range(3):
        checker(request)

    with pytest.raises(HTTPException) as exc_info:
        checker(request)
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


def test_the_limit_is_tracked_separately_per_ip():
    checker = rate_limit("unit-test-c", max_requests=1, window_seconds=60)
    checker(_fake_request("1.1.1.1"))
    checker(_fake_request("2.2.2.2"))  # different IP, must not raise


def test_the_limit_is_tracked_separately_per_bucket():
    request = _fake_request("3.3.3.3")
    rate_limit("unit-test-d1", max_requests=1, window_seconds=60)(request)
    rate_limit("unit-test-d2", max_requests=1, window_seconds=60)(request)  # different bucket, must not raise


def test_the_limit_resets_once_the_window_elapses():
    checker = rate_limit("unit-test-e", max_requests=1, window_seconds=0.05)
    request = _fake_request()
    checker(request)
    with pytest.raises(HTTPException):
        checker(request)

    time.sleep(0.1)
    checker(request)  # window has elapsed, must not raise


def test_login_endpoint_returns_429_after_the_configured_limit(client, seed_roles):
    """End-to-end: confirms the dependency is actually wired into the route."""
    body = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(settings.AUTH_RATE_LIMIT_MAX_REQUESTS):
        response = client.post("/api/v1/auth/login", json=body)
        assert response.status_code != 429

    response = client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_register_endpoint_returns_429_after_the_configured_limit(client, seed_roles):
    def _payload(n):
        return {
            "first_name": "Rate",
            "last_name": "Limited",
            "email": f"ratelimited{n}@example.com",
            "password": "Str0ngPass!",
            "roll_number": f"R{n}",
        }

    for i in range(settings.AUTH_RATE_LIMIT_MAX_REQUESTS):
        response = client.post("/api/v1/auth/register/student", json=_payload(i))
        assert response.status_code != 429

    response = client.post("/api/v1/auth/register/student", json=_payload(999))
    assert response.status_code == 429


def test_rate_limits_on_login_and_register_are_independent(client, seed_roles):
    """Exhausting /auth/login must not affect /auth/register/student's budget."""
    body = {"email": "nobody2@example.com", "password": "wrong-password"}
    for _ in range(settings.AUTH_RATE_LIMIT_MAX_REQUESTS):
        client.post("/api/v1/auth/login", json=body)
    assert client.post("/api/v1/auth/login", json=body).status_code == 429

    response = client.post("/api/v1/auth/register/student", json={
        "first_name": "Still",
        "last_name": "Works",
        "email": "still-works@example.com",
        "password": "Str0ngPass!",
        "roll_number": "SW1",
    })
    assert response.status_code == 201
