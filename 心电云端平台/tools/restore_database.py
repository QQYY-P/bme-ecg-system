"""Restore a server-local backup into a NEW directory, never over a live database."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database_backup import restore_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try: result = restore_backup(args.archive, args.destination)
    except Exception as exc: parser.exit(1, f'恢复未完成：{exc}\n')
    print(json.dumps(result, ensure_ascii=False))
    print('已恢复到新目录。登录会话和设备凭据已失效，需重新登录及发放凭据；分析结果需重新生成。尚未替换正式服务。')


if __name__ == '__main__': main()
