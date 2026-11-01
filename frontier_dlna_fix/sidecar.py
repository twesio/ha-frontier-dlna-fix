"""DLNA sidecar for Frontier Silicon radios whose device description lacks a serviceList.

The radio's MediaRenderer works, but its dd.xml omits the <serviceList>, so UPnP
clients such as Music Assistant ignore it. This sidecar:

* serves a corrected description (with serviceList and its own UDN) on /dd.xml,
* relays all other HTTP requests (SCPD, SOAP control, SUBSCRIBE) to the radio,
* announces itself via SSDP so it is discovered like a normal renderer,
* optionally rewrites stream URLs to MP3, since these radios reject FLAC/WAV.

Audio streams and event NOTIFYs flow directly between the client and the radio.
"""

import http.client
import json
import logging
import os
import random
import re
import signal
import socket
import struct
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
MAX_AGE = 1800
NOTIFY_INTERVAL = 300
SERVICES = ("AVTransport", "RenderingControl", "ConnectionManager")
SERVER = "Linux UPnP/1.0 frontier-dlna-fix/1.0"
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "host"}
# Stream URLs ending in a format the radio can't play, e.g. Music Assistant's
# ".../<player_id>.flac" or ".../<player_id>.wav;codec=pcm;rate=44100;...".
UNSUPPORTED_URL_RE = re.compile(
    r"(https?://[^<>\s\"]*?/[^/<>\s\"?]+)\.(?:flac|wav|aiff?|ogg|opus|alac|pcm)(?:(?:;|%3[Bb])[^<>\s\"&?]*)?",
)
UNSUPPORTED_MIME_RE = re.compile(r"(http-get:\*:)audio/(?:x-)?(?:flac|wav|wave|aiff|ogg|opus|L16[^:]*)(:)")
DLNA_PN_RE = re.compile(r"DLNA\.ORG_PN=[A-Za-z0-9_]+;?")
SET_URI_ACTIONS = ("#SetAVTransportURI", "#SetNextAVTransportURI")

log = logging.getLogger("frontier_dlna_fix")


def load_options() -> dict:
    opts = {"radio_host": "", "radio_port": 8080, "port": 8099, "friendly_name": "", "force_mp3": True}
    try:
        with open("/data/options.json", encoding="utf-8") as f:
            opts.update(json.load(f))
    except FileNotFoundError:
        pass
    for key in opts:
        if (env := os.environ.get(key.upper())) is not None:
            opts[key] = env
    if not opts["radio_host"]:
        sys.exit("radio_host is not configured")
    opts["radio_port"] = int(opts["radio_port"])
    opts["port"] = int(opts["port"])
    if isinstance(opts["force_mp3"], str):
        opts["force_mp3"] = opts["force_mp3"].lower() in ("1", "true", "yes", "on")
    return opts


def rewrite_to_mp3(body: bytes) -> bytes:
    """Point stream URLs and protocolInfo of a SetAVTransportURI request at MP3.

    Music Assistant picks the stream format from the URL's extension, so asking
    for ".mp3" instead of ".flac" makes it encode MP3, which these radios accept.
    """
    text = body.decode("utf-8", errors="replace")
    new = UNSUPPORTED_URL_RE.sub(r"\1.mp3", text)
    if new != text:
        new = UNSUPPORTED_MIME_RE.sub(r"\1audio/mpeg\2", new)
        new = DLNA_PN_RE.sub("", new)
    return new.encode("utf-8")


def soap_fault(body: bytes) -> str:
    """Return 'code description' of a UPnP SOAP fault, or an empty string."""
    text = body.decode("utf-8", errors="replace")
    code = re.search(r"<errorCode>\s*(\d+)\s*</errorCode>", text)
    desc = re.search(r"<errorDescription>(.*?)</errorDescription>", text, re.S)
    return " ".join(m.group(1).strip() for m in (code, desc) if m)


