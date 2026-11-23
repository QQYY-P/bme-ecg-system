"""Local-screen ECG application and the same pipeline's headless verification entrypoint."""
import argparse
import json
import logging
import multiprocessing
import os
import time
from pathlib import Path


def parser():
    result = argparse.ArgumentParser(description="STM32 → 树莓派心电波形终端")
    source = result.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true", help="合成波形演示，非实采")
    source.add_argument("--port", help="STM32 串口设备，或 auto 自动选择唯一 USB 串口")
    source.add_argument("--replay", help="回放本程序导出的 CSV")
    result.add_argument("--headless", action="store_true", help="无图形会话运行，用于验证")
    result.add_argument("--duration", type=float, help="运行秒数；模拟模式按360Hz生成相应点数")
    result.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    result.add_argument("--fullscreen", action="store_true")
    result.add_argument("--standby", action="store_true", help="设备待机界面；点击开始才采集，来源参数仅作预选")
    result.add_argument("--geometry", default="1000x620")
    result.add_argument("--blas-threads", type=int, choices=(1, 2), default=1)
    result.add_argument("--summary", type=Path, help="保存运行摘要 JSON，通常与 --headless 配合")
    result.add_argument("--list-ports", action="store_true", help="列出串口、USB属性和稳定设备路径")
    result.add_argument("--no-classification", action="store_true", help="仅采集显示，关闭实验性三分类")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.standby and args.headless:
        parser().error("待机需要本地界面，不能与 --headless 同用")
    if args.list_ports:
        from serial_connection import list_serial_ports
        print(json.dumps(list_serial_ports(), ensure_ascii=False, indent=2))
        return 0
    if args.duration is not None and (not 0 < args.duration <= 86400):
        parser().error("duration 必须在0～86400秒之间")
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = str(args.blas_threads)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from controller import Controller
    controller = Controller(args.data_dir, classification_enabled=False if args.no_classification else None)
    controller.initialize_environment()
    source = "demo" if args.demo else "serial" if args.port else "replay" if args.replay else None

    def start():
        if source:
            controller.start(source=source, port=args.port, replay_path=args.replay,
                             duration=args.duration if args.demo else None)

    if args.headless:
        if not source:
            parser().error("headless 模式须指定 --demo、--port 或 --replay")
        try:
            start()
        except (ValueError, OSError, RuntimeError) as exc:
            logging.error("无法启动采集：%s", exc)
            controller.shutdown_environment()
            return 2
        started = time.monotonic()
        try:
            while controller.snapshot()["running"]:
                if args.duration and not args.demo and time.monotonic() - started >= args.duration:
                    controller.stop()
                time.sleep(.1)
        except KeyboardInterrupt:
            controller.stop()
        finally:
            controller.stop()
            stopped = controller.wait(15)
            controller.shutdown_environment()
        if not stopped:
            logging.error("停止未完成，请检查保存状态")
            return 2
        summary = controller.snapshot()
        summary.pop("wave", None)
        summary.update(host_platform=os.uname().machine if hasattr(os, "uname") else os.name,
                       blas_threads=args.blas_threads, synthetic=True if source == "demo" else None)
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["error"] else 0

    import tkinter as tk
    from ui.main_window import MainWindow, SOURCE_NAMES
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        logging.error("无法打开本地屏幕：%s。请从图形会话启动，或用 --headless 验证。", exc)
        controller.shutdown_environment()
        return 2
    root.geometry(args.geometry)
    view = MainWindow(root, controller, simple_mode=args.standby)
    if args.fullscreen:
        root.attributes("-fullscreen", True)
    if source:
        view.source.set(SOURCE_NAMES[source])
        if source == "serial":
            view.port.set(args.port)
        elif source == "replay":
            view._replay_path = args.replay
        if not args.standby:
            root.after(300, lambda: view.start(duration=args.duration))
    try:
        root.mainloop()
    finally:
        controller.stop()
        controller.wait(15)
        controller.shutdown_environment()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
