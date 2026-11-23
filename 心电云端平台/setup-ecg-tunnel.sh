#!/bin/sh
set -eu
id ecg-tunnel >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin ecg-tunnel
test "$(getent passwd ecg-tunnel | cut -d: -f6)" = /home/ecg-tunnel
install -d -m 700 -o ecg-tunnel -g ecg-tunnel /home/ecg-tunnel/.ssh
touch /home/ecg-tunnel/.ssh/authorized_keys
grep -qF 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOqKAQGN9WY8oExPB9975HIcOofZh6XUAlAgjTZlKyBI ecg-auto-link' /home/ecg-tunnel/.ssh/authorized_keys || printf '%s\n' 'restrict,port-forwarding,command="/bin/false" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOqKAQGN9WY8oExPB9975HIcOofZh6XUAlAgjTZlKyBI ecg-auto-link' >> /home/ecg-tunnel/.ssh/authorized_keys
chown ecg-tunnel:ecg-tunnel /home/ecg-tunnel/.ssh/authorized_keys
chmod 600 /home/ecg-tunnel/.ssh/authorized_keys
config=/etc/ssh/sshd_config.d/70-ecg-tunnel.conf
cat > "$config" <<'ECGCONFIG'
Match User ecg-tunnel
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:8766
    PermitListen none
    GatewayPorts no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    ForceCommand /bin/false
Match all
ECGCONFIG
chmod 644 "$config"
/usr/sbin/sshd -t || { rm -f "$config"; exit 1; }
systemctl reload ssh
echo ECG_RESTRICTED_TUNNEL_READY_ecg-tunnel
