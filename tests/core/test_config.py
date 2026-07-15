"""Las variables del seam de identidad existen con sus defaults."""
from __future__ import annotations

from app.core.config import settings


def test_auth_mode_defaults_local():
    assert settings.AUTH_MODE == "local"


def test_local_dev_user_default():
    assert settings.LOCAL_DEV_USER == "dev@local"


def test_local_dev_optional_fields_default_empty():
    assert settings.LOCAL_DEV_USERNAME == ""
    assert settings.LOCAL_DEV_DISPLAY_NAME == ""