def local_ip_for(host: str) -> str:
    """Return the local IP address used to reach host."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((host, 9))
        return s.getsockname()[0]


class Radio:
    """Fetches the radio's description and builds the corrected one."""

    def __init__(self, host: str, port: int, friendly_name: str) -> None:
        self.host = host
        self.port = port
        self.friendly_name = friendly_name
        self.udn = ""
        self.description = b""

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.reason, resp.getheaders(), resp.read()
        finally:
            conn.close()

    def refresh(self) -> None:
        status, _, _, body = self.request("GET", "/dd.xml")
        if status != 200:
            raise OSError(f"radio returned HTTP {status} for /dd.xml")
        xml = body.decode("utf-8", errors="replace")
        radio_udn = re.search(r"<UDN>\s*uuid:([^<\s]+)\s*</UDN>", xml)
        if not radio_udn:
            raise ValueError("radio description has no UDN")
        # Own, stable UDN so clients don't confuse us with the radio's broken entry.
        # "v2": IDs from 1.0.0 may be cached in Music Assistant as linked to the wrong device.
        self.udn = str(uuid.uuid5(uuid.NAMESPACE_URL, f"frontier-dlna-fix:v2:{radio_udn.group(1)}"))
        xml = xml.replace(radio_udn.group(0), f"<UDN>uuid:{self.udn}</UDN>")
        # Clients such as Music Assistant take the device's IP from presentationURL. Point it
        # at the radio so it is identified by the radio's address and MAC, not this host's.
        xml = re.sub(r"\s*<presentationURL>.*?</presentationURL>", "", xml, flags=re.S)
        xml = xml.replace(
            "</UDN>", f"</UDN><presentationURL>http://{self.host}/</presentationURL>", 1
        )
        if self.friendly_name:
            xml = re.sub(
                r"<friendlyName>.*?</friendlyName>",
                f"<friendlyName>{escape(self.friendly_name)}</friendlyName>",
                xml,
                count=1,
                flags=re.S,
            )
        if "<serviceList>" not in xml:
            services = "".join(
                "<service>"
                f"<serviceType>urn:schemas-upnp-org:service:{name}:1</serviceType>"
                f"<serviceId>urn:upnp-org:serviceId:{name}</serviceId>"
                f"<SCPDURL>/{name}/scpd.xml</SCPDURL>"
                f"<controlURL>/{name}/control</controlURL>"
                f"<eventSubURL>/{name}/event</eventSubURL>"
                "</service>"
                for name in SERVICES
            )
            xml = xml.replace("</device>", f"<serviceList>{services}</serviceList></device>", 1)
        self.description = xml.encode("utf-8")


def make_handler(radio: Radio, force_mp3: bool = True):
    class Handler(BaseHTTPRequestHandler):
        server_version = SERVER
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, reason: str, headers, body: bytes) -> None:
            self.send_response(status, reason)
            for key, value in headers:
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self) -> None:
            if self.path.split("?")[0] == "/dd.xml":
                try:
                    radio.refresh()
                except (OSError, ValueError) as err:
                    log.debug("Using cached description, radio not reachable: %s", err)
                    if not radio.description:
                        self._send(502, "Bad Gateway", [], b"")
                        return
                self._send(200, "OK", [("Content-Type", 'text/xml; charset="utf-8"')], radio.description)
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            headers["Host"] = f"{radio.host}:{radio.port}"
            action = self.headers.get("SOAPACTION", "").strip('"')
            if body and force_mp3 and action.endswith(SET_URI_ACTIONS):
                rewritten = rewrite_to_mp3(body)
                if rewritten != body:
                    log.info("Requesting MP3 instead of an unsupported stream format")
                    body = rewritten
            try:
                status, reason, resp_headers, resp_body = radio.request(self.command, self.path, body, headers)
            except OSError as err:
                log.warning("Relaying %s %s failed: %s", self.command, self.path, err)
                self._send(502, "Bad Gateway", [], b"")
                return
            if status >= 400 and self.command == "POST":
                fault = soap_fault(resp_body)
                log.warning("Radio rejected %s: %s", action.rpartition("#")[2] or self.path, fault or status)
                if fault.startswith("714"):
                    log.warning("Set the player's output codec to MP3 or enable force_mp3")
            self._send(status, reason, resp_headers, resp_body)

        do_GET = do_HEAD = do_POST = do_SUBSCRIBE = do_UNSUBSCRIBE = _handle

    return Handler


