#!/bin/sh
set -eu
id ecg-relay >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin ecg-relay
test "$(getent passwd ecg-relay | cut -d: -f6)" = /home/ecg-relay
install -d -m 700 -o ecg-relay -g ecg-relay /home/ecg-relay/.ssh
touch /home/ecg-relay/.ssh/authorized_keys
grep -qF 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILycENCcZK3OgDjIb139dVgSNVgjLaL4zpX/uBSfW2Xy ecg-auto-link' /home/ecg-relay/.ssh/authorized_keys || printf '%s\n' 'restrict,port-forwarding,command="/bin/false" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILycENCcZK3OgDjIb139dVgSNVgjLaL4zpX/uBSfW2Xy ecg-auto-link' >> /home/ecg-relay/.ssh/authorized_keys
chown ecg-relay:ecg-relay /home/ecg-relay/.ssh/authorized_keys
chmod 600 /home/ecg-relay/.ssh/authorized_keys
config=/etc/ssh/sshd_config.d/70-ecg-relay.conf
cat > "$config" <<'ECGCONFIG'
Match User ecg-relay
    ClientAliveInterval 15
    ClientAliveCountMax 3
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:8766
    PermitOpen none
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
echo ECG_RESTRICTED_TUNNEL_READY_ecg-relay
