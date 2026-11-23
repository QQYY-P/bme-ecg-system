"""Prepare separate, restricted tunnel identities; never stores passwords."""
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.cloud_private'


def prepare(name, user, forwarding, permission):
    PRIVATE.mkdir(exist_ok=True)
    key = PRIVATE / name
    if not key.exists():
        private = Ed25519PrivateKey.generate()
        key.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()))
        key.with_suffix('.pub').write_bytes(private.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH) + b' ecg-auto-link\n')
    public = key.with_suffix('.pub').read_text().strip()
    script = f'''#!/bin/sh
set -eu
id {user} >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin {user}
test "$(getent passwd {user} | cut -d: -f6)" = /home/{user}
install -d -m 700 -o {user} -g {user} /home/{user}/.ssh
touch /home/{user}/.ssh/authorized_keys
grep -qF '{public}' /home/{user}/.ssh/authorized_keys || printf '%s\\n' 'restrict,port-forwarding,command="/bin/false" {public}' >> /home/{user}/.ssh/authorized_keys
chown {user}:{user} /home/{user}/.ssh/authorized_keys
chmod 600 /home/{user}/.ssh/authorized_keys
config=/etc/ssh/sshd_config.d/70-{user}.conf
cat > "$config" <<'ECGCONFIG'
Match User {user}
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding {forwarding}
    {permission}
    GatewayPorts no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    ForceCommand /bin/false
Match all
ECGCONFIG
chmod 644 "$config"
/usr/sbin/sshd -t || {{ rm -f "$config"; exit 1; }}
systemctl reload ssh
echo ECG_RESTRICTED_TUNNEL_READY_{user}
'''
    (ROOT / f'setup-{user}.sh').write_text(script, encoding='utf-8', newline='\n')
    print(f'{user}: {public}')


if __name__ == '__main__':
    prepare('cloud_tunnel', 'ecg-tunnel', 'local', 'PermitOpen 127.0.0.1:8766\n    PermitListen none')
    prepare('pi_tunnel', 'ecg-relay', 'remote', 'PermitListen 127.0.0.1:8766\n    PermitOpen none')
