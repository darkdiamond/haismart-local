"""Tests for the uSS local protocol (uss.py).

The status/handshake byte vectors are real wire structures, so these tests pin behaviour against
ground truth, not just internal self-consistency. The deviceId and localKey below are illustrative
placeholders — no real device credential is committed; the status blobs carry no secret (they are the
decrypted sensor readings).
"""

import pytest

from haismart_hrdp import uss

DEV = "A1B2C3D4E5F6"  # illustrative deviceId (real ones are the module's MAC)
LOCALKEY = "0123456789abcdef0123456789abcdef"  # illustrative — not a real device key

# HELLO the client sends (real 48-byte structure), and the AC's HELLO_RESP
REAL_HELLO = bytes.fromhex(
    "0000ea60002a01000000000100000000" + DEV.encode().hex() + "00" * 20
)
REAL_HELLO_RESP = bytes.fromhex("0000ea610012010000000001000089fc0000000100000004")
# real decrypted status blob (127B), typeId AAC1UKZ01
REAL_STATUS = bytes.fromhex(
    "00002715000000004e56010000030200000401" + "00" * 66
    + "2fffff2c000000000000066d010808c30002010007080000003c005e80000f" + "00" * 15 + "ae"
)


def test_hello_message_matches_hardware():
    assert uss.hello_message(DEV, sn=1, pro_ver=2) == REAL_HELLO
    assert len(REAL_HELLO) == 48


def test_decode_real_hello_resp():
    m = uss.decode_message(REAL_HELLO_RESP)
    assert m.info_code == 0xEA61
    assert m.info_type == uss.INFO_HELLO_RESP
    assert m.sn == 1               # AC echoes our sn
    assert m.session == 0x89FC     # AC-assigned session
    assert m.payload == bytes.fromhex("0000000100000004")  # status=1, localkey_ver=4


def test_hello_done_message():
    b = uss.hello_done_message(sn=2, session=0x89FC, pro_ver=2)
    m = uss.decode_message(b)
    assert m.info_type == uss.INFO_HELLO_DONE and m.info_code == 0xEA62
    assert m.sn == 2 and m.session == 0x89FC and m.payload == b""
    assert b == bytes.fromhex("0000ea62000a0100000000020000" "89fc")


def test_message_roundtrip():
    b = uss.encode_message(7, 42, b"hello-body", type_byte=0x6E, flag=1, session=0x1234)
    m = uss.decode_message(b)
    assert (m.info_type, m.sn, m.flag, m.session, m.payload) == (7, 42, 1, 0x1234, b"hello-body")


def test_split_messages():
    buf = uss.hello_message(DEV) + REAL_HELLO_RESP + uss.hello_done_message(2, 0x1)
    parts = list(uss.split_messages(buf))
    assert len(parts) == 3
    assert uss.decode_message(parts[1]).info_code == 0xEA61


def test_localkey_aes_key():
    assert uss.localkey_aes_key(LOCALKEY).hex() == \
        __import__("hashlib").md5(LOCALKEY.encode()).hexdigest()
    assert len(uss.localkey_aes_key(LOCALKEY)) == 16


def test_biz_roundtrip_and_integrity():
    data = b'\x00\x00\x27\x15\x00\x00\x00\x00status-bytes-here'
    ct = uss.biz_encrypt(0x11223344, data, LOCALKEY)
    # real biz payloads are AES-CBC ciphertext (16-multiple) + a 5-digit ASCII transport nonce trailer
    assert len(ct) % 16 == 5
    assert ct[-5:].isdigit()
    sn, out = uss.biz_decrypt(ct, LOCALKEY)
    assert sn == 0x11223344 and out == data


