"""Active TCP/HTTP probes (spec 13.3-13.4). Async only; never blocks the HA loop.

The name is resolved here, every address is validated, and the socket connects to the
validated IP itself, so a DNS answer cannot redirect the probe (rebinding). HTTP never
follows redirects, reads at most 64 KiB and has a 3 s total budget. TLS is verified;
there is no option to skip certificate checks.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from dataclasses import dataclass

from ..modules.network.scheduler import Outcome
from ..modules.network.targets import TargetRejected, TargetSpec, check_resolved

CONNECT_TIMEOUT_S = 2.0
HTTP_TOTAL_TIMEOUT_S = 3.0
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(slots=True)
class ProbeResult:
    outcome: Outcome
    latency_ms: int | None = None
    address: str | None = None
    detail: str | None = None


async def resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), CONNECT_TIMEOUT_S)
    return [info[4][0] for info in infos]


_SSL_CONTEXT: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = ssl.create_default_context()  # verifies certificates and hostnames
    return _SSL_CONTEXT


async def probe(spec: TargetSpec, allowlist) -> ProbeResult:
    try:
        addresses = check_resolved(await resolve(spec.host, spec.port), allowlist,
                                   spec.allow_loopback)
    except TargetRejected as err:
        return ProbeResult(Outcome.BLOCKED, detail=err.reason)
    except (TimeoutError, OSError):
        return ProbeResult(Outcome.DNS_ERROR)
    address = addresses[0]
    start = time.monotonic()
    ssl_ctx = _ssl_context() if spec.kind == "https" else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(address, spec.port, ssl=ssl_ctx,
                                    server_hostname=spec.host if ssl_ctx else None),
            CONNECT_TIMEOUT_S)
    except ConnectionRefusedError:
        return ProbeResult(Outcome.REFUSED, address=address)
    except ssl.SSLError as err:
        # The host answered; the service is not usable with verified TLS.
        return ProbeResult(Outcome.REFUSED, address=address, detail=f"tls:{err.reason}")
    except (TimeoutError, OSError):
        return ProbeResult(Outcome.TIMEOUT, address=address)
    latency = int((time.monotonic() - start) * 1000)
    try:
        if spec.kind == "tcp":
            return ProbeResult(Outcome.OPEN, latency, address)
        return await asyncio.wait_for(_http(reader, writer, spec, latency, address),
                                      HTTP_TOTAL_TIMEOUT_S)
    except TimeoutError:
        return ProbeResult(Outcome.TIMEOUT, latency, address, "http_timeout")
    except (OSError, ValueError):
        return ProbeResult(Outcome.ERROR, latency, address, "http_error")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass


async def _http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, spec: TargetSpec,
                latency: int, address: str) -> ProbeResult:
    host = spec.host if ":" not in spec.host else f"[{spec.host}]"
    writer.write(f"GET {spec.path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: HA-Sentinel-probe\r\n"
                 "Accept: */*\r\nConnection: close\r\n\r\n".encode("ascii"))
    await writer.drain()
    head = await reader.read(MAX_RESPONSE_BYTES)  # bounded: never more than 64 KiB
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        return ProbeResult(Outcome.ERROR, latency, address, "not_http")
    # Any HTTP answer, including 3xx (never followed), proves the endpoint responds.
    return ProbeResult(Outcome.OPEN, latency, address, f"http_{parts[1]}")
