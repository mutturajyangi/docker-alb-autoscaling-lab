"""A tiny "AWS Auto Scaling Group" for Docker Compose.

Every CHECK_INTERVAL seconds it measures the average CPU utilisation of the
app replicas (like a CloudWatch CPUUtilization metric) and scales the Compose
service out or in, one replica at a time, with a cooldown between actions.
"""
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import docker

PROJECT = os.getenv("COMPOSE_PROJECT", "lb-lab")
SERVICE = os.getenv("SCALE_SERVICE", "app")
COMPOSE_FILE = os.getenv("COMPOSE_FILE_PATH", "/project/docker-compose.yml")

MIN_REPLICAS = int(os.getenv("MIN_REPLICAS", "1"))
MAX_REPLICAS = int(os.getenv("MAX_REPLICAS", "6"))
SCALE_OUT_CPU = float(os.getenv("SCALE_OUT_CPU", "60"))
SCALE_IN_CPU = float(os.getenv("SCALE_IN_CPU", "20"))
SCALE_OUT_PERIODS = int(os.getenv("SCALE_OUT_PERIODS", "3"))
SCALE_IN_PERIODS = int(os.getenv("SCALE_IN_PERIODS", "6"))
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL", "5"))
COOLDOWN = float(os.getenv("COOLDOWN", "30"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [autoscaler] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

client = docker.from_env()


def app_containers():
    return client.containers.list(
        filters={
            "label": [
                f"com.docker.compose.project={PROJECT}",
                f"com.docker.compose.service={SERVICE}",
            ],
            "status": "running",
        }
    )


def cpu_utilisation(container) -> float | None:
    """CPU usage as a percentage of the container's CPU limit (0-100+)."""
    try:
        stats = container.stats(stream=False)
        cpu, precpu = stats["cpu_stats"], stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - precpu["cpu_usage"]["total_usage"]
        system_delta = cpu["system_cpu_usage"] - precpu.get("system_cpu_usage", 0)
        if system_delta <= 0:
            return None
        online_cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage", [1]))
        cores_used = cpu_delta / system_delta * online_cpus

        # Normalise against the limit (e.g. cpus: 0.5), like an instance's vCPUs
        nano_cpus = container.attrs["HostConfig"].get("NanoCpus") or 0
        cores_allowed = nano_cpus / 1e9 if nano_cpus else online_cpus
        return cores_used / cores_allowed * 100
    except (docker.errors.APIError, KeyError):
        return None


def average_cpu(containers) -> float:
    with ThreadPoolExecutor(max_workers=max(len(containers), 1)) as pool:
        values = [v for v in pool.map(cpu_utilisation, containers) if v is not None]
    return sum(values) / len(values) if values else 0.0


def scale_to(replicas: int) -> bool:
    cmd = [
        "docker", "compose",
        "-p", PROJECT,
        "-f", COMPOSE_FILE,
        "up", "-d",
        "--no-recreate", "--no-build", "--no-deps",
        "--scale", f"{SERVICE}={replicas}",
        SERVICE,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("scale command failed: %s", result.stderr.strip())
        return False
    return True


def main():
    log.info(
        "watching %s/%s | min=%d max=%d | out>%.0f%% x%d | in<%.0f%% x%d | cooldown=%.0fs",
        PROJECT, SERVICE, MIN_REPLICAS, MAX_REPLICAS,
        SCALE_OUT_CPU, SCALE_OUT_PERIODS, SCALE_IN_CPU, SCALE_IN_PERIODS, COOLDOWN,
    )
    high_streak = low_streak = 0
    last_action = 0.0

    while True:
        containers = app_containers()
        replicas = len(containers)
        avg = average_cpu(containers)

        high_streak = high_streak + 1 if avg > SCALE_OUT_CPU else 0
        low_streak = low_streak + 1 if avg < SCALE_IN_CPU else 0
        in_cooldown = time.monotonic() - last_action < COOLDOWN

        target = replicas
        if replicas < MIN_REPLICAS:
            target = MIN_REPLICAS
        elif not in_cooldown and high_streak >= SCALE_OUT_PERIODS and replicas < MAX_REPLICAS:
            target = replicas + 1
        elif not in_cooldown and low_streak >= SCALE_IN_PERIODS and replicas > MIN_REPLICAS:
            target = replicas - 1

        status = "cooldown" if in_cooldown else "ok"
        if target != replicas:
            direction = "OUT" if target > replicas else "IN"
            log.info("avg_cpu=%5.1f%% replicas=%d -> scaling %s to %d", avg, replicas, direction, target)
            if scale_to(target):
                last_action = time.monotonic()
                high_streak = low_streak = 0
        else:
            log.info("avg_cpu=%5.1f%% replicas=%d (%s)", avg, replicas, status)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