def test_biz_encrypt_reproduces_real_frame_byte_exact():
    # A biz-data frame (trailer nonce = "24225"): given the same nonce + fields, the
    # encoder must reproduce the ENTIRE payload incl. the 5-digit trailer the AC requires.
    inner = b'\x00\x00\x27\x14control-op-bytes'
    nonce = b"24225"
    ct = uss.biz_encrypt(7, inner, LOCALKEY, pre4=nonce)
    assert ct[-5:] == nonce                        # trailer appended
    # the plaintext pre4 is the trailer's first 4 digits
    pt = uss._cbc(uss.localkey_aes_key(LOCALKEY), ct[: (len(ct) // 16) * 16], decrypt=True)
    assert pt[38:42] == nonce[:4]
    sn, out = uss.biz_decrypt(ct, LOCALKEY)
    assert sn == 7 and out == inner


def test_biz_decrypt_wrong_key_raises():
    ct = uss.biz_encrypt(1, b"payload-data-1234", LOCALKEY)
    with pytest.raises(ValueError):  # MD5 check fails on a wrong/stale key — the re-pull signal
        uss.biz_decrypt(ct, "00" * 16)


def test_parse_status_container():
    c = uss.parse_status_container(REAL_STATUS)
    assert c.header == REAL_STATUS[:13]
    assert c.attr_region == REAL_STATUS[13:]
    assert c.raw == REAL_STATUS


# 127B full-status blobs (two units)
REAL_STATUS_DOWN = bytes.fromhex(
    "00002715000000004e560100000302000004010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002fffff2c000000000000066d010808c30002010007080000003c005e80000f00000000000000000000000000000000ae")
REAL_STATUS_UP = bytes.fromhex(
    "00002715000000004e560100000302000004010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002fffff2c000000000000066d010700220002000007080000003e005e80000300000000000000000000000000000000f9")


def test_parse_full_status_confirmed_fields():
    from haismart_hrdp import profile_for
    prof = profile_for("AAC1UKZ01")
    # Downstairs: ON, target 24, indoor 30.0, mode 6=fan_only, fan 3=low (byte[94]=0xc3),
    #   swing on (byte[93]=0x08)
    # secondary toggles read back from the same grSetDAC word block: both units have only the display
    # light on (lamp=True); health/strong/quiet/sleep off and eco=0 (computed from the real blobs)
    _toggles = {"health": False, "strong": False, "quiet": False, "sleep": False, "lamp": True, "eco": 0}
    d = uss.parse_full_status(REAL_STATUS_DOWN, prof)
    assert d == {"power": True, "target_temperature": 24.0, "current_temperature": 30.0,
                 "operation_mode": "6", "wind_speed": "3", "swing_vertical": True,
                 "outdoor_temperature": 30.0, **_toggles, "mode": "fan_only", "fan_mode": "low"}
    # Upstairs: OFF, target 23, indoor 31.0, mode 1=cool, fan 2=medium, swing off, outdoor 30
    u = uss.parse_full_status(REAL_STATUS_UP, prof)
    assert u == {"power": False, "target_temperature": 23.0, "current_temperature": 31.0,
                 "operation_mode": "1", "wind_speed": "2", "swing_vertical": False,
                 "outdoor_temperature": 30.0, **_toggles, "mode": "cool", "fan_mode": "medium"}
    # without a profile: raw STD codes only (still includes the secondary toggles)
    assert uss.parse_full_status(REAL_STATUS_UP) == {
        "power": False, "target_temperature": 23.0, "current_temperature": 31.0,
        "operation_mode": "1", "wind_speed": "2", "swing_vertical": False,
        "outdoor_temperature": 30.0, **_toggles}
    # non-full-status blob -> empty (no fabrication)
    assert uss.parse_full_status(b"\x00\x00\x27\x15short") == {}


def test_hello_v3_shape():
    b = uss.hello_message(DEV, sn=1, pro_ver=3, arg8=0, arg7=0)
    m = uss.decode_message(b)
    assert m.type_byte == 0x6E and len(m.payload) == 40  # deviceId[32] + arg8 + arg7


def test_hello_message_rejects_bad_pro_ver():
    with pytest.raises(ValueError):
        uss.hello_message(DEV, pro_ver=5)


class _FragSock:
    """A socket stub that hands back the buffer in small chunks, to exercise reassembly."""
    def __init__(self, data, chunk=5):
        self.data, self.chunk, self.i = data, chunk, 0

    def recv(self, _n):
        c = self.data[self.i:self.i + self.chunk]
        self.i += len(c)
        return c


def test_recv_message_reassembles_tcp_fragments():
    full = REAL_HELLO_RESP  # a real 24-byte HELLO_RESP, delivered 5 bytes at a time
    m = uss._recv_message(_FragSock(full, chunk=5))
    assert m.info_type == uss.INFO_HELLO_RESP and m.session == 0x89FC and len(m.payload) == 8


def test_recv_message_raises_on_early_close():
    with pytest.raises(RuntimeError):
        uss._recv_message(_FragSock(REAL_HELLO_RESP[:10], chunk=5))  # closes mid-message


# --- write/op path builders ---

def test_getallproperty_frame_matches_re():
    # The exact bytes from the wire model (the read-only probe frame). eppCmd 4D01, frameType 1.
    assert uss.getallproperty_epp_frame() == bytes.fromhex("ffff0a000000000000014d0159")
    assert uss.getallproperty_epp_frame()[10:12] == uss.EPP_CMD_GETALLPROPERTY  # 4D01, read-only


def test_epp_frame_checksum_reproduces_real_report():
    # Rebuild the real DOWN report frame (frameType 06, cmd 6D01, 34 data bytes) from its own data and
    # assert the builder reproduces it byte-exact — proving structure + the (len+payload)&0xFF checksum.
    real_frame = REAL_STATUS_DOWN[80:]                       # ff ff .. ae, 47 bytes
    data = real_frame[12:-1]                                 # after 00*6|06|6d01, before checksum
    assert uss.build_epp_frame(0x06, b"\x6d\x01", data) == real_frame
    assert real_frame[-1] == 0xAE                            # the real checksum
    # UP frame too (checksum 0xF9)
    up = REAL_STATUS_UP[80:]
    assert uss.build_epp_frame(0x06, b"\x6d\x01", up[12:-1]) == up and up[-1] == 0xF9


def test_grsetdac_set_to_current_matches_re():
    # grSetDAC (6001) with DOWN's live words1-5 -> the exact candidate frame in the wire model.
    words1_5 = REAL_STATUS_DOWN[80:][12:12 + 10]  # the 5 BE16 words after 06 6d01 in the real report
    assert words1_5 == bytes.fromhex("0808c300020100070800")
    assert uss.build_epp_frame(0x01, uss.EPP_CMD_GRSETDAC, words1_5) == \
        bytes.fromhex("ffff140000000000000160010808c3000201000708005b")


def test_epp_frame_rejects_bad_cmd_length():
    with pytest.raises(ValueError):
        uss.build_epp_frame(0x01, b"\x4d")  # eppCmd must be exactly 2 bytes


def test_cae_prefix_is_the_real_report_prefix():
    # CAE_REPORT_PREFIX must be byte-identical to bytes [0:78] of a real status blob (both units share it).
    assert uss.CAE_REPORT_PREFIX == REAL_STATUS_DOWN[:78] == REAL_STATUS_UP[:78]
    assert len(uss.CAE_REPORT_PREFIX) == 78
    assert uss.CAE_CONTAINER_HEADER == REAL_STATUS_DOWN[:13]


def test_cae_envelope_reproduces_real_status_blob():
    # Feeding the real report frame back through the envelope builder must reproduce the real blob.
    assert uss.build_cae_op_envelope(REAL_STATUS_DOWN[80:]) == REAL_STATUS_DOWN
    assert uss.build_cae_op_envelope(REAL_STATUS_UP[80:]) == REAL_STATUS_UP


def test_outbound_getallproperty_envelope_matches_findings():
    # The candidate outbound biz-data from the wire model §4b: prefix | 000d | getAllProperty.
    env = uss.build_cae_op_envelope(uss.getallproperty_epp_frame())
    assert env == uss.CAE_REPORT_PREFIX + bytes.fromhex("000d") + \
        bytes.fromhex("ffff0a000000000000014d0159")
    assert env[78:80] == bytes.fromhex("000d")  # frameLen BE16 = 13


def test_build_op_message_roundtrips_through_biz_and_framing():
    sn, session, info_type = 0x00000005, 0x1234, 0x64  # 0x64 -> info_code 0xEAC4 (a candidate)
    msg = uss.build_op_message(sn, uss.getallproperty_epp_frame(), LOCALKEY, session,
                               info_type=info_type)
    m = uss.decode_message(msg)
    assert m.info_type == info_type and m.info_code == 0xEA60 + info_type
    assert m.flag == uss.FLAG_BIZ_ENCRYPTED and m.session == session and m.sn == sn
    dec_sn, envelope = uss.biz_decrypt(m.payload, LOCALKEY)
    assert dec_sn == sn
    assert envelope == uss.build_cae_op_envelope(uss.getallproperty_epp_frame())


# --- outbound op — pinned to a real control frame ---------------------------------------
# A "set temperature +1" (grSetDAC) op. The inner EPP frame
# below is the exact on-wire command (no secret — it is the AC's own control bytes); the CAE envelope
# structure is pinned via a placeholder deviceId so no real device MAC is committed. This is the ground
# truth that unblocked the write path (the protocol).
REAL_GRSETDAC_EPP = bytes.fromhex("ffff160000000000000160010c0422000201000708000000bc")
REAL_GRSETDAC_WORDS = bytes.fromhex("0c0422000201000708000000")  # word[0]=0x0c = setpoint (was 0x0b)


def test_build_epp_frame_reproduces_real_grsetdac():
    # The positional frame + checksum rule reproduces a SET command exactly.
    frame = uss.build_epp_frame(0x01, uss.EPP_CMD_GRSETDAC, REAL_GRSETDAC_WORDS)
    assert frame == REAL_GRSETDAC_EPP
    assert frame[-1] == 0xBC                      # checksum (len + sum(payload)) & 0xFF
    assert frame[10:12] == uss.EPP_CMD_GRSETDAC   # eppCmd 0x6001 = grSetDAC


def test_build_cae_op_request_matches_real_envelope_structure():
    # Reconstruct the confirmed outbound CAE envelope for a placeholder device and pin its layout.
    env = uss.build_cae_op_request(REAL_GRSETDAC_EPP, DEV, counter=5)
    assert env[0:4] == bytes.fromhex("00002714")              # op type (reports are 0x2715)
    assert env[4:40] == b"\x00" * 36                          # reserved
    assert env[40:52] == DEV.encode() and env[52:72] == b"\x00" * 20  # 32-byte deviceId field
    assert env[72:76] == bytes.fromhex("00000005")            # counter BE32
    assert env[76:80] == bytes.fromhex("00000019")            # epplen BE32 = 25
    assert env[80:] == REAL_GRSETDAC_EPP


def test_build_op_request_message_roundtrips_and_pins_envelope():
    sn, session, counter = 0x00000223, 0x9C10, 5
    msg = uss.build_op_request_message(sn, REAL_GRSETDAC_EPP, LOCALKEY, session,
                                       device_id=DEV, counter=counter)
    m = uss.decode_message(msg)
    assert m.info_code == 0xEAC4 and m.flag == uss.FLAG_BIZ_ENCRYPTED
    assert m.session == session and m.sn == sn
    dec_sn, envelope = uss.biz_decrypt(m.payload, LOCALKEY)
    assert dec_sn == sn
    assert envelope == uss.build_cae_op_request(REAL_GRSETDAC_EPP, DEV, counter)


# grSetDAC field encoder — each (before, field, epp_value) -> after exercises one single-field
# transition across a temp/mode/fan/power/toggle sweep, so these exercise the bit map end to end.
@pytest.mark.parametrize("before,name,value,after", [
    ("0c0422000200000708000000", "onOffStatus",       1,    "0c0422000201000708000000"),  # power on
    ("0c0422000201000708000000", "windSpeed",         1,    "0c0421000201000708000000"),  # fan -> high
    ("0c0421000201000708000000", "targetTemperature", 11,   "0b0421000201000708000000"),  # 28 -> 27C
    ("0800050002030007080c0000", "operationMode",     2,    "0800450002030007080c0000"),  # auto -> dry
    ("090025000211000708000000", "healthMode",        1,    "090025000213000708000000"),  # health on
    ("090023000201000708000000", "rapidMode",         1,    "090023000209000708000000"),  # rapid on
    # eco-only + up/down-only transitions:
    ("0800230002030007080c0000", "ecoMode",           5,    "080023000203002f080c0000"),  # eco off -> L5
    ("0800230002030007080c0000", "ecoMode",           6,    "0800230002030037080c0000"),  # eco off -> L6
    ("080023000203002f080c0000", "ecoMode",           0,    "0800230002030007080c0000"),  # eco -> off
    ("0800230002030007080c0000", "windDirectionVertical", 0x0c, "080c230002030007080c0000"),  # up/down on
    ("080c230002030007080c0000", "windDirectionVertical", 0,    "0800230002030007080c0000"),  # up/down off
])
def test_set_grsetdac_field_reproduces_real_transitions(before, name, value, after):
    assert uss.set_grsetdac_field(bytes.fromhex(before), name, value) == bytes.fromhex(after)


def test_set_grsetdac_field_refuses_unmapped_fields():
    words = bytes.fromhex("0c0422000201000708000000")
    for unmapped in ("energySavingStatus", "lightStatus", "windDirectionHorizontal", "notARealAttr"):
        with pytest.raises(KeyError):
            uss.set_grsetdac_field(words, unmapped, 1)


def test_set_grsetdac_field_refuses_unobserved_values():
    # Values the app was never seen to send must be refused, even for mapped fields.
    words = bytes.fromhex("0800230002030007080c0000")
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "ecoMode", 3)             # 3 is not one of {0,5,6,7}
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "windDirectionVertical", 8)   # model's 8, but app uses 0x0c
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "operationMode", 3)       # 3 is not a valid mode
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "targetTemperature", 20)  # 20 -> 36 degC, out of 16..30 range


