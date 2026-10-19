from gt86_remote_start.controller_protocol import (
    ControllerFault,
    ControllerState,
    ControllerStatus,
    Frame,
    FrameDecoder,
    MsgType,
    ReservationState,
    StartRequest,
    StatusFlag,
    crc16_ccitt,
    encode_frame,
)


def test_crc_known_vector():
    assert crc16_ccitt(b"123456789") == 0x29B1  # CRC-16/CCITT-FALSE check value


def test_round_trip_with_noise_and_split_bytes():
    req = StartRequest(0xDEADBEEF, 3000, 600)
    raw = encode_frame(Frame(MsgType.START_REQUEST, 7, req.pack()))
    dec = FrameDecoder()
    frames = dec.feed(b"\x00\x13garbage" + raw[:4])
    frames += dec.feed(raw[4:] + raw)
    assert len(frames) == 2
    assert StartRequest.unpack(frames[0].payload) == req
    assert frames[0].seq == 7


def test_corrupt_frame_dropped_and_counted():
    raw = bytearray(encode_frame(Frame(MsgType.HEARTBEAT, 1, b"\x01\x00\x00\x00\x02")))
    raw[7] ^= 0xFF
    dec = FrameDecoder()
    good = encode_frame(Frame(MsgType.HEARTBEAT, 2, b"\x01\x00\x00\x00\x02"))
    frames = dec.feed(bytes(raw) + good)
    assert [f.seq for f in frames] == [2]
    assert dec.crc_errors == 1


def test_status_round_trip_and_tristate():
    st = ControllerStatus(
        ControllerState.IDLE,
        ReservationState.VALID,
        StatusFlag.NEUTRAL_A | StatusFlag.PARKING_BRAKE,
        StatusFlag.NEUTRAL_A | StatusFlag.PARKING_BRAKE | StatusFlag.HOOD_CLOSED,
        ControllerFault.LOW_VOLTAGE,
        ControllerFault(0),
        0,
        12600,
        0,
        3,
    )
    back = ControllerStatus.unpack(st.pack())
    assert back == st
    assert back.has(StatusFlag.NEUTRAL_A) is True
    assert back.has(StatusFlag.HOOD_CLOSED) is False
    assert back.has(StatusFlag.NEUTRAL_B) is None  # invalid = unknown
