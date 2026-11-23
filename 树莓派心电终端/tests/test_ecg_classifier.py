"""验证训练/部署一致性、串口分包不变性和断流状态隔离。"""
import unittest
from pathlib import Path
import numpy as np
from ecg_classifier import (AcquisitionClassifier, FixedFrameStream, HistoryRR,
                            Predictor, preprocess_windows)


def fake_predictor(windows, rr):
    """状态测试仅检查输入与时间，不借助随机模型输出判定正确性。"""
    return np.tile([0.6, 0.3, 0.1], (len(windows), 1))


def synthetic(seconds=30):
    """生成含 QRS、慢漂移和 50 Hz 干扰的 ADC 测试波形。"""
    times = np.arange(int(seconds * 360)) / 360
    phase = times % 0.85
    signal = (2048 + 900 * np.exp(-((phase - 0.23) / 0.015) ** 2)
              - 230 * np.exp(-((phase - 0.27) / 0.02) ** 2)
              + 120 * np.exp(-((phase - 0.48) / 0.07) ** 2)
              + 50 * np.sin(2 * np.pi * times * 0.5)
              + 100 * np.sin(2 * np.pi * times * 50))
    return times, signal


class ClassifierTests(unittest.TestCase):
    def test_rr_uses_only_prior_history(self):
        rr = HistoryRR()
        np.testing.assert_array_equal(rr.step(100), [1, 1])
        np.testing.assert_array_equal(rr.step(400), [1, 1])
        np.testing.assert_array_equal(rr.step(550), [0.5, 1])
        np.testing.assert_allclose(rr.step(1000), [2, 150 / 225])

    def test_normalization_matches_positive_gain_offset_and_constant(self):
        wave = np.sin(np.arange(180) / 15)[None, :]
        np.testing.assert_allclose(preprocess_windows(wave), preprocess_windows(wave * 600 + 2048), atol=1e-6)
        np.testing.assert_array_equal(preprocess_windows(np.ones((1, 180))), np.zeros((1, 180)))

    def test_no_output_before_context_and_no_repeated_peaks(self):
        _, signal = synthetic()
        stream = FixedFrameStream(fake_predictor)
        self.assertEqual(stream.push(signal[:4319]), [])
        rows = stream.push(signal[4319:4320])
        self.assertTrue(rows)
        self.assertTrue(all(2 <= r["latency_s"] <= 12 for r in rows))
        rows += stream.push(signal[4320:]) + stream.flush()
        peaks = [r["peak"] for r in rows]
        self.assertEqual(peaks, sorted(set(peaks)))
        self.assertEqual(stream.flush(), [])

    def test_packet_size_does_not_change_features(self):
        _, signal = synthetic()
        outputs = []
        for size in (1, 36, 720, len(signal)):
            stream = FixedFrameStream(fake_predictor)
            rows = []
            for start in range(0, len(signal), size):
                rows += stream.push(signal[start:start + size], start=start)
            rows += stream.flush()
            outputs.append([(row["peak"], row["rr"], row["proba"]) for row in rows])
        self.assertTrue(outputs[0])
        self.assertTrue(all(item == outputs[0] for item in outputs))

    def test_gap_invalid_and_saturation_restart_without_bridging(self):
        times, signal = synthetic(30)
        for invalid in (np.nan, 0, 4095):
            values = signal.copy()
            values[3600] = invalid
            engine = AcquisitionClassifier(fake_predictor, use_notch=False)
            rows = engine.process(times, values) + engine.flush()
            reference = AcquisitionClassifier(fake_predictor, use_notch=False)
            expected = reference.process(times[3601:], signal[3601:]) + reference.flush()
            self.assertEqual(rows, expected)
            self.assertGreater(rows[0]["peak"], 3601 + 90)
            self.assertEqual(rows[0]["rr"], [1, 1])

    def test_time_jump_and_notch_warmup(self):
        times, signal = synthetic(30)
        engine = AcquisitionClassifier(fake_predictor)
        self.assertEqual(engine.process(times[:720], signal[:720]), [])
        self.assertIsNone(engine.stream)
        engine.process(times[720:2000], signal[720:2000])
        engine.process(times[3000:3300], signal[3000:3300])
        self.assertIsNone(engine.stream)
        self.assertEqual(engine.notch.segment_samples, 300)

    def test_constant_input_does_not_produce_classifications(self):
        engine = AcquisitionClassifier(fake_predictor)
        self.assertEqual(engine.process(np.arange(10800) / 360, np.full(10800, 2048)), [])
        self.assertEqual(engine.flush(), [])

    def test_original_research_stream_reference(self):
        fixture = np.load(Path(__file__).parent / "fixtures" / "stream_reference.npz")
        captured = []
        def capture(wave, rr):
            captured.extend(zip(wave.copy(), rr.copy()))
            return fake_predictor(wave, rr)
        stream = FixedFrameStream(capture)
        signal = fixture["signal"]
        rows = []
        for index in range(0, len(signal), 36):
            rows += stream.push(signal[index:index + 36])
        rows += stream.flush()
        np.testing.assert_array_equal([r["peak"] for r in rows], fixture["peaks"])
        np.testing.assert_array_equal(np.array([item[0] for item in captured]), fixture["morph"])
        np.testing.assert_array_equal(np.array([item[1] for item in captured]), fixture["rr"])

    def test_bundled_weights_match_saved_training_probabilities(self):
        fixture = np.load(Path(__file__).parent / "fixtures" / "model_reference.npz")
        probabilities = Predictor()(fixture["morph"], fixture["rr"])
        # 严格比对原架构 CPU 输出；原训练 GPU 数值另作跨设备误差检查。
        np.testing.assert_allclose(probabilities, fixture["proba_cpu"], atol=2e-5, rtol=2e-5)
        np.testing.assert_allclose(probabilities, fixture["proba"], atol=5e-4, rtol=0)
        np.testing.assert_array_equal(probabilities.argmax(1), fixture["proba"].argmax(1))
        np.testing.assert_allclose(probabilities.sum(1), 1, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