# --- values authorized by the DEVICE's own digital model (heat on a heat-pump unit) ---------------
# Our reference units are cooling-only, so heat (operationMode 4) is not in the observed allowlist.
# A device whose model declares the code may use it; a device that doesn't, may not.
def test_model_declared_mode_is_encodable_but_not_by_default():
    auto = bytes.fromhex("0800050002030007080c0000")            # operationMode = 0 (auto)
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(auto, "operationMode", 4)        # unauthorized: no model says so
    heat = uss.set_grsetdac_field(auto, "operationMode", 4, model_values={0, 1, 2, 4, 6})
    assert heat == bytes.fromhex("0800850002030007080c0000")     # mode bits (word2 b13) = 4
    # everything else in the group-set is untouched
    assert uss.set_grsetdac_field(heat, "operationMode", 0, model_values={0, 4}) == auto


def test_model_values_cannot_widen_device_specific_fields_or_overflow():
    words = bytes.fromhex("0800230002030007080c0000")
    # windDirectionVertical/ecoMode have no matching model attribute (this unit repurposes them), so
    # the model is not allowed to authorize values for them — the observed set stays the authority.
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "windDirectionVertical", 8, model_values={0, 8})
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "ecoMode", 1, model_values={0, 1})
    # a code that doesn't fit the field would silently corrupt neighbouring attributes
    with pytest.raises(ValueError):
        uss.set_grsetdac_field(words, "operationMode", 8, model_values={8})


