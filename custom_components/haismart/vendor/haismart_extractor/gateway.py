"""Per-device localKey fetch over Haier's SE-Asia cloud **MQTT gateway**.

The per-device ``localKey`` is delivered over an MQTT 3.1.1 / TLS business channel to
``gw-sgp.haieriot.net:58702``. The gateway is authoritative — it returns the current key even when the
app's own cache is stale.

**Protocol**:

* CONNECT (MQTT 3.1.1 / TLS) to ``gw-sgp.haieriot.net:58702`` with :class:`GatewayCreds`.
* SUB ``Client/<clientId>/Business/Down``
* PUB ``Client/<clientId>/Business/Up`` (QoS 0), body :func:`localkey_request_payload`::

      {"type":"devLocalkey",
       "data": base64('{"sn":"<sn>","dev":"<deviceId>","flag":0}'),
       "tokens":["<accessToken>"]}

* RESP on ``.../Business/Down`` (:func:`parse_localkey_response`)::

      base64(data) -> {"sn":"<echoed>","errNo":0,"vers":<ver>,"key":"<localKey>"}

**Credentials.** All four CONNECT inputs are derivable:

* ``client_id`` — :func:`derive_client_id` = ``MD5(<uSDK CLIENTID> + "_" + <package>)`` (the uSDK
  ``CLIENTID`` is provisioned by the Login onboarding path). The gateway does **not** validate
  ``client_id`` at CONNECT — only the ``username``/``password`` pair — so any value connects; we derive it
  to match the app.
* ``access_token`` — an account accessToken; mint one from the reusable refreshToken via
  :meth:`haismart_extractor.cloud.HaierCloud.refresh_token`.
* ``username`` + ``password`` — :func:`derive_gateway_auth`. There is **no per-user secret** and no
  token/clientId in the pre-image — the pair is self-contained and self-verifying::

      username_body = 8 digits  (any 8 work)
      username      = "01" + username_body
      block         = BE16(len("haier_sdk")=9) + b"haier_sdk"    # zero-padded to a 16-byte boundary
      password      = hex( AES-128-CBC( key=MD5(username_body), iv=0, block ) )

  The gateway recomputes ``password`` from the sent username (stripping the ``"01"`` tag) and the global
  ``"haier_sdk"`` salt, so a freshly generated pair is accepted. Build creds with
  :meth:`GatewayCreds.derive`.

The MQTT connection is injectable (``connect=`` on :class:`GatewayClient`) so the request-build / response-
parse / sn-matching logic is unit-testable with a fake — no network. The default connection uses ``ssl``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import ssl
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .cloud import LocalKey

DEFAULT_HOST = "gw-sgp.haieriot.net"
DEFAULT_PORT = 58702
DEFAULT_PACKAGE = "com.haier.uhome.uplus.seasia"

#: Global salt used by the gateway-auth credential derivation; the same for every install/account —
#: the gateway uses it to recompute the CONNECT password from the username.
GATEWAY_AUTH_SALT = b"haier_sdk"
#: Fixed 2-char tag prepended to the username body on the wire.
GATEWAY_USERNAME_TAG = "01"


# --- credential derivation -----------------------------------------------------


def derive_client_id(usdk_client_id: str, package: str = DEFAULT_PACKAGE) -> str:
    """The MQTT clientId = ``MD5(<uSDK CLIENTID> + "_" + <package>)`` (lowercase hex).

    ``usdk_client_id`` is the per-install uSDK ``CLIENTID`` (32-hex, e.g.
    ``A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4``), provisioned by the Login onboarding path. Reproduces the
    app's real clientId byte-for-byte.
    """
    return hashlib.md5(f"{usdk_client_id}_{package}".encode()).hexdigest()


def derive_gateway_password(username_body: str, salt: bytes = GATEWAY_AUTH_SALT) -> str:
    """Compute the CONNECT ``password`` from the username body (lowercase 32-hex).

    The gateway-auth password derivation: the plaintext is a
    length-prefixed salt block ``BE16(len(salt)) + salt`` zero-padded to a 16-byte boundary, encrypted
    with **AES-128-CBC, IV=0** under ``key = MD5(username_body)``; the 16-byte ciphertext is hex-encoded.

    ``username_body`` is the username WITHOUT the ``"01"`` wire tag (the app derives the password from the
    body only, and the gateway strips the tag before recomputing).
    """
    block = len(salt).to_bytes(2, "big") + salt
    block += b"\x00" * (-len(block) % 16)  # pad up to a whole 16-byte block
    key = hashlib.md5(username_body.encode()).digest()
    enc = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    return (enc.update(block) + enc.finalize()).hex()


def generate_username_body(rng: secrets.SystemRandom | None = None) -> str:
    """A fresh 8-character username body (8 decimal digits, matching the app's format).

    The app builds it as ``"%d%d%d%d%d%d%d%d"`` over 8 random bytes truncated to 8 chars; the gateway
    doesn't care about the internal structure, only that ``password == f(body)``. Any 8 digits work.
    """
    r = rng or secrets.SystemRandom()
    return "".join(str(r.randrange(10)) for _ in range(8))


def derive_gateway_auth(username_body: str | None = None) -> tuple[str, str]:
    """Return a valid ``(username, password)`` CONNECT pair, fully derived.

    ``username`` is the wire username (``"01" + body``); ``password`` is derived from the body. If
    ``username_body`` is omitted a fresh random one is generated. Verified live (CONNACK rc=0).
    """
    body = username_body if username_body is not None else generate_username_body()
    return GATEWAY_USERNAME_TAG + body, derive_gateway_password(body)


# --- request / response codec ------------------------------------------------


def localkey_request_payload(
    device_id: str, access_token: str, *, sn: str | int, flag: int = 0
) -> str:
    """Build the exact ``Business/Up`` publish body (compact JSON, cJSON key order).

    The localKey request body: an inner ``{"sn","dev","flag"}`` object (``sn`` a
    STRING, ``flag`` a NUMBER) is base64-encoded into ``data``; ``sn`` is echoed in the response so it
    doubles as a request id.
    """
    inner = json.dumps({"sn": str(sn), "dev": device_id, "flag": flag}, separators=(",", ":"))
    data = base64.b64encode(inner.encode()).decode()
    body = {"type": "devLocalkey", "data": data, "tokens": [access_token]}
    return json.dumps(body, separators=(",", ":"))


def parse_localkey_response(payload: bytes | str) -> dict:
    """Decode a ``Business/Down`` message to its inner ``{"sn","errNo","vers","key"}`` dict.

    Returns ``{}`` for a message that is not a decodable localKey response (so a reader can skip
    unrelated pushes without raising)."""
    try:
        outer = json.loads(payload)
        if outer.get("type") != "devLocalkey" or "data" not in outer:
            return {}
        inner = json.loads(base64.b64decode(outer["data"]))
    except (ValueError, KeyError, TypeError):
        return {}
    return inner if isinstance(inner, dict) else {}


# --- credentials + result ------------------------------------------------------


@dataclass(frozen=True)
class GatewayCreds:
    """MQTT CONNECT credentials for the localKey gateway.

    Every field is derivable without (see module docstring): ``client_id`` via
    :func:`derive_client_id`, ``username``/``password`` via :func:`derive_gateway_auth`, and
    ``access_token`` minted from the reusable refreshToken. Use :meth:`derive` to build a fully-derived
    instance; the raw constructor stays available for supplying values directly.
    """

    client_id: str
    username: str
    password: str
    access_token: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def derive(
        cls,
        *,
        usdk_client_id: str,
        access_token: str,
        package: str = DEFAULT_PACKAGE,
        username_body: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> "GatewayCreds":
        """Build fully-derived creds — no stored username/password needed.

        ``client_id = MD5(usdk_client_id + "_" + package)`` and the ``username``/``password`` pair is
        generated by :func:`derive_gateway_auth` (fresh random body unless ``username_body`` is pinned).
        """
        username, password = derive_gateway_auth(username_body)
        return cls(
            client_id=derive_client_id(usdk_client_id, package),
            username=username,
            password=password,
            access_token=access_token,
            host=host,
            port=port,
        )

    @property
    def pub_topic(self) -> str:
        return f"Client/{self.client_id}/Business/Up"

    @property
    def sub_topic(self) -> str:
        return f"Client/{self.client_id}/Business/Down"


# --- MQTT connection abstraction (injectable for tests) ------------------------


class MqttConnection:
    """Minimal MQTT connection contract used by :class:`GatewayClient`.

    Tests inject a fake; :class:`_TlsMqttConnection` is the real one.
    """

    def subscribe(self, topic: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def publish(self, topic: str, payload: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def poll(self, timeout: float) -> list[tuple[str, bytes]]:  # pragma: no cover - interface
        """Return any PUBLISH messages received within ``timeout`` as ``(topic, payload)``."""
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


ConnectionFactory = Callable[[GatewayCreds], MqttConnection]


class GatewayError(Exception):
    pass


class GatewayClient:
    """Fetch per-device localKeys over the MQTT gateway.

    ``connect`` is a factory returning a live :class:`MqttConnection` for the given creds; it defaults to
    the real TLS connection but tests pass a fake.
    """

    def __init__(
        self, creds: GatewayCreds, *, connect: ConnectionFactory | None = None
    ) -> None:
        self.creds = creds
        self._connect = connect or _tls_connect

    def get_localkey(self, device_id: str, *, timeout: float = 8.0) -> LocalKey:
        """Fetch ``device_id``'s current localKey. Raises :class:`GatewayError` on no/failed response."""
        conn = self._connect(self.creds)
        try:
            conn.subscribe(self.creds.sub_topic)
            sn = str(int(time.time() * 1000) % 1_000_000_000)
            conn.publish(
                self.creds.pub_topic,
                localkey_request_payload(device_id, self.creds.access_token, sn=sn),
            )
            deadline = time.time() + timeout
            while time.time() < deadline:
                for _topic, pay in conn.poll(0.5):
                    inner = parse_localkey_response(pay)
                    if inner.get("key") and str(inner.get("sn")) == sn:
                        if int(inner.get("errNo", 0)) != 0:
                            raise GatewayError(f"gateway errNo={inner['errNo']} for {device_id}")
                        return LocalKey(key=str(inner["key"]), version=_as_int(inner.get("vers")))
            raise GatewayError(f"no localKey response for {device_id} within {timeout}s")
        finally:
            conn.close()

    def get_localkeys(self, device_ids: list[str], *, timeout: float = 8.0) -> dict[str, LocalKey]:
        """Fetch several devices' localKeys over one connection (skips ones that don't answer)."""
        out: dict[str, LocalKey] = {}
        conn = self._connect(self.creds)
        try:
            conn.subscribe(self.creds.sub_topic)
            for device_id in device_ids:
                sn = str((int(time.time() * 1000) + len(out)) % 1_000_000_000)
                conn.publish(
                    self.creds.pub_topic,
                    localkey_request_payload(device_id, self.creds.access_token, sn=sn),
                )
                deadline = time.time() + timeout
                while time.time() < deadline:
                    got = False
                    for _topic, pay in conn.poll(0.5):
                        inner = parse_localkey_response(pay)
                        if inner.get("key") and str(inner.get("sn")) == sn:
                            out[device_id] = LocalKey(
                                key=str(inner["key"]), version=_as_int(inner.get("vers"))
                            )
                            got = True
                            break
                    if got:
                        break
        finally:
            conn.close()
        return out


def get_localkey_via_gateway(
    creds: GatewayCreds,
    device_id: str,
    *,
    timeout: float = 8.0,
    connect: ConnectionFactory | None = None,
) -> LocalKey:
    """One-shot convenience: connect, fetch ``device_id``'s localKey, disconnect."""
    return GatewayClient(creds, connect=connect).get_localkey(device_id, timeout=timeout)


def _as_int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# --- default TLS MQTT connection (raw MQTT 3.1.1; no external deps) -------------


def _encode_len(n: int) -> bytes:
    out = b""
    while True:
        d = n % 128
        n //= 128
        out += bytes([d | 0x80]) if n > 0 else bytes([d])
        if n == 0:
            return out


def _mqtt_field(s: bytes | str) -> bytes:
    b = s.encode() if isinstance(s, str) else s
    return struct.pack(">H", len(b)) + b


class _TlsMqttConnection(MqttConnection):  # pragma: no cover - needs network
    """Raw MQTT 3.1.1 over TLS. Deliberately dependency-free (stdlib ``ssl`` only)."""

    def __init__(self, creds: GatewayCreds) -> None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((creds.host, creds.port), timeout=10)
        self.ss = ctx.wrap_socket(raw, server_hostname=creds.host)
        vh = b"\x00\x04MQTT\x04" + bytes([0x02 | 0x80 | 0x40]) + struct.pack(">H", 60)
        body = vh + _mqtt_field(creds.client_id) + _mqtt_field(creds.username) + _mqtt_field(creds.password)
        self.ss.sendall(b"\x10" + _encode_len(len(body)) + body)
        r = self.ss.recv(16)
        rc = r[3] if len(r) > 3 else -1
        if rc != 0:
            self.ss.close()
            raise GatewayError(f"CONNACK rc={rc} (creds rejected/stale)")
        self._pid = 0
        self._buf = b""

    def subscribe(self, topic: str) -> None:
        self._pid += 1
        body = struct.pack(">H", self._pid) + _mqtt_field(topic) + b"\x00"
        self.ss.sendall(b"\x82" + _encode_len(len(body)) + body)

    def publish(self, topic: str, payload: str) -> None:
        body = _mqtt_field(topic) + payload.encode()
        self.ss.sendall(b"\x30" + _encode_len(len(body)) + body)

    def poll(self, timeout: float) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        self.ss.settimeout(timeout)
        try:
            d = self.ss.recv(8192)
            if d:
                self._buf += d
        except (TimeoutError, socket.timeout):
            return out
        while len(self._buf) >= 2:
            mult = 1
            val = 0
            i = 1
            done = False
            while i < len(self._buf):
                b = self._buf[i]
                val += (b & 0x7F) * mult
                mult *= 128
                i += 1
                if not (b & 0x80):
                    done = True
                    break
            if not done:
                break
            total = i + val
            if len(self._buf) < total:
                break
            t = self._buf[0]
            pkt = self._buf[i:total]
            self._buf = self._buf[total:]
            if (t >> 4) == 3:  # PUBLISH
                tl = (pkt[0] << 8) | pkt[1]
                topic = pkt[2 : 2 + tl].decode("latin1")
                qos = (t >> 1) & 3
                payload = pkt[2 + tl + (2 if qos > 0 else 0):]
                out.append((topic, payload))
        return out

    def close(self) -> None:
        try:
            self.ss.close()
        except OSError:
            pass


def _tls_connect(creds: GatewayCreds) -> MqttConnection:  # pragma: no cover - needs network
    return _TlsMqttConnection(creds)
