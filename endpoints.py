"""Адреса API языковых сервисов. Пользователь может указать свой адрес (прокси/зеркало) в настройках —
поля openai_base_url, anthropic_base_url, gemini_base_url в config.json. Пусто — официальный адрес."""
DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
CUSTOM = {}


def configure(cfg):
    for k in DEFAULTS:
        CUSTOM[k] = (cfg.get(k + "_base_url") or "").strip().rstrip("/")


def base(name):
    return CUSTOM.get(name) or DEFAULTS[name]


def is_custom(name):
    return bool(CUSTOM.get(name))
