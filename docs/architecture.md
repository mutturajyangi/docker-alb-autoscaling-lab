# Architecture

## Request flow

1. The client sends a request to `localhost:8080`, the only published port (like the ALB's public DNS name).
2. Nginx picks a target from the `app_servers` upstream pool using round-robin.
3. The pool is built from the DNS name `app`. Docker's embedded DNS (`127.0.0.11`) returns one IP
   per running replica, and Nginx re-resolves it every 5 seconds (`server app:8000 resolve`).
4. The FastAPI replica replies with its container ID (hostname) and IP.
5. Nginx returns the response and adds an `X-Upstream-Server` header naming the backend.

## Auto scaling loop

Every `CHECK_INTERVAL` (5s) the autoscaler does the following:

1. Lists running containers labelled `com.docker.compose.project=lb-lab` and `com.docker.compose.service=app`.
2. Reads each one's CPU from the Docker stats API, normalised to its limit
   (`cpus: 0.5`, so 100% means the replica is using its whole half-CPU).
3. Averages the CPU across replicas, like the CloudWatch `CPUUtilization` average over an ASG.
4. Decides what to do:
   - avg > `SCALE_OUT_CPU` for `SCALE_OUT_PERIODS` checks → +1 replica (up to `MAX_REPLICAS`)
   - avg < `SCALE_IN_CPU` for `SCALE_IN_PERIODS` checks → −1 replica (down to `MIN_REPLICAS`)
   - no action during `COOLDOWN` after a scaling event
5. Applies the change with
   `docker compose up -d --no-recreate --no-build --no-deps --scale app=N app`,
   so Compose stays in charge of the containers and `docker compose down` cleans everything up.

Scale-in is deliberately slower than scale-out (6 checks vs 3). This avoids "flapping" and
mirrors how AWS policies scale out quickly and scale in conservatively.

## Health checks

| Layer  | Mechanism | AWS equivalent |
|--------|-----------|----------------|
| Docker | `HEALTHCHECK` calls `/health` every 10s; Compose waits for `service_healthy` before starting the LB | EC2 status checks |
| Nginx  | `max_fails=2 fail_timeout=10s`: after 2 failed attempts a target is skipped for 10s | Target group unhealthy threshold |
| Nginx  | `proxy_next_upstream`: a failed request is retried on the next target | ALB routing around unhealthy targets |

## Differences from real AWS

- **No connection draining.** AWS waits for the "deregistration delay" before terminating an
  instance. Here a replica is removed straight away, and Nginx keeps its IP for up to 5s. Requests
  that hit it are retried on another replica via `proxy_next_upstream`. In testing, 5,306 requests
  across a full scale-out and scale-in cycle all returned HTTP 200.
- **Passive health checks only.** Open-source Nginx detects failures from real traffic, while an
  ALB also probes actively.
- **One host.** All "instances" share the same machine's CPUs, so `cpus: 0.5` is what makes each
  replica saturate like a small instance type.

## Why `zone` matters

Nginx runs one worker process per CPU core. Without a shared memory `zone`, each worker keeps
its own round-robin counter, so separate short-lived connections can all land on the same replica.
The `zone` is also required for the `resolve` parameter.
