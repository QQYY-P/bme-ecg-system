"""STM32 v2: AA 55 MODE SEQ ADC_H ADC_L SUM (seven bytes per sample)."""
from dataclasses import dataclass
from operator import index

BAUD_RATE = 115200
FRAME_BYTES = 7
FRAME_HEADER = b"\xaa\x55"
BLOCK_SAMPLES = 36
MODE_REAL = 0
MODE_SIMULATED = 1
PROTOCOL_NAME = "stm32-aa55-mode-seq8-adc16be-sum8-v2"


def encode_frame(sequence, value, mode):
    """Encode one legal v2 sample; never silently wrap invalid caller values."""
    sequence, value, mode = index(sequence), index(value), index(mode)
    if not 0 <= sequence <= 255:
        raise ValueError("序号必须在 0..255")
    if mode not in (MODE_REAL, MODE_SIMULATED):
        raise ValueError("模式必须为 0（真实）或 1（板端模拟）")
    if not StreamDecoder.legal(value):
        raise ValueError("ADC 值必须在 0..4095，或为 0xFFFF 无效标记")
    body = bytes((mode, sequence, value >> 8, value & 255))
    return FRAME_HEADER + body + bytes((sum(body) & 255,))


@dataclass(frozen=True)
class Sample:
    sequence: int
    value: int
    step: int
    discontinuity: bool
    mode: int | None = None
    resynchronized: bool = False

    @property
    def valid(self):
        return self.value != 0xFFFF


class StreamDecoder:
    """Search and validate v2 frames across arbitrary serial read boundaries.

    Corrupt candidates are rejected one byte at a time, then the header search
    resumes. A valid frame may start at any sequence number. ``discontinuity``
    counts only observed sequence anomalies; ``resynchronized`` also marks a
    stream break whose next sequence happens to be continuous. The additive
    checksum and 8-bit sequence cannot detect every possible transmission loss
    or corruption. ``ramp`` is retained for call compatibility and no longer
    changes alignment rules. Legacy three-byte frames are not auto-detected.
    """

    def __init__(self, ramp=False):
        self.ramp = ramp
        self.buffer = bytearray()
        self.locked = False
        self.fault = None  # Recoverable wire errors are counters, not latched faults.
        self.previous = None
        self.discarded = 0
        self.discontinuities = 0
        self.missing_mod256 = 0
        self.bad_frames = 0
        self.checksum_errors = 0
        self.resync_count = 0
        self._pending_resync = False

    @staticmethod
    def legal(value):
        return 0 <= value <= 4095 or value == 0xFFFF

    def _discard(self, count):
        if count:
            self.discarded += count
            self.locked = False
            if self.previous is not None:
                self._pending_resync = True

    def feed(self, data):
        self.buffer.extend(data)
        samples = []
        consumed = 0
        while consumed < len(self.buffer):
            offset = self.buffer.find(FRAME_HEADER, consumed)
            if offset < 0:
                # A final AA may be the first half of the next read's header.
                end = len(self.buffer) - int(self.buffer[-1] == FRAME_HEADER[0])
                self._discard(end - consumed)
                consumed = end
                break
            self._discard(offset - consumed)
            consumed = offset
            if len(self.buffer) - consumed < FRAME_BYTES:
                break
            mode, seq, high, low, checksum = self.buffer[consumed + 2:consumed + FRAME_BYTES]
            value = (high << 8) | low
            checksum_ok = checksum == ((mode + seq + high + low) & 255)
            if mode not in (MODE_REAL, MODE_SIMULATED) or not self.legal(value) or not checksum_ok:
                self.bad_frames += 1
                self.checksum_errors += int(not checksum_ok)
                self._discard(1)
                consumed += 1
                continue

            delta = 1 if self.previous is None else (seq - self.previous) & 255
            discontinuity = delta != 1
            if discontinuity:
                self.discontinuities += 1
                # Delta 0 may be duplication or a full-wrap loss, not 255 lost points.
                if delta > 1:
                    self.missing_mod256 += delta - 1
            samples.append(Sample(seq, value, max(1, delta), discontinuity,
                                  mode, self._pending_resync))
            self.resync_count += int(self._pending_resync)
            self._pending_resync = False
            self.previous = seq
            self.locked = True
            consumed += FRAME_BYTES
        del self.buffer[:consumed]
        return samples
