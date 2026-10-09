"""Checkers that misbehave on purpose, to test the runner's limits."""

import sys
import time


def _sleep(files, facts, params):
    time.sleep(30)
    return "pass", "slept"


def _flood(files, facts, params):
    sys.__stdout__.write("x" * 200_000)
    sys.__stdout__.flush()
    return "pass", "flooded"


def _crash(files, facts, params):
    raise RuntimeError("checker crashed")


def _bad_status(files, facts, params):
    return "maybe", "not a status"


def _network(files, facts, params):
    import socket

    socket.create_connection(("example.com", 80), timeout=2)
    return "pass", "connected"


def _write_back(files, facts, params):
    for path in files:
        files[path] = b"changed"
    return "pass", "changed my copy"


REGISTRY = {
    "test.sleep": ("1.0.0", _sleep),
    "test.flood": ("1.0.0", _flood),
    "test.crash": ("1.0.0", _crash),
    "test.bad-status": ("1.0.0", _bad_status),
    "test.network": ("1.0.0", _network),
    "test.write-back": ("1.0.0", _write_back),
}
