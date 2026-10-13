# Docker ALB + Auto Scaling Lab

Simulating an **AWS Application Load Balancer (ALB)** and an **Auto Scaling Group (ASG)** with nothing but Docker containers.

FastAPI "instances" sit on a private Docker network behind an Nginx load balancer. An autoscaler
watches their CPU and adds or removes instances as load changes, just like an ASG with a
target-tracking policy. Every response tells you **which container served it**, so you can watch
distribution, scaling and failover happen live.

```
                     ┌──────────────────── lb-net (private bridge network) ────────────────────┐
                     │                                                                         │
  Client ──:8080───▶ │  load-balancer (nginx) ──round-robin──▶ app replica 1 (FastAPI :8000)  │
                     │        ▲  re-resolves "app" via         app replica 2                   │
                     │        │  Docker DNS every 5s           ...                             │
                     │        │                                app replica N  (1 ≤ N ≤ 6)      │
                     └────────┼────────────────────────────────────────▲────────────────────────┘
                              │                                        │ docker compose --scale
                              │                         autoscaler ────┘ (reads CPU via Docker API)
```

## AWS ↔ Docker mapping

| AWS concept                        | This lab                                                        |
|------------------------------------|-----------------------------------------------------------------|
| VPC / private subnet               | User-defined bridge network `lb-net`                            |
| Launch template / instance type    | `app` image with `cpus: 0.5` per replica                        |
| EC2 instances (target group)       | Replicas of the `app` Compose service (`lb-lab-app-1`, `-2`, ...) |
| Application Load Balancer          | `load-balancer` (Nginx), the only container with a public port  |
| Target registration                | Nginx re-resolves the `app` DNS name every 5s (`resolve`)       |
| Target group health checks         | `/health` + Docker `HEALTHCHECK` + Nginx `max_fails`            |
| Auto Scaling Group (min/max)       | `autoscaler` container, `MIN_REPLICAS=1`, `MAX_REPLICAS=6`      |
| CloudWatch CPU alarm + cooldown    | Avg CPU > 60% for 3 checks → +1; < 20% for 6 checks → −1; 30s cooldown |
| Security groups                    | App replicas publish no ports; reachable only via the LB        |

## Project structure

```
.
├── app/
│   ├── main.py            # FastAPI: GET / (instance info), GET /work (burn CPU), GET /health
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .dockerignore
├── autoscaler/
│   ├── autoscaler.py      # Polls CPU, scales the app service with docker compose --scale
│   ├── requirements.txt
│   └── Dockerfile
├── nginx/
│   └── nginx.conf         # Dynamic upstream (DNS resolve), round-robin, passive health checks
├── scripts/
│   ├── test_lb.sh         # Fire N requests and show the distribution
│   └── watch.sh           # Live view: replicas, CPU, autoscaler decisions
├── docs/
│   └── architecture.md
└── docker-compose.yml
```

## Prerequisites

- Docker Engine 20.10+ with the Compose v2 plugin (`docker compose version`)
- `curl` (for the test script)
- Linux, or Docker Desktop on macOS/Windows (the autoscaler mounts `/var/run/docker.sock`)

## Run it

```bash
# Build the images and start the load balancer, 1 app replica and the autoscaler
docker compose up -d --build

# Check status
docker compose ps

# Hit the load balancer
curl http://localhost:8080/
# {"message":"Hello from the load balancing lab!","instance":"a52a9f0cc07e","hostname":"a52a9f0cc07e","ip":"172.19.0.2"}

# Send 10 requests and see how they are spread
./scripts/test_lb.sh 10
```

Browser: <http://localhost:8080> · API docs: <http://localhost:8080/docs>

## Auto Scaling demo

Use two terminals.

**Terminal 1: watch the Auto Scaling Group**
```bash
./scripts/watch.sh                 # Ctrl+C to exit
# or just the decisions:
docker logs -f autoscaler
```

**Terminal 2: generate load** (3 minutes of ~30 req/s to the CPU-heavy `/work` endpoint)
```bash
docker compose --profile load run --rm loadgen
```

What you should see (from a real run):
```
avg_cpu=101.5% replicas=1 -> scaling OUT to 2
avg_cpu= 62.2% replicas=2 -> scaling OUT to 3
avg_cpu= 50.0% replicas=3 (ok)              # stable: demand ≈ 1.5 CPU over 3 × 0.5 CPU
...load stops...
avg_cpu= 16.8% replicas=3 -> scaling IN to 2
avg_cpu= 19.0% replicas=2 -> scaling IN to 1
```
While the load is running, `./scripts/test_lb.sh 9` shows requests spread across all replicas.

**Make it scale higher** by pushing more load, e.g. 60 req/s (≈ 3 CPUs, so up to 6 replicas):
```bash
docker compose --profile load run --rm loadgen -z 180s -c 20 -q 3 http://load-balancer/work?ms=50
```

**Tune the policy** by editing the `autoscaler` environment in `docker-compose.yml`
(`SCALE_OUT_CPU`, `SCALE_IN_CPU`, `MAX_REPLICAS`, `COOLDOWN`, ...), then:
```bash
docker compose up -d autoscaler
```

## Other experiments

**Manual scaling** (like changing an ASG's desired capacity by hand):
```bash
docker compose up -d --scale app=4
./scripts/test_lb.sh 12
```
The autoscaler will bring it back down if the replicas stay idle.

**Failover** (an instance becomes unhealthy):
```bash
docker compose up -d --scale app=3
docker stop lb-lab-app-2
./scripts/test_lb.sh 9      # only the remaining replicas respond, no errors
docker start lb-lab-app-2
```

**Balancing strategy**: edit `nginx/nginx.conf`, uncomment `least_conn;` or `ip_hash;`, then
`docker compose restart load-balancer`.

**Logs**: `docker compose logs -f` (all) or `docker compose logs -f load-balancer`.

## Stop it

```bash
# Stop and remove all containers (every replica) and networks; images are kept
docker compose down

# Also remove the built images (lb-lab-app, lb-lab-autoscaler)
docker compose down --rmi local

# Pause without removing (resume with: docker compose start)
docker compose stop
```

## Security note

The autoscaler mounts `/var/run/docker.sock`, which gives it full control of Docker on the host.
That is acceptable for a local lab but should never be done in production. Use a real
orchestrator (ECS Service Auto Scaling, Kubernetes HPA) instead.

## Troubleshooting

- **Port 8080 already in use**: change `"8080:80"` in `docker-compose.yml` (e.g. `"9090:80"`).
- **"container name already in use"**: an older version of this lab is still running. Remove it with
  `docker rm -f app1 app2 app3 load-balancer` and try again.
- **All requests hit the same instance**: keep `zone app_servers 64k;` in the upstream block.
  Without it, each Nginx worker process keeps its own round-robin counter.
- **Never scales**: check `docker logs autoscaler`. The `/` endpoint is too light to load the CPU,
  so use the `loadgen` service, which calls `/work`.
