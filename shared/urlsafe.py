"""Validación de URLs que el servidor descarga en nombre del usuario (anti-SSRF).

`POST /videos {url}` y `reference_url` hacen que el servidor pida una URL
arbitraria. Sin esto, `http://169.254.169.254/...` (metadata cloud) o
`http://localhost:6379` son alcanzables y la respuesta vuelve en `/scripts`.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


def check_public_url(url: str) -> str:
    """Devuelve la URL si es http(s) y TODAS sus IPs resueltas son públicas."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURLError(f"URL no permitida (solo http/https): {url!r}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURLError(f"no resuelve: {parsed.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURLError(f"{parsed.hostname} resuelve a IP no pública ({ip})")
    # ponytail: DNS rebinding (resolver distinto entre este check y el fetch)
    # sigue abierto; cerrarlo exige fijar la IP en el conector HTTP.
    return url
