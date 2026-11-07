"""Shared ARQ connection contract for the API and both worker queues."""
from urllib.parse import unquote

from arq.connections import RedisSettings

from .config import Settings


def redis_settings(settings: Settings) -> RedisSettings:
    config = RedisSettings.from_dsn(settings.redis_url)
    if config.username is not None:
        config.username = unquote(config.username)
    if config.password is not None:
        config.password = unquote(config.password)
    if config.ssl:
        config.ssl_check_hostname = True
        config.ssl_ca_certs = settings.redis_ssl_ca_certs or None
    return config
