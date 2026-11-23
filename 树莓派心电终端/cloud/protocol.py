"""Reader for the terminal's ECGRAW01 v1/v2 format; no model dependencies."""
import hashlib
import struct

FS = 360
HEADER = struct.Struct('<8sHHII')
ROWS = {1: struct.Struct('<QIIBHB'), 2: struct.Struct('<QIIBHBB')}


def decode_chunk(raw, expected_hash=None):
    if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('数据块校验值不一致')
    if len(raw) < HEADER.size:
        raise ValueError('数据块头不完整')
    magic, version, size, fs, count = HEADER.unpack_from(raw)
    row_format = ROWS.get(version)
    if (magic != b'ECGRAW01' or row_format is None or size != row_format.size
            or fs != FS or not 0 < count <= 36000
            or len(raw) != HEADER.size + count * size):
        raise ValueError('数据块格式、采样率或长度不正确')
    result = []
    for row in row_format.iter_unpack(raw[HEADER.size:]):
        row = row + (None,) if version == 1 else row[:-1] + (None if row[-1] == 255 else row[-1],)
        sample, segment, index, seq, adc, flags, mode = row
        if adc not in range(4096) and adc != 65535:
            raise ValueError('ADC超出支持范围')
        if flags & ~31 or mode not in (None, 0, 1):
            raise ValueError('质量标记或模式无效')
        if result and sample <= result[-1][0]:
            raise ValueError('采样编号必须严格递增')
        result.append(row)
    return result


def valid(row):
    return 0 < row[4] < 4095 and not row[5] & 3


def signal_origin(source, mode):
    if source == 'demo':
        return 'local_simulated'
    if source == 'serial':
        return {0: 'real_adc', 1: 'board_simulated'}.get(mode, 'unknown')
    return 'unknown'
