import random
import unittest

from protocol import (BLOCK_SAMPLES, FRAME_BYTES, MODE_REAL, MODE_SIMULATED,
                      StreamDecoder, encode_frame)


def frames(pairs, mode=MODE_REAL):
    return b"".join(encode_frame(seq, value, mode) for seq, value in pairs)


class ProtocolTests(unittest.TestCase):
    def test_documented_vectors_are_decoded_without_using_encoder(self):
        vectors = [
            ("AA 55 00 05 08 00 0D", 0, 5, 2048),
            ("AA 55 01 05 08 00 0E", 1, 5, 2048),
            ("AA 55 01 FF FF FF FE", 1, 255, 65535),
            ("AA 55 00 FF 0F FF 0D", 0, 255, 4095),
        ]
        for wire, mode, sequence, value in vectors:
            with self.subTest(wire=wire):
                decoder = StreamDecoder()
                sample, = decoder.feed(bytes.fromhex(wire))
                self.assertEqual((sample.mode, sample.sequence, sample.value), (mode, sequence, value))
                self.assertFalse(sample.discontinuity)
                self.assertFalse(sample.resynchronized)
                self.assertTrue(decoder.locked)

    def test_encoder_matches_documented_vectors_and_rejects_invalid_input(self):
        self.assertEqual(encode_frame(5, 2048, 0), bytes.fromhex("AA 55 00 05 08 00 0D"))
        self.assertEqual(encode_frame(5, 2048, 1), bytes.fromhex("AA 55 01 05 08 00 0E"))
        self.assertEqual(encode_frame(255, 65535, 1), bytes.fromhex("AA 55 01 FF FF FF FE"))
        self.assertEqual(encode_frame(255, 4095, 0), bytes.fromhex("AA 55 00 FF 0F FF 0D"))
        for args in ((-1, 10, 0), (256, 10, 0), (0, -1, 0), (0, 4096, 0),
                     (0, 65536, 0), (0, 10, 2), (0, 10, -1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                encode_frame(*args)

    def test_every_split_position_accepts_arbitrary_starting_sequence(self):
        wire = frames(((i + 173) & 255, i) for i in range(300))
        for cut in range(len(wire) + 1):
            decoder = StreamDecoder(ramp=True)
            samples = decoder.feed(wire[:cut]) + decoder.feed(wire[cut:])
            self.assertEqual([s.value for s in samples], list(range(300)))
            self.assertEqual(decoder.discontinuities, 0)
            self.assertFalse(decoder.buffer)

    def test_one_byte_at_a_time_and_invalid_marker(self):
        wire = frames([(200, 2048), (201, 4095), (202, 0xFFFF), (203, 0)])
        decoder = StreamDecoder()
        samples = []
        for byte in wire:
            samples.extend(decoder.feed(bytes([byte])))
            self.assertLessEqual(len(decoder.buffer), 6)
        self.assertEqual(len(samples), 4)
        self.assertFalse(samples[2].valid)
        self.assertTrue(samples[1].valid)  # Saturation is a separate processing flag.
        self.assertIsNone(decoder.fault)

    def test_noise_and_partial_old_frame_do_not_mark_initial_sample_as_missing(self):
        decoder = StreamDecoder()
        noise = b"noise\xff\x08\xaa"
        samples = decoder.feed(noise + frames((i, 2000 + i) for i in range(17, 25)))
        self.assertEqual(len(samples), 8)
        self.assertEqual(decoder.discarded, len(noise))
        self.assertFalse(samples[0].discontinuity)
        self.assertFalse(samples[0].resynchronized)
        self.assertEqual(decoder.resync_count, 0)

    def test_modes_and_invalid_guard_are_carried_per_sample(self):
        decoder = StreamDecoder()
        wire = (encode_frame(10, 2048, MODE_REAL)
                + encode_frame(11, 65535, MODE_SIMULATED)
                + encode_frame(12, 2100, MODE_SIMULATED)
                + encode_frame(13, 65535, MODE_REAL))
        samples = decoder.feed(wire)
        self.assertEqual([s.mode for s in samples], [0, 1, 1, 0])
        self.assertEqual([s.valid for s in samples], [True, False, True, False])
        self.assertTrue(all(not s.discontinuity and not s.resynchronized for s in samples))

    def test_bad_checksum_recovers_and_preserves_both_valid_sides(self):
        decoder = StreamDecoder()
        bad = bytearray(encode_frame(31, 2300, 0))
        bad[-1] ^= 1
        samples = decoder.feed(encode_frame(30, 2200, 0) + bad + encode_frame(32, 2400, 1))
        self.assertEqual([s.sequence for s in samples], [30, 32])
        self.assertTrue(samples[1].discontinuity)
        self.assertTrue(samples[1].resynchronized)
        self.assertEqual(samples[1].step, 2)
        self.assertEqual(decoder.bad_frames, 1)
        self.assertEqual(decoder.checksum_errors, 1)
        self.assertEqual(decoder.resync_count, 1)
        self.assertEqual(decoder.discarded, FRAME_BYTES)
        self.assertIsNone(decoder.fault)
        self.assertTrue(decoder.locked)

    def test_invalid_mode_or_range_is_rejected_even_with_correct_checksum(self):
        # Checksums are fixed here independently of the encoder.
        bad_frames = (bytes.fromhex("AA 55 02 14 08 00 1E"),
                      bytes.fromhex("AA 55 00 14 10 00 24"))
        for bad in bad_frames:
            with self.subTest(bad=bad.hex()):
                decoder = StreamDecoder()
                samples = decoder.feed(bad + encode_frame(21, 2000, 0))
                self.assertEqual([s.sequence for s in samples], [21])
                self.assertEqual(decoder.bad_frames, 1)
                self.assertEqual(decoder.checksum_errors, 0)
                self.assertEqual(decoder.discarded, 7)

    def test_insertion_inside_each_frame_position_recovers(self):
        frame = encode_frame(22, 2300, 0)
        for position in range(1, FRAME_BYTES):
            with self.subTest(position=position):
                broken = frame[:position] + b"\xee" + frame[position:]
                decoder = StreamDecoder()
                samples = decoder.feed(encode_frame(21, 2200, 0) + broken + encode_frame(23, 2400, 0))
                self.assertEqual([s.sequence for s in samples], [21, 23])
                self.assertTrue(samples[1].resynchronized)
                self.assertTrue(samples[1].discontinuity)
                self.assertEqual(decoder.discarded, 8)

    def test_deletion_of_each_frame_byte_recovers(self):
        frame = encode_frame(22, 2300, 0)
        for position in range(FRAME_BYTES):
            with self.subTest(position=position):
                broken = frame[:position] + frame[position + 1:]
                decoder = StreamDecoder()
                samples = decoder.feed(encode_frame(21, 2200, 0) + broken + encode_frame(23, 2400, 0))
                self.assertEqual([s.sequence for s in samples], [21, 23])
                self.assertTrue(samples[1].resynchronized)
                self.assertTrue(samples[1].discontinuity)
                self.assertEqual(decoder.discarded, 6)

    def test_noise_break_is_reported_even_when_sequence_is_continuous(self):
        decoder = StreamDecoder()
        decoder.feed(encode_frame(70, 2000, 0))
        self.assertEqual(decoder.feed(b"garbage\xaa"), [])
        self.assertFalse(decoder.locked)
        samples = decoder.feed(b"\x55\x00\x47\x08\x00\x4f" + encode_frame(72, 2001, 0))
        self.assertEqual([s.sequence for s in samples], [71, 72])
        self.assertTrue(samples[0].resynchronized)
        self.assertFalse(samples[0].discontinuity)
        self.assertFalse(samples[1].resynchronized)
        self.assertEqual(decoder.resync_count, 1)
        self.assertEqual(decoder.discontinuities, 0)

    def test_fragmented_valid_header_does_not_destroy_lock(self):
        decoder = StreamDecoder()
        decoder.feed(encode_frame(70, 2000, 0))
        next_frame = encode_frame(71, 2001, 0)
        self.assertEqual(decoder.feed(next_frame[:1]), [])
        self.assertTrue(decoder.locked)
        self.assertEqual(decoder.feed(next_frame[1:6]), [])
        self.assertTrue(decoder.locked)
        sample, = decoder.feed(next_frame[6:])
        self.assertFalse(sample.resynchronized)
        self.assertEqual(decoder.discarded, 0)

    def test_gap_duplicate_and_wrap(self):
        decoder = StreamDecoder()
        samples = decoder.feed(frames([(0, 1), (1, 2), (2, 3), (3, 4),
                                      (6, 8), (6, 9), (255, 10), (0, 11)]))
        self.assertEqual(samples[4].step, 3)
        self.assertTrue(samples[5].discontinuity)
        self.assertEqual(samples[5].step, 1)
        self.assertFalse(samples[-1].discontinuity)
        self.assertEqual(decoder.discontinuities, 3)
        self.assertEqual(decoder.missing_mod256, 250)
        self.assertEqual(decoder.resync_count, 0)

    def test_unsynchronized_buffer_bounded(self):
        decoder = StreamDecoder()
        for suffix in (b"", b"\xaa", b"\xaa\x55\x01\x30\x08\x00"):
            for _ in range(10):
                self.assertFalse(decoder.feed(b"\xee" * 4096 + suffix))
                self.assertLessEqual(len(decoder.buffer), FRAME_BYTES - 1)
        self.assertFalse(decoder.locked)

    def test_legacy_stream_is_not_autodetected(self):
        wire = b"".join(bytes((i & 255, value >> 8, value & 255))
                        for i, value in enumerate(range(4096)))
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(wire), [])
        self.assertFalse(decoder.locked)
        self.assertLessEqual(len(decoder.buffer), 6)

    def test_dma_blocks_and_trailing_fragment(self):
        decoder = StreamDecoder()
        result = []
        for block in range(10):
            wire = frames(((block * 36 + i) & 255, 2000 + i) for i in range(36))
            self.assertEqual(len(wire), BLOCK_SAMPLES * FRAME_BYTES)
            result.extend(decoder.feed(wire))
        self.assertEqual(len(result), 360)
        tail = encode_frame(360 & 255, 2048, MODE_REAL)
        self.assertEqual(decoder.feed(tail[:6]), [])
        self.assertEqual(decoder.feed(tail[6:])[0].value, 2048)

    def test_random_fragmentation_has_same_results_as_one_read(self):
        wire = (frames((i & 255, 2000 + i % 32) for i in range(350))
                + b"noise\xaa\x55\x02\x00\x08\x00\x0a"
                + frames(((i + 350) & 255, 2100 + i % 32) for i in range(350)))
        reference = StreamDecoder()
        expected = reference.feed(wire)
        for seed in range(10):
            rng = random.Random(seed)
            decoder, actual, offset = StreamDecoder(), [], 0
            while offset < len(wire):
                count = rng.randint(1, 277)
                actual.extend(decoder.feed(wire[offset:offset + count]))
                offset += count
            self.assertEqual(actual, expected)
            self.assertEqual(decoder.discarded, reference.discarded)
            self.assertEqual(decoder.bad_frames, reference.bad_frames)
            self.assertEqual(decoder.resync_count, reference.resync_count)
            self.assertFalse(decoder.buffer)


if __name__ == "__main__":
    unittest.main()
