import argparse
import os
from pathlib import Path
import uvicorn
from server import create_app

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='心电记录、账号与后台分析服务')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and len(os.getenv('ECG_API_TOKEN', '')) < 16:
        parser.error('局域网监听需要先设置至少16位的ECG_API_TOKEN；本机使用无需设置')
    print(f'心电工作台监听 http://{args.host}:{args.port}；公网须通过 HTTPS 入口访问', flush=True)
    uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port, log_level='info',
                proxy_headers=True, forwarded_allow_ips='127.0.0.1,::1')