# --- control (grSetDAC) baseline + field read/write pipeline (HA layer building blocks) -------------
def test_grsetdac_baseline_extracted_from_real_report():
    base = uss.grsetdac_baseline_from_status(REAL_STATUS_DOWN)
    assert len(base) == 12 and base == REAL_STATUS_DOWN[92:104]
    with pytest.raises(ValueError):
        uss.grsetdac_baseline_from_status(b"\x00\x00\x99\x99" + b"\x00" * 200)  # not a 0x2715 report


def test_read_grsetdac_field_agrees_with_parse_full_status():
    from haismart_hrdp import profile_for
    prof = profile_for("AAC1UKZ01")
    st = uss.parse_full_status(REAL_STATUS_DOWN, prof)
    assert uss.read_grsetdac_field(REAL_STATUS_DOWN, "targetTemperature") == st["target_temperature"] - 16
    assert uss.read_grsetdac_field(REAL_STATUS_DOWN, "operationMode") == int(st["operation_mode"])
    assert uss.read_grsetdac_field(REAL_STATUS_DOWN, "windSpeed") == int(st["wind_speed"])
    assert uss.read_grsetdac_field(REAL_STATUS_DOWN, "onOffStatus") == int(st["power"])


def test_control_pipeline_baseline_to_frame_preserves_other_fields():
    # Change ONLY the setpoint; every other read-back field must be unchanged (group-set safety).
    base = uss.grsetdac_baseline_from_status(REAL_STATUS_DOWN)
    before = {f: uss.read_grsetdac_field(REAL_STATUS_DOWN, f)
              for f in ("operationMode", "windSpeed", "onOffStatus")}
    new_words = uss.set_grsetdac_field(base, "targetTemperature", 26 - 16)  # -> 26 degC
    frame = uss.grsetdac_op_frame(new_words)
    assert frame[:2] == b"\xff\xff" and frame[10:12] == uss.EPP_CMD_GRSETDAC
    assert frame[-1] == (frame[2] + sum(frame[3:-1])) & 0xFF   # checksum holds
    # rebuild a report-shaped blob to read the fields back
    rebuilt = REAL_STATUS_DOWN[:92] + new_words + REAL_STATUS_DOWN[104:]
    assert uss.read_grsetdac_field(rebuilt, "targetTemperature") == 10
    for f, v in before.items():
        assert uss.read_grsetdac_field(rebuilt, f) == v


