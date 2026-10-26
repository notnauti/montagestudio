"""Headless Chrome over the DevTools protocol, stdlib only: enough to load a page, run JS, and click.

Not a test module. `find_chrome()` returns None when no Chrome/Edge is installed; callers skip.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome():
    if os.environ.get("MONTAGE_TEST_CHROME"):
        return os.environ["MONTAGE_TEST_CHROME"]
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser", "msedge"):
        if shutil.which(name):
            return shutil.which(name)
    return next((p for p in CANDIDATES if Path(p).is_file()), None)


class CDPError(RuntimeError):
    pass


class _Socket:
    """A client websocket: masked text frames out, text frames (maybe fragmented) in."""

    def __init__(self, url, timeout):
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        # no Origin header: Chrome only checks --remote-allow-origins when one is sent
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake: connection closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError(f"websocket handshake refused: {head[:200]!r}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        n = len(data)
        head = bytes([0x81])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 0x8:
                raise CDPError("websocket closed by the browser")
            if op in (0x9, 0xA):                      # ping/pong: Chrome does not ping clients
                continue
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Chrome:
    """One headless browser, one page. `exceptions` collects uncaught page errors."""

    def __init__(self, exe, timeout=30):
        self.profile = tempfile.mkdtemp(prefix="montage-cdp-")
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
             "--window-size=1280,900", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.next_id = 0
        self.exceptions = []
        try:
            page = self._page(self._port(timeout), timeout)
            self.ws = _Socket(page["webSocketDebuggerUrl"], timeout)
            self.call("Runtime.enable")
            self.call("Page.enable")
        except Exception:
            self.close()
            raise

    def _page(self, port, timeout):
        # the port file can appear before the about:blank target is listed
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout))
            page = next((t for t in targets if t["type"] == "page"), None)
            if page:
                return page
            time.sleep(0.05)
        raise CDPError("browser never listed a page target")

    def _port(self, timeout):
        f = Path(self.profile) / "DevToolsActivePort"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise CDPError(f"browser exited with {self.proc.returncode}")
            if f.is_file() and f.read_text().strip():
                return int(f.read_text().split()[0])
            time.sleep(0.05)
        raise CDPError("browser never opened its debugging port")

    def call(self, method, **params):
        self.next_id += 1
        mine = self.next_id
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.exceptions.append((d.get("exception") or {}).get("description") or d.get("text"))
            if msg.get("id") == mine:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error']}")
                return msg["result"]

    def goto(self, url):
        self.call("Page.navigate", url=url)
        self.wait("document.readyState === 'complete'")

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise CDPError(f"{expr!r}: {r['exceptionDetails'].get('exception', {}).get('description')}")
        return r["result"].get("value")

    def wait(self, expr, timeout=20):
        end = time.monotonic() + timeout
        while True:
            value = self.js(expr)
            if value:
                return value
            if time.monotonic() > end:
                raise AssertionError(f"timed out after {timeout}s waiting for: {expr}")
            time.sleep(0.1)

    def click_at(self, x, y):
        """A real mouse click (trusted event, real coordinates), unlike element.click()."""
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def close(self):
        if getattr(self, "ws", None):
            self.ws.close()
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)    # Windows may still hold a lock briefly
