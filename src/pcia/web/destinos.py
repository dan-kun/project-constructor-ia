"""Validación de destinos de red para peticiones server-side.

El endpoint de descubrimiento de modelos hace un GET desde el servidor a una
URL que elige el visitante. Sin control, eso es un SSRF: en un despliegue
público cualquiera podría usar el servidor como proxy para alcanzar servicios
internos, no expuestos a internet (``localhost``, la red privada del proveedor
de hosting, o el endpoint de metadatos de la nube en ``169.254.169.254``, que
suele entregar credenciales de la instancia).

Regla aplicada: solo http/https, y el host debe resolver a direcciones
**públicas**. Se resuelve el nombre antes de decidir, porque un dominio
controlado por el atacante puede apuntar a una IP privada.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ESQUEMAS_PERMITIDOS = ("http", "https")


class DestinoNoPermitidoError(Exception):
    """La URL apunta a un destino que el servidor no debe alcanzar."""


def _es_publica(ip: str) -> bool:
    direccion = ipaddress.ip_address(ip)
    return not (
        direccion.is_private
        or direccion.is_loopback
        or direccion.is_link_local  # 169.254.0.0/16: metadatos de la nube
        or direccion.is_reserved
        or direccion.is_multicast
        or direccion.is_unspecified
    )


def validar_url_externa(url: str, *, permitir_privadas: bool = False) -> str:
    """Devuelve la URL si el servidor puede consultarla; si no, levanta.

    ``permitir_privadas`` habilita destinos locales para uso en la propia
    máquina (un Ollama en ``localhost`` es un caso legítimo cuando el
    servidor y el usuario son la misma persona). Debe quedar desactivado en
    un despliegue público.
    """
    partes = urlparse(url)
    if partes.scheme not in ESQUEMAS_PERMITIDOS:
        raise DestinoNoPermitidoError(
            f"Esquema no permitido: {partes.scheme or '(vacío)'}. "
            f"Solo se aceptan {' y '.join(ESQUEMAS_PERMITIDOS)}."
        )
    if not partes.hostname:
        raise DestinoNoPermitidoError("La URL no tiene host.")
    if permitir_privadas:
        return url

    try:
        # Todas las direcciones del host: si alguna es interna, se rechaza.
        infos = socket.getaddrinfo(partes.hostname, partes.port or 0)
    except socket.gaierror as exc:
        raise DestinoNoPermitidoError(
            f"No se pudo resolver el host {partes.hostname!r}: {exc}"
        ) from exc

    for info in infos:
        ip = info[4][0]
        if not _es_publica(ip):
            raise DestinoNoPermitidoError(
                f"El host {partes.hostname!r} resuelve a una dirección interna "
                f"({ip}). El servidor solo puede consultar destinos públicos."
            )
    return url