# --- async_send_op returns promptly after the reply burst (no full-timeout drain) -------------------
async def test_send_op_returns_promptly_after_reply_burst(monkeypatch):
    """The AC pushes its updated status right after the op, then holds the socket open and silent.
    async_send_op must return shortly after that burst (a short idle window), NOT block for the whole
    op ``timeout`` — the bug that made HA's state lag seconds behind the unit. It must still return the
    status blob so the caller can confirm the new state."""
    import asyncio
    import time

    SESSION = 0x1234
    hello_resp = uss.encode_message(uss.INFO_HELLO_RESP, 1, b"", session=SESSION)
    done_resp = uss.encode_message(
        uss.INFO_HELLO_DONE_RESP, 2, uss.biz_encrypt(0, (547).to_bytes(4, "big"), LOCALKEY),
        flag=uss.FLAG_BIZ_ENCRYPTED, session=SESSION,
    )
    status = uss.encode_message(
        0x64, 3, uss.biz_encrypt(547, REAL_STATUS_DOWN, LOCALKEY),
        flag=uss.FLAG_BIZ_ENCRYPTED, session=SESSION,
    )

    reader = asyncio.StreamReader()
    reader.feed_data(hello_resp + done_resp)  # handshake available up front

    class FakeWriter:
        def __init__(self) -> None:
            self.writes = 0

        def write(self, data: bytes) -> None:
            self.writes += 1
            if self.writes == 3:  # the op write -> the AC now emits its updated status, then falls silent
                reader.feed_data(status)

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def fake_open(ip, port):
        return reader, FakeWriter()

    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    t0 = time.monotonic()
    blobs = await uss.async_send_op(
        "1.2.3.4", DEV, LOCALKEY, REAL_GRSETDAC_EPP, counter=1, timeout=5.0
    )
    elapsed = time.monotonic() - t0

    assert REAL_STATUS_DOWN in blobs           # the updated state was captured
    assert elapsed < 2.5                        # returned ~_COLLECT_IDLE after the burst, not ~5s