class Ssdp:
    """Answers M-SEARCH requests and sends NOTIFY announcements."""

    def __init__(self, radio: Radio, location: str, local_ip: str) -> None:
        self.radio = radio
        self.location = location
        self.local_ip = local_ip
        self.stop = threading.Event()

    def targets(self) -> list[tuple[str, str]]:
        """Return (NT/ST, USN) pairs for the root device and its services."""
        udn = f"uuid:{self.radio.udn}"
        pairs = [
            ("upnp:rootdevice", f"{udn}::upnp:rootdevice"),
            (udn, udn),
            ("urn:schemas-upnp-org:device:MediaRenderer:1", f"{udn}::urn:schemas-upnp-org:device:MediaRenderer:1"),
        ]
        pairs += [
            (f"urn:schemas-upnp-org:service:{s}:1", f"{udn}::urn:schemas-upnp-org:service:{s}:1") for s in SERVICES
        ]
        return pairs

    def _send_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.local_ip))
        return sock

    def notify(self, nts: str) -> None:
        with self._send_socket() as sock:
            for nt, usn in self.targets():
                lines = [
                    "NOTIFY * HTTP/1.1",
                    f"HOST: {SSDP_ADDR}:{SSDP_PORT}",
                    f"NT: {nt}",
                    f"NTS: {nts}",
                    f"USN: {usn}",
                ]
                if nts == "ssdp:alive":
                    lines += [f"CACHE-CONTROL: max-age={MAX_AGE}", f"LOCATION: {self.location}", f"SERVER: {SERVER}"]
                sock.sendto(("\r\n".join(lines) + "\r\n\r\n").encode(), (SSDP_ADDR, SSDP_PORT))

    def notify_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.notify("ssdp:alive")
            except OSError as err:
                log.warning("Sending SSDP NOTIFY failed: %s", err)
            self.stop.wait(NOTIFY_INTERVAL)

    def listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", SSDP_PORT))
        mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(self.local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1)
        while not self.stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as err:
                log.warning("SSDP receive failed: %s", err)
                continue
            text = data.decode("utf-8", errors="replace")
            if not text.startswith("M-SEARCH"):
                continue
            headers = dict(
                (k.strip().lower(), v.strip()) for k, _, v in (line.partition(":") for line in text.split("\r\n")[1:])
            )
            st = headers.get("st", "")
            try:
                mx = min(max(int(headers.get("mx", "1")), 1), 5)
            except ValueError:
                mx = 1
            matches = [(t, usn) for t, usn in self.targets() if st in ("ssdp:all", t)]
            if matches:
                threading.Thread(target=self._respond, args=(addr, matches, mx), daemon=True).start()

    def _respond(self, addr, matches, mx: int) -> None:
        time.sleep(random.uniform(0, min(mx, 2)))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for st, usn in matches:
                msg = (
                    "HTTP/1.1 200 OK\r\n"
                    f"CACHE-CONTROL: max-age={MAX_AGE}\r\n"
                    "EXT:\r\n"
                    f"LOCATION: {self.location}\r\n"
                    f"SERVER: {SERVER}\r\n"
                    f"ST: {st}\r\n"
                    f"USN: {usn}\r\n"
                    "\r\n"
                )
                sock.sendto(msg.encode(), addr)
        log.debug("Answered M-SEARCH from %s (%d targets)", addr[0], len(matches))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.environ.get("DEBUG"):
        log.setLevel(logging.DEBUG)
    opts = load_options()
    radio = Radio(opts["radio_host"], opts["radio_port"], opts["friendly_name"])

    while True:
        try:
            radio.refresh()
            break
        except (OSError, ValueError) as err:
            log.warning("Radio %s not reachable yet (%s), retrying in 30s", radio.host, err)
            time.sleep(30)

    local_ip = local_ip_for(radio.host)
    location = f"http://{local_ip}:{opts['port']}/dd.xml"
    httpd = ThreadingHTTPServer(("0.0.0.0", opts["port"]), make_handler(radio, opts["force_mp3"]))
    ssdp = Ssdp(radio, location, local_ip)

    def shutdown(*_):
        log.info("Shutting down")
        ssdp.stop.set()
        try:
            ssdp.notify("ssdp:byebye")
        except OSError:
            pass
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    threading.Thread(target=ssdp.listen_loop, daemon=True).start()
    threading.Thread(target=ssdp.notify_loop, daemon=True).start()
    log.info("Serving radio %s:%d as uuid:%s at %s", radio.host, radio.port, radio.udn, location)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
