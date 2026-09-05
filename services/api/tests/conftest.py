"""Global test safety guard: pytest must never select a live email provider."""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"
os.environ["EMAIL_DELIVERY_MODE"] = "fake"
os.environ["RESEND_API_KEY"] = ""
os.environ["EMAIL_FROM"] = ""
