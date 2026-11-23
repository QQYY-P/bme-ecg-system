from queue import Queue
import threading
from types import SimpleNamespace
from pathlib import Path
import time
import unittest
import uuid

from contracts import SampleBatch, SamplePoint, DISCONTINUITY, INVALID
from inference_worker import InferenceClient


class FakeProcess:
    def __init__(self, **kwargs):
        self.alive = False
        self.exitcode = None

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        if hasattr(self, "on_join"):
            callback, self.on_join = self.on_join, lambda: None
            callback()
        self.alive = False
        self.exitcode = 0

    def terminate(self):
        self.alive = False
        self.exitcode = -1


class FakeContext:
    Queue = staticmethod(lambda maxsize: Queue(maxsize=maxsize))
    Value = staticmethod(lambda kind, value: SimpleNamespace(value=value))
    Event = staticmethod(threading.Event)
    Process = FakeProcess


def batch(start, count=36, segment=0, segment_start=None, first_flag=0, invalid=False):
    segment_start = start if segment_start is None else segment_start
    return SampleBatch("s", "synthetic", tuple(
        SamplePoint(start + i, segment, segment_start + i, (start + i) % 256,
                    65535 if invalid else 2048, (first_flag if i == 0 else 0) | (INVALID if invalid else 0))
        for i in range(count)), (start + count) / 360, "2026-10-26T00:00:00+00:00")


class InferenceQueueTests(unittest.TestCase):
    def make_client(self):
        client = InferenceClient(_context=FakeContext())
        client.start()
        return client

    def test_36_sample_chunks_are_aggregated_to_one_second(self):
        client = self.make_client()
        for start in range(0, 324, 36):
            self.assertTrue(client.submit(batch(start)))
        self.assertEqual(client._inputs.qsize(), 0)
        self.assertTrue(client.submit(batch(324)))
        message = client._inputs.get_nowait()
        self.assertEqual(len(message["raw"]), 360)
        self.assertEqual(message["start"], 0)
        self.assertEqual(message["source"], "synthetic")

    def test_boundaries_never_merge_old_partial_block(self):
        for flag, segment, segment_start in ((DISCONTINUITY, 0, 180), (0, 1, 0)):
            client = self.make_client()
            client.submit(batch(0, 180))
            client.submit(batch(180, 360, segment=segment, segment_start=segment_start, first_flag=flag))
            message = client._inputs.get_nowait()
            self.assertEqual(message["first_sample_id"], 180)
            self.assertEqual(len(message["raw"]), 360)
            self.assertEqual(message["generation"], 1)
            gaps = [x for x in client.poll() if x["type"] == "gap"]
            self.assertEqual(gaps[0]["samples"], 180)

    def test_input_overflow_changes_generation_and_reports_range(self):
        client = self.make_client()
        for start in range(0, 8 * 360, 360):
            self.assertTrue(client.submit(batch(start, 360)))
        self.assertFalse(client.submit(batch(2880, 360)))
        self.assertEqual(client._generation_value.value, 1)
        self.assertEqual(client._inputs.qsize(), 8)
        gap = next(x for x in client.poll() if x["type"] == "gap")
        self.assertEqual((gap["first_sample_id"], gap["last_sample_id"]), (0, 3239))
        self.assertEqual(client.status()["dropped_samples"], 3240)
        client._inputs.get_nowait()
        client.submit(batch(3240, 360))
        messages = [client._inputs.get_nowait() for _ in range(8)]
        self.assertEqual(messages[-1]["generation"], 1)
        self.assertEqual(messages[-1]["start"], 3240)

    def test_stale_results_do_not_update_new_generation(self):
        client = self.make_client()
        client.submit(batch(0, 360))
        client.submit(batch(360, 36, first_flag=DISCONTINUITY))
        client._outputs.put_nowait({"type": "result", "task_id": 1, "generation": 0,
                                   "processed_through": 359, "rows": [{"label": 1}]})
        events = client.poll()
        self.assertFalse(any(x["type"] == "result" for x in events))
        self.assertIsNone(client.status()["last_result"])

    def test_analysis_missing_index_and_invalid_data_reset(self):
        for next_batch in (batch(361, 360), batch(360, 36, invalid=True)):
            client = self.make_client()
            client.submit(batch(0, 360))
            client.submit(next_batch)
            self.assertEqual(client._generation, 1)
            self.assertTrue(any(x["type"] == "gap" for x in client.poll()))

    def test_worker_error_and_result_overflow_are_visible(self):
        client = self.make_client()
        client._outputs.put_nowait({"type": "error", "error": "missing model"})
        self.assertEqual(client.poll()[0]["error"], "missing model")
        self.assertEqual(client.status()["status"], "error")
        self.assertFalse(client.submit(batch(0)))
        second = self.make_client()
        second._feedback_fault.value = 1
        self.assertEqual(second.poll()[0]["type"], "error")

    def test_normal_stop_sends_partial_then_stop_without_losing_results(self):
        client = self.make_client()
        client.submit(batch(0, 36))
        client._outputs.put_nowait({"type": "result", "task_id": 0, "generation": 0,
                                   "processed_through": 35, "rows": [{"label": 0}]})
        client.stop()
        self.assertEqual(len(client._inputs.get_nowait()["raw"]), 36)
        self.assertEqual(client._inputs.get_nowait(), {"type": "stop", "normal_end": True})
        self.assertTrue(any(x["type"] == "result" for x in client.poll()))
        self.assertEqual(client.status()["status"], "stopped")

    def test_tail_created_during_worker_join_is_available_once_after_stop(self):
        client = self.make_client()
        client.submit(batch(0, 36))
        tail = {"type": "result", "task_id": 1, "generation": 0,
                "processed_through": 35,
                "rows": [{"label": 2, "tail": True, "peak_sample_id": 20}]}
        client._process.on_join = lambda: client._outputs.put_nowait(tail)
        client.stop(normal_end=True)
        results = [event for event in client.poll() if event["type"] == "result"]
        self.assertEqual(results, [tail])
        self.assertEqual(client.status()["last_result"], tail["rows"][0])
        self.assertEqual(client.poll(), [])


class SpawnFailureTests(unittest.TestCase):
    def test_missing_model_is_reported_by_real_spawned_process(self):
        missing = Path(__file__).parent / ("missing-model-" + uuid.uuid4().hex)
        client = InferenceClient(directory=missing)
        client.start()
        events = []
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                events.extend(client.poll())
                if any(event["type"] == "error" for event in events):
                    break
                time.sleep(.05)
            errors = [event["error"] for event in events if event["type"] == "error"]
            self.assertTrue(errors)
            self.assertIn("FileNotFoundError", errors[0])
            self.assertFalse(client.submit(batch(0)))
        finally:
            client.stop(normal_end=False, timeout=1)


if __name__ == "__main__":
    unittest.main()
