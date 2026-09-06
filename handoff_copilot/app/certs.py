"""Windows-only fix for corporate/AV TLS-inspection proxies (e.g. AVG,
Kaspersky, Zscaler) that re-sign HTTPS traffic with a locally-installed root
certificate. That root lives in the Windows certificate store, which plain
``certifi``-based HTTP clients (httpx, and therefore the ``pinecone`` SDK)
never consult, so their TLS handshakes fail with CERTIFICATE_VERIFY_FAILED
even though the OS itself trusts the connection.

We do NOT monkeypatch ``ssl.SSLContext`` globally (e.g. via ``pip-system-certs``
/ ``truststore.inject_into_ssl()``): the ``anthropic`` SDK already vendors its
own ``truststore``-based transport that reads the Windows store natively, and
layering a second global patch on top of it causes infinite recursion in
``SSLContext.verify_mode`` (verified empirically). Instead we build a plain
merged PEM file once (certifi's bundle + every cert in the Windows ROOT/CA
stores) and hand its path to individual clients via ``cafile=``/``ssl_ca_certs=``.
This only affects the clients we explicitly pass it to.
"""
from __future__ import annotations

import ssl
import sys
from pathlib import Path

import certifi

_CACHE_PATH = Path(__file__).resolve().parents[1] / ".cache" / "merged_ca_bundle.pem"


def _build_merged_bundle() -> Path:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    base = Path(certifi.where()).read_text(encoding="utf-8")

    seen: set[str] = set()
    extra: list[str] = []
    for store_name in ("ROOT", "CA"):
        try:
            entries = ssl.enum_certificates(store_name)
        except (AttributeError, OSError):
            continue
        for cert_der, encoding, _trust in entries:
            if encoding != "x509_asn":
                continue
            pem = ssl.DER_cert_to_PEM_cert(cert_der)
            if pem not in seen:
                seen.add(pem)
                extra.append(pem)

    with _CACHE_PATH.open("w", encoding="utf-8") as out:
        out.write(base)
        if not base.endswith("\n"):
            out.write("\n")
        out.write("\n".join(extra))

    return _CACHE_PATH


def get_ca_bundle_path() -> str | None:
    """Path to a CA bundle that trusts the OS store, or None to use defaults.

    Only does anything on Windows, where TLS-inspecting security software is
    the common case. Rebuilt lazily and cached on disk; delete the cache file
    to force a rebuild (e.g. after the intercepting proxy rotates its root).
    """
    if sys.platform != "win32":
        return None
    if not _CACHE_PATH.exists():
        _build_merged_bundle()
    return str(_CACHE_PATH)
