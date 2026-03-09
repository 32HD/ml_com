# NGC Container Setup (GPU + DPU)

This repository includes a hardened Docker Compose setup for NGC containers on this host.

## Files

- `docker-compose.ngc.yml`: container definitions
- `.env.ngc.example`: environment template
- `Dockerfile.ngc`: RDMA-enabled NGC derived image
- `scripts/ngc-build-rdma-image.sh`: build local RDMA image
- `scripts/ngc-up.sh`: start container (`safe` or `rdma`)
- `scripts/ngc-check.sh`: validate GPU and RDMA visibility

## First-time setup

```bash
cd /home/haodong_chen/ml_com
scripts/ngc-build-rdma-image.sh
scripts/ngc-up.sh safe
scripts/ngc-check.sh safe
```

## Optional RDMA host-network mode

```bash
scripts/ngc-up.sh rdma
scripts/ngc-check.sh rdma
```

## Enter container

```bash
docker compose --env-file .env.ngc -f docker-compose.ngc.yml exec ngc-safe bash
```

For RDMA profile:

```bash
docker compose --env-file .env.ngc -f docker-compose.ngc.yml exec ngc-rdma bash
```

## Security defaults

- Non-root runtime user (`UID:GID` from host user)
- Read-only root filesystem
- `no-new-privileges`
- `cap_drop: ALL`, only `IPC_LOCK` added back
- Limited PID count and explicit `/dev/infiniband` mapping

## Stop containers

```bash
docker compose --env-file .env.ngc -f docker-compose.ngc.yml --profile rdma down
```

## Use a private NGC image

If your target image is private, authenticate first:

```bash
docker login nvcr.io
```

Use:
- Username: `$oauthtoken`
- Password: your NGC API key
