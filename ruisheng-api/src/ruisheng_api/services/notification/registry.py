"""通知器工厂注册表。build_notifier(provider, config) → INotifier。"""

from __future__ import annotations

from typing import Any, cast

from .base import INotifier
from .email import EmailNotifier
from .sms import CustomHttpSmsNotifier
from .voice import CustomHttpVoiceNotifier
from .wechat import WechatNotifier

_REGISTRY: dict[str, type] = {
    "wechat": WechatNotifier,
    "email": EmailNotifier,
    "sms_custom_http": CustomHttpSmsNotifier,
    "voice_custom_http": CustomHttpVoiceNotifier,
}


def build_notifier(provider: str, config: dict[str, Any]) -> INotifier:
    """
    Build a notifier from provider name and config dict.
    Config keys must match the notifier's __init__ kwargs.
    """
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(f"Unknown notification provider: {provider!r}")
    return cast(INotifier, cls(**config))


def list_providers() -> list[str]:
    return list(_REGISTRY)
