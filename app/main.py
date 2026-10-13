"""Simple FastAPI app that reports which container (instance) served the request."""
import os
import socket
import time

from fastapi import FastAPI, Query

app = FastAPI(title="Docker Load Balancing Lab")

# When scaled with `docker compose --scale`, each replica's hostname is its
# container ID, which uniquely identifies the instance.
INSTANCE_NAME = os.getenv("INSTANCE_NAME", socket.gethostname())


def get_container_ip() -> str:
    """Return the container's IP address on its Docker network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # No packets are sent; this just picks the outbound interface.
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def instance_info() -> dict:
    return {
        "instance": INSTANCE_NAME,
        "hostname": socket.gethostname(),
        "ip": get_container_ip(),
    }


@app.get("/")
def root():
    return {"message": "Hello from the load balancing lab!", **instance_info()}


@app.get("/work")
def work(ms: int = Query(50, ge=1, le=2000)):
    """Burn CPU for `ms` milliseconds to simulate a compute-heavy request."""
    deadline = time.perf_counter() + ms / 1000
    count = 0
    while time.perf_counter() < deadline:
        count += 1
    return {"worked_ms": ms, "iterations": count, **instance_info()}


@app.get("/health")
def health():
    return {"status": "healthy", "instance": INSTANCE_NAME}
