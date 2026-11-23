import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from acquisition import Acquisition, PointBuilder
from contracts import INVALID, SATURATED, DISCONTINUITY, MODE_CHANGE, STREAM_RESYNC
from protocol import Sample, FRAME_BYTES, encode_frame


class AcquisitionTests(unittest.TestCase):
    def test_unconnected_or_unrecognized_serial_stream_is_explained(self):
        for data, expected in ((b"", "no_data"), (b"text,not-binary\n", "sync_wait")):
            with self.subTest(expected=expected):
                events, now = [], [0.0]
                a = Acquisition("serial-test", "serial", lambda batch: True,
                                lambda kind, detail: events.append((kind, detail)), port="test-device")
                port = mock.MagicMock()
                def read(_):
                    now[0] += .1
                    if now[0] > 3.5:
                        a.stop()
                    return data
                port.read.side_effect = read
                port.__enter__.return_value = port
                with mock.patch("acquisition.open_serial_port", return_value=port), \
                     mock.patch("acquisition.time.monotonic", side_effect=lambda: now[0]):
                    a.run()
                self.assertIsNone(a.error)
                self.assertEqual(sum(kind == expected for kind, _ in events), 1)
                self.assertFalse(a.decoder.locked)

    def test_repeated_sequence_starts_new_segment_even_with_step_one(self):
        b = PointBuilder()
        p = b.build([Sample(1, 2000, 1, False), Sample(1, 2100, 1, True)])
        self.assertEqual([x.sample_id for x in p], [0, 1])
        self.assertEqual([x.segment_index for x in p], [0, 0])
        self.assertNotEqual(p[0].segment_id, p[1].segment_id)
        self.assertTrue(p[1].flags & DISCONTINUITY)

    def test_invalid_saturated_and_timeout_boundaries(self):
        b = PointBuilder()
        p = b.build([Sample(0, 2000, 1, False), Sample(1, 65535, 1, False),
                     Sample(2, 4095, 1, False), Sample(3, 2200, 1, False)])
        self.assertTrue(p[1].flags & INVALID)
        self.assertTrue(p[2].flags & SATURATED)
        self.assertFalse(p[1].valid)
        self.assertNotEqual(p[0].segment_id, p[3].segment_id)
        b.break_segment()
        q = b.build([Sample(4, 2200, 1, False)])[0]
        self.assertNotEqual(q.segment_id, p[-1].segment_id)
        self.assertEqual(q.segment_index, 0)

    def test_demo_uses_wire_decoder_and_exact_sample_count(self):
        batches = []
        a = Acquisition("test", "demo", lambda b: batches.append(b) or True,
                        lambda *args: None, duration=.2)
        a.start(); a.join(3)
        self.assertFalse(a.is_alive())
        self.assertIsNone(a.error)
        self.assertEqual(sum(len(b.points) for b in batches), 72)
        self.assertEqual(sum(len(b.wire) for b in batches), 72 * FRAME_BYTES)
        self.assertTrue(all(b.source == "demo" for b in batches))

    def test_replay_preserves_segment_breaks_and_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.csv"
            path.write_text("sequence,raw_adc,segment_id\n0,2000,0\n1,2001,1\n2,7000,1\n", encoding="utf-8")
            batches = []
            a = Acquisition("test", "replay", lambda b: batches.append(b) or True,
                            lambda *args: None, replay_path=path)
            a.run()
            self.assertIn("非法", a.error)
            self.assertEqual(batches[0].points[0].raw_adc, 2000)
            self.assertEqual(sum(len(b.points) for b in batches), 2)
            self.assertNotEqual(batches[0].points[0].segment_id, batches[-1].points[-1].segment_id)

    def test_negative_replay_value_is_rejected_before_submitting_the_valid_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "negative.csv"
            path.write_text("sequence,raw_adc\n0,2000\n1,-1\n", encoding="utf-8")
            batches = []
            a = Acquisition("test", "replay", lambda b: batches.append(b) or True,
                            lambda *args: None, replay_path=path)
            a.run()
            self.assertIn("非法 ADC", a.error)
            self.assertEqual(a.finished_reason, "source_error")
            self.assertEqual([p.raw_adc for b in batches for p in b.points], [2000])
            self.assertEqual(b"".join(b.wire for b in batches), b"")
            self.assertIsNone(batches[0].points[0].mode)

    def test_mode_change_without_guard_separates_segments(self):
        b = PointBuilder()
        p = b.build([Sample(24, 2000, 1, False, 0),
                     Sample(25, 2001, 1, False, 1),
                     Sample(26, 2002, 1, False, 1)])
        self.assertEqual([x.mode for x in p], [0, 1, 1])
        self.assertEqual([x.segment_id for x in p], [0, 1, 1])
        self.assertEqual([x.segment_index for x in p], [0, 0, 1])
        self.assertEqual(p[1].flags, MODE_CHANGE)
        resumed = b.build([Sample(27, 2003, 1, False, 1, True)])[0]
        self.assertEqual(resumed.flags, STREAM_RESYNC)
        self.assertEqual(resumed.segment_id, 2)

    def test_decoder_groups_modes_and_preserves_corrupted_wire_once(self):
        batches, events = [], []
        a = Acquisition("test", "serial", lambda b: batches.append(b) or True,
                        lambda kind, detail: events.append((kind, detail)))
        bad = bytearray(encode_frame(83, 2500, 1)); bad[-1] ^= 1
        wire = (encode_frame(81, 2000, 0) + encode_frame(82, 65535, 1)
                + bytes(bad) + encode_frame(84, 2100, 1))
        a._decode(wire)
        points = [p for batch in batches for p in batch.points]
        self.assertEqual([p.sequence for p in points], [81, 82, 84])
        self.assertEqual([p.mode for p in points], [0, 1, 1])
        self.assertEqual(b"".join(b.wire for b in batches), wire)
        self.assertTrue(points[1].flags & MODE_CHANGE)
        self.assertTrue(points[2].flags & STREAM_RESYNC)
        self.assertTrue(points[2].flags & DISCONTINUITY)
        self.assertEqual([d['mode'] for k, d in events if k == 'mode_changed'], [0, 1])
        self.assertTrue(any(k == 'protocol_resync' and d['locked'] for k, d in events))

    def test_bad_trailing_frame_clears_alignment_until_recovery(self):
        events = []
        a = Acquisition("test", "serial", lambda b: True,
                        lambda k, d: events.append((k, d)))
        bad = bytearray(encode_frame(12, 2000, 0)); bad[-1] ^= 1
        a._decode(encode_frame(11, 2000, 0) + bad)
        self.assertTrue(any(k == 'protocol_resync' and not d['locked'] for k, d in events))
        self.assertIsNone(a._board_mode)
        a._decode(encode_frame(13, 2000, 0))
        self.assertTrue(a.decoder.locked)
        self.assertEqual(a._board_mode, 0)

    def test_corruption_split_across_reads_preserves_wire_and_recovers_same_mode(self):
        batches, events = [], []
        a = Acquisition("test", "serial", lambda b: batches.append(b) or True,
                        lambda k, d: events.append((k, d)))
        bad = bytearray(encode_frame(91, 2300, 1)); bad[-1] ^= 1
        next_frame = encode_frame(92, 2400, 1)
        chunks = [encode_frame(90, 2200, 1) + bad[:4],
                  bytes(bad[4:]) + next_frame[:3], next_frame[3:]]
        self.assertTrue(a._decode(chunks[0]))
        self.assertTrue(a.decoder.locked)
        self.assertTrue(a._decode(chunks[1]))
        self.assertFalse(a.decoder.locked)
        self.assertIsNone(a._board_mode)
        self.assertTrue(a._decode(chunks[2]))
        self.assertTrue(a.decoder.locked)
        self.assertEqual(a._board_mode, 1)
        points = [p for batch in batches for p in batch.points]
        self.assertEqual([p.sequence for p in points], [90, 92])
        self.assertTrue(points[-1].flags & STREAM_RESYNC)
        self.assertNotEqual(points[0].segment_id, points[1].segment_id)
        self.assertEqual(b"".join(batch.wire for batch in batches), b"".join(chunks))
        self.assertEqual([detail["locked"] for kind, detail in events if kind == "protocol_resync"],
                         [False, True])
        statistics = [detail for kind, detail in events if kind == "protocol_stats"]
        self.assertEqual(statistics[-1]["checksum_errors"], 1)
        self.assertEqual(statistics[-1]["resync_count"], 1)

    def test_serial_stop_on_downstream_rejection_emits_no_later_events(self):
        for wire in (b"noise", encode_frame(80, 2000, 0) + encode_frame(81, 2100, 1) + b"noise"):
            with self.subTest(wire=wire.hex()):
                callbacks = []
                def reject(batch):
                    callbacks.append(("batch", batch))
                    return False
                a = Acquisition("test", "serial", reject,
                                lambda kind, detail: callbacks.append((kind, detail)), port="test-port")
                port = mock.MagicMock()
                port.__enter__.return_value = port
                port.read.return_value = wire
                with mock.patch("acquisition.open_serial_port", return_value=port):
                    a.run()
                self.assertIsNone(a.error)
                self.assertEqual(a.finished_reason, "downstream_stopped")
                self.assertEqual(port.read.call_count, 1)
                kinds = [kind for kind, _ in callbacks]
                self.assertEqual(kinds.count("batch"), 1)
                self.assertEqual(kinds[kinds.index("batch") + 1:], ["source_finished"])
                self.assertEqual(next(value.wire for kind, value in callbacks if kind == "batch"), wire)

    def test_timeout_discards_partial_frame_even_after_corruption_unlocked_decoder(self):
        for corrupt_before_pause in (False, True):
            with self.subTest(corrupt_before_pause=corrupt_before_pause):
                events, batches, now = [], [], [0.0]
                bad = bytearray(encode_frame(11, 2100, 0)); bad[-1] ^= 1
                prefix = (bytes(bad) if corrupt_before_pause else b"") + b"\xaa\x55\x01"
                timeline = iter(((.1, encode_frame(10, 2000, 0)), (.2, prefix),
                                 (2.4, b""), (2.5, encode_frame(80, 2400, 1))))
                a = Acquisition("test", "serial", lambda b: batches.append(b) or True,
                                lambda kind, detail: events.append((kind, detail)), port="test-port")
                port = mock.MagicMock()
                port.__enter__.return_value = port
                def read(_):
                    try:
                        now[0], wire = next(timeline)
                        return wire
                    except StopIteration:
                        a.stop()
                        return b""
                port.read.side_effect = read
                with mock.patch("acquisition.open_serial_port", return_value=port), \
                     mock.patch("acquisition.time.monotonic", side_effect=lambda: now[0]):
                    a.run()
                self.assertIsNone(a.error)
                timeouts = [detail for kind, detail in events if kind == "timeout"]
                self.assertEqual(len(timeouts), 1)
                self.assertEqual(timeouts[0]["discarded_partial_bytes"], 3)
                self.assertEqual(sum(kind == "resumed" for kind, _ in events), 1)
                points = [p for batch in batches for p in batch.points]
                self.assertEqual([p.sequence for p in points], [10, 80])
                self.assertNotEqual(points[0].segment_id, points[1].segment_id)
                self.assertEqual([p.mode for p in points], [0, 1])
                self.assertEqual(b"".join(batch.wire for batch in batches),
                                 encode_frame(10, 2000, 0) + prefix + encode_frame(80, 2400, 1))

    def test_replay_preserves_modes_and_rejects_unknown_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modes.csv"
            path.write_text("sequence,raw_adc,mode\n10,2000,0\n11,2100,1\n12,2200,2\n", encoding="utf-8")
            batches = []
            a = Acquisition("test", "replay", lambda b: batches.append(b) or True,
                            lambda *args: None, replay_path=path)
            a.run()
            self.assertIn("回放模式", a.error)
            points = [p for b in batches for p in b.points]
            self.assertEqual([p.mode for p in points], [0, 1])
            self.assertNotEqual(points[0].segment_id, points[1].segment_id)
            self.assertEqual(b"".join(b.wire for b in batches), b"")

    def test_all_replay_flush_paths_follow_the_sample_clock(self):
        for mode in ("segments", "blank_rows", "full_batches"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "paced.csv"
                lines = ["sequence,raw_adc,segment_id"]
                for i in range(73):
                    lines.append(f"{i},2000,{i if mode == 'segments' else 0}")
                    if mode == "blank_rows":
                        lines.append(",,")
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                now, emissions = [0.0], []
                a = Acquisition("test", "replay",
                                lambda b: emissions.append((now[0], b)) or True,
                                lambda *args: None, replay_path=path)

                def advance(timeout):
                    now[0] += timeout
                    return False

                with mock.patch("acquisition.time.monotonic", side_effect=lambda: now[0]), \
                     mock.patch.object(a.stop_event, "wait", side_effect=advance):
                    a.run()
                self.assertIsNone(a.error)
                self.assertEqual(a.finished_reason, "replay_complete")
                self.assertEqual(sum(len(b.points) for _, b in emissions), 73)
                self.assertEqual([len(b.points) for _, b in emissions], [36, 36, 1])
                points = [p for _, b in emissions for p in b.points]
                expected_segments = list(range(73)) if mode != "full_batches" else [0] * 73
                self.assertEqual([p.segment_id for p in points], expected_segments)
                self.assertTrue(all(not (p.flags & DISCONTINUITY) for p in points))
                self.assertAlmostEqual(now[0], 73 / 360)
                for emitted_at, batch in emissions:
                    self.assertAlmostEqual(emitted_at, batch.points[0].sample_id / 360)

    def test_replay_stops_immediately_after_downstream_rejects_a_flush(self):
        rows = {
            "segments": "".join(f"{i},2000,{i}\n" for i in range(36)) + "36,-1,36\n",
            "blank_rows": "".join(f"{i},2000,0\n,,\n" for i in range(36)) + "36,-1,0\n",
            "full_batches": "".join(f"{i},2000,0\n" for i in range(36)) + "36,-1,0\n",
        }
        for mode, content in rows.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "rejected.csv"
                path.write_text("sequence,raw_adc,segment_id\n" + content, encoding="utf-8")
                batches = []

                def reject(batch):
                    batches.append(batch)
                    return False

                a = Acquisition("test", "replay", reject, lambda *args: None, replay_path=path)
                a.run()
                self.assertIsNone(a.error)
                self.assertEqual(a.finished_reason, "downstream_stopped")
                self.assertEqual(len(batches), 1)
                self.assertEqual(a.builder.sample_id, 36)

    def test_stopping_with_a_partial_batch_preserves_samples_and_segment_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stop.csv"
            path.write_text("raw_adc\n", encoding="utf-8")
            batches = []
            a = Acquisition("test", "replay", lambda b: batches.append(b) or True,
                            lambda *args: None, replay_path=path)

            def rows():
                yield {"raw_adc": "2000", "sequence": "0", "segment_id": "0"}
                yield {"raw_adc": "2001", "sequence": "1", "segment_id": "1"}
                a.stop()
                yield {"raw_adc": "-1", "sequence": "2", "segment_id": "1"}

            reader = mock.MagicMock()
            reader.fieldnames = ["raw_adc"]
            reader.__iter__.return_value = rows()
            with mock.patch("acquisition.csv.DictReader", return_value=reader):
                a.run()
            self.assertIsNone(a.error)
            self.assertEqual(a.finished_reason, "stopped")
            self.assertEqual(len(batches), 1)
            self.assertEqual([p.raw_adc for p in batches[0].points], [2000, 2001])
            self.assertEqual([p.segment_id for p in batches[0].points], [0, 1])


if __name__ == "__main__":
    unittest.main()