async def test_send_op_build_frame_seeds_from_in_session_push(monkeypatch):
    """Single-session read-modify-write: build_frame is handed the AC's post-handshake status push as
    the baseline, so a control op seeds from live state without a separate read connection."""
    import asyncio

    SESSION = 0x1234
    hello_resp = uss.encode_message(uss.INFO_HELLO_RESP, 1, b"", session=SESSION)
    done_resp = uss.encode_message(
        uss.INFO_HELLO_DONE_RESP, 2, uss.biz_encrypt(0, (547).to_bytes(4, "big"), LOCALKEY),
        flag=uss.FLAG_BIZ_ENCRYPTED, session=SESSION,
    )
    push = uss.encode_message(  # the status the AC pushes right after the handshake
        0x64, 3, uss.biz_encrypt(547, REAL_STATUS_DOWN, LOCALKEY),
        flag=uss.FLAG_BIZ_ENCRYPTED, session=SESSION,
    )
    reader = asyncio.StreamReader()
    reader.feed_data(hello_resp + done_resp + push)

    class FakeWriter:
        def write(self, data: bytes) -> None: ...
        async def drain(self) -> None: ...
        def close(self) -> None: ...
        async def wait_closed(self) -> None: ...

    async def fake_open(ip, port):
        return reader, FakeWriter()

    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    seen: dict = {}

    def build(baseline):
        seen["baseline"] = baseline
        return REAL_GRSETDAC_EPP

    await uss.async_send_op("1.2.3.4", DEV, LOCALKEY, build_frame=build, counter=1, timeout=1.0)
    assert seen["baseline"] == REAL_STATUS_DOWN   # the in-session push became the seed baseline
