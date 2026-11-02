"""Bounded, local timings; never subtract clocks on different machines."""

import logging
import socket
import threading
import time


LOGGER = logging.getLogger("Beamer")
LOG_SECONDS = 30.0


class LinkTiming:
    def __init__(self, direction, clock=time.monotonic):
        self.direction = direction
        self.clock = clock
        self.lock = threading.Lock()
        self.stages = {}
        self.logged_at = clock()
        self.route = "unavailable"

    def connected(self, sock):
        try:
            local, remote = sock.getsockname(), sock.getpeername()
            self.route = f"{local[0]}:{local[1]}->{remote[0]}:{remote[1]}"
            LOGGER.info("link route %s %s interface_address=%s nodelay=%d send_buffer=%d receive_buffer=%d",
                        self.direction, self.route, local[0],
                        bool(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)),
                        sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF),
                        sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
        except (AttributeError, OSError, TypeError, IndexError):
            # Test sockets and a connection closed during setup may have no endpoints.
            self.route = "unavailable"

    def add(self, stage, elapsed):
        elapsed = max(0.0, elapsed) * 1000
        with self.lock:
            count, total, largest = self.stages.get(stage, (0, 0.0, 0.0))
            self.stages[stage] = count + 1, total + elapsed, max(largest, elapsed)

    def flush(self, force=False, ping_ms=None):
        now = self.clock()
        with self.lock:
            if not force and now - self.logged_at < LOG_SECONDS:
                return
            stages, self.stages = self.stages, {}
            self.logged_at = now
        if stages:
            fields = " ".join(f"{name}_ms(count/mean/max)={count}/{total / count:.3f}/{largest:.3f}"
                              for name, (count, total, largest) in sorted(stages.items()))
            LOGGER.info("link timing %s %s ping_rtt_ms=%s %s", self.direction, self.route,
                        ping_ms if ping_ms is not None else "unavailable", fields)
