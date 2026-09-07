"""HTTP policy shared by speech provider adapters."""

from ipaddress import ip_address
from urllib.parse import urlparse


def should_trust_environment_proxy(url: str) -> bool:
    """Keep configured proxies for remote providers but bypass them for loopback."""

    hostname = urlparse(url).hostname
    if not hostname:
        return True
    if hostname.lower() == "localhost":
        return False
    try:
        return not ip_address(hostname).is_loopback
    except ValueError:
        return True
