"""Generate explicitly synthetic data using the real terminal recorder, then upload."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import threading
import time
import uuid

from uploader import Uploader, UploadError

ROOT = Path(__file__).resolve().parent
TERMINAL = ROOT.parent / '树莓派心电终端'


def create_demo(data_dir, seconds=30, on_block=None, live=False):
    if not 1 <= seconds <= 1800:
        raise ValueError('演示时长应为1至1800秒')
    sys.path.insert(0, str(TERMINAL))
    # Only these standard-library acquisition modules are used, never classifiers.
    from recording import Recorder
    from contracts import SampleBatch, SamplePoint
    sid = 'demo_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    stamp = lambda: datetime.now(timezone.utc).isoformat()
    recorder = Recorder(data_dir, {'session_id': sid, 'source': 'demo', 'started_utc': stamp(),
        'sample_rate_hz': 360, 'vref_v': 3.3, 'note': '本地合成演示，不是人体采集'}, flush_interval=0.1)
    recorder.start()
    directory = Path(data_dir) / 'sessions' / sid
    finished = False
    try:
        for second in range(seconds):
            points = []
            for i in range(360):
                sample = second * 360 + i
                t = sample / 360
                phase = (t % (60 / 72)) / (60 / 72)
                g = lambda center, width: math.exp(-((phase-center)/width)**2)
                value = 2048 + 80*g(.15,.04) - 110*g(.285,.015) + 750*g(.31,.012) - 200*g(.34,.02) + 165*g(.57,.07) + 12*math.sin(t*1.3)
                points.append(SamplePoint(sample, 0, sample, sample % 256, round(value), 0, 1))
            batch = SampleBatch(sid, 'demo', tuple(points), time.monotonic(), stamp())
            if not recorder.submit(batch):
                raise RuntimeError('演示记录器拒绝数据')
            deadline = time.monotonic() + 5
            while recorder.status()['saved'] < (second+1)*360:
                if time.monotonic() > deadline:
                    raise RuntimeError('演示记录保存超时')
                time.sleep(.02)
            if on_block:
                on_block(directory)
            if live:
                time.sleep(1)
        finished = True
    finally:
        recorder.finish(reason='demo_finished' if finished else 'demo_interrupted', complete=finished)
    if on_block:
        on_block(directory)
    return directory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds', type=int, default=30)
    p.add_argument('--url', default='http://127.0.0.1:8766')
    p.add_argument('--data-dir', type=Path, default=ROOT / 'demo_data')
    p.add_argument('--live', action='store_true', help='约每秒生成并上传一块，供页面演示')
    p.add_argument('--generate-only', action='store_true')
    args = p.parse_args()
    client = Uploader(args.url)
    def upload(directory):
        print(json.dumps(client.sync(directory), ensure_ascii=False), flush=True)
    pending = []
    stopped = threading.Event()
    def background_upload():
        while not stopped.is_set():
            if pending:
                try:
                    upload(pending[0])
                except (UploadError, OSError, ValueError) as exc:
                    print(f'待补传：{exc}', flush=True)
            stopped.wait(2)
    worker = None
    if args.live and not args.generate_only:
        worker = threading.Thread(target=background_upload, daemon=True)
        worker.start()
    try:
        directory = create_demo(args.data_dir, args.seconds,
            (lambda path: pending.append(path) if not pending else None) if worker else None, args.live)
    finally:
        stopped.set()
        if worker:
            worker.join()
    if not args.generate_only:
        upload(directory)
    print('合成演示记录：' + str(directory), flush=True)


if __name__ == '__main__':
    main()
