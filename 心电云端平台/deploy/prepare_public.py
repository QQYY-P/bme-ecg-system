"""Generate reviewable IP HTTPS configs; never install them or contact a server."""
import argparse
import ipaddress
from pathlib import Path


def render(address):
    ip = ipaddress.ip_address(address)
    if ip.version != 4 or not ip.is_global: raise ValueError('请提供服务器的公网 IPv4 地址')
    ip = str(ip)
    challenge = 'location ^~ /.well-known/acme-challenge/ { root /var/www/ecg-acme; try_files $uri =404; }'
    bootstrap = f'server {{\n    listen 80;\n    server_name {ip};\n    {challenge}\n    location / {{ return 404; }}\n}}\n'
    nginx = f'''# Place in Nginx http context (sites-available). Backend remains loopback only.
limit_req_zone $binary_remote_addr zone=ecg_login:10m rate=10r/m;
server {{
    listen 80;
    server_name {ip};
    {challenge}
    location / {{ return 308 https://{ip}$request_uri; }}
}}
server {{
    listen 443 ssl;
    server_name {ip};
    ssl_certificate /etc/letsencrypt/live/{ip}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{ip}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 2m;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_read_timeout 30s;
    location = /api/auth/login {{
        limit_req zone=ecg_login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8766;
    }}
    location / {{ proxy_pass http://127.0.0.1:8766; }}
}}
'''
    service = '''[Service]
Environment=ECG_AUTH_REQUIRED=1
Environment=ECG_COOKIE_SECURE=1
Environment=ECG_PUBLIC_ACCESS=1
Environment=ECG_MODEL=cardioedge
'''
    hook = '#!/bin/sh\nset -eu\n/usr/sbin/nginx -t\n/bin/systemctl reload nginx\n'
    return {'nginx-bootstrap.conf':bootstrap, 'nginx-https.conf':nginx,
            'public-access.conf':service, 'reload-ecg-nginx.sh':hook}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    contents=render(args.ip)
    args.output.mkdir(parents=True,exist_ok=False)
    for name,content in contents.items(): (args.output/name).write_text(content,encoding='utf-8',newline='\n')
    print('已生成配置，未更改服务器。请按部署说明逐项安装并验证。')


if __name__=='__main__': main()
