# SOCKS5 до amoCRM через SSH на сервер, откуда amoCRM доступен (s4).
# Порт 1080 слушается только внутри сети compose, наружу не публикуется.
# Ключ и known_hosts монтируются в /keys с хоста (в репо их нет).
FROM alpine:3.20

RUN apk add --no-cache openssh-client

ENTRYPOINT ["sh", "-c", "exec ssh -N -D 0.0.0.0:1080 \
  -i /keys/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/keys/known_hosts \
  -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  \"$AMO_TUNNEL_SSH\""]
