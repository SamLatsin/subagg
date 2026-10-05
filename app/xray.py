"""Конвертация xray outbound JSON в proxy-объект mihomo.

Принимает как целый конфиг xray (с ключом outbounds), так и один outbound.
"""

from __future__ import annotations

import json

from .parsers import xhttp_opts


def _stream_to_clash(out: dict, stream: dict) -> None:
    net = (stream.get("network") or "tcp").lower()
    security = (stream.get("security") or "none").lower()

    if security in ("tls", "reality", "xtls"):
        out["tls"] = True
        tls = stream.get("tlsSettings") or stream.get("realitySettings") or {}
        sni = tls.get("serverName")
        if sni:
            out["servername"] = sni
            out.setdefault("sni", sni)
        if tls.get("fingerprint"):
            out["client-fingerprint"] = tls["fingerprint"]
        if tls.get("alpn"):
            out["alpn"] = list(tls["alpn"])
        if tls.get("allowInsecure"):
            out["skip-cert-verify"] = True
    if security == "reality":
        r = stream.get("realitySettings") or {}
        opts = {}
        if r.get("publicKey"):
            opts["public-key"] = r["publicKey"]
        if r.get("shortId"):
            opts["short-id"] = r["shortId"]
        out["reality-opts"] = opts

    if net in ("h2", "http"):
        out["network"] = "h2"
        h = stream.get("httpSettings") or stream.get("h2Settings") or {}
        o: dict = {"path": h.get("path", "/")}
        if h.get("host"):
            o["host"] = list(h["host"]) if isinstance(h["host"], list) else [h["host"]]
        out["h2-opts"] = o
    elif net == "ws":
        out["network"] = "ws"
        w = stream.get("wsSettings") or {}
        o = {"path": w.get("path", "/")}
        headers = w.get("headers") or {}
        if headers:
            o["headers"] = dict(headers)
        out["ws-opts"] = o
    elif net == "grpc":
        out["network"] = "grpc"
        g = stream.get("grpcSettings") or {}
        out["grpc-opts"] = {"grpc-service-name": g.get("serviceName", "")}
    elif net in ("xhttp", "splithttp"):
        out["network"] = "xhttp"
        x = stream.get("xhttpSettings") or stream.get("splithttpSettings") or {}
        out["xhttp-opts"] = xhttp_opts(x.get("path", "/"), x.get("host", ""), x.get("mode", ""),
                                       {**(x.get("extra") or {}), **x})
    else:
        out["network"] = "tcp"


def outbound_to_clash(ob: dict, name: str | None = None) -> dict | None:
    proto = (ob.get("protocol") or "").lower()
    settings = ob.get("settings") or {}
    stream = ob.get("streamSettings") or {}
    tag = name or ob.get("tag") or proto

    if proto == "vless":
        vnext = (settings.get("vnext") or [{}])[0]
        user = (vnext.get("users") or [{}])[0]
        if not vnext.get("address"):
            return None
        out = {
            "name": tag,
            "type": "vless",
            "server": vnext["address"],
            "port": int(vnext.get("port", 443)),
            "uuid": user.get("id", ""),
            "udp": True,
        }
        if user.get("flow"):
            out["flow"] = user["flow"]
        if user.get("encryption") and user["encryption"] != "none":
            out["encryption"] = user["encryption"]
        if settings.get("packetEncoding") in ("xudp", "packetaddr"):
            out["packet-encoding"] = settings["packetEncoding"]
        _stream_to_clash(out, stream)
        return out

    if proto == "vmess":
        vnext = (settings.get("vnext") or [{}])[0]
        user = (vnext.get("users") or [{}])[0]
        if not vnext.get("address"):
            return None
        out = {
            "name": tag,
            "type": "vmess",
            "server": vnext["address"],
            "port": int(vnext.get("port", 443)),
            "uuid": user.get("id", ""),
            "alterId": int(user.get("alterId", 0) or 0),
            "cipher": user.get("security", "auto"),
            "udp": True,
        }
        _stream_to_clash(out, stream)
        return out

    if proto == "trojan":
        srv = (settings.get("servers") or [{}])[0]
        if not srv.get("address"):
            return None
        out = {
            "name": tag,
            "type": "trojan",
            "server": srv["address"],
            "port": int(srv.get("port", 443)),
            "password": srv.get("password", ""),
            "udp": True,
        }
        _stream_to_clash(out, stream)
        out.pop("network", None)
        return out

    if proto == "shadowsocks":
        srv = (settings.get("servers") or [{}])[0]
        if not srv.get("address"):
            return None
        return {
            "name": tag,
            "type": "ss",
            "server": srv["address"],
            "port": int(srv.get("port", 443)),
            "cipher": srv.get("method", "aes-256-gcm"),
            "password": srv.get("password", ""),
            "udp": True,
        }

    if proto == "wireguard":
        peer = (settings.get("peers") or [{}])[0]
        endpoint = str(peer.get("endpoint", ""))
        if ":" not in endpoint:
            return None
        host, port = endpoint.rsplit(":", 1)
        addrs = settings.get("address") or []
        out = {
            "name": tag,
            "type": "wireguard",
            "server": host.strip("[]"),
            "port": int(port),
            "private-key": settings.get("secretKey", ""),
            "public-key": peer.get("publicKey", ""),
            "udp": True,
        }
        for a in addrs:
            if ":" in str(a):
                out["ipv6"] = str(a).split("/")[0]
            else:
                out["ip"] = str(a)
        if settings.get("mtu"):
            out["mtu"] = int(settings["mtu"])
        if peer.get("preSharedKey"):
            out["pre-shared-key"] = peer["preSharedKey"]
        return out

    return None


def parse_xray_json(text: str, name: str | None = None) -> list[dict]:
    data = json.loads(text)
    obs: list[dict]
    if isinstance(data, dict) and isinstance(data.get("outbounds"), list):
        obs = data["outbounds"]
    elif isinstance(data, list):
        obs = data
    elif isinstance(data, dict):
        obs = [data]
    else:
        return []

    out = []
    for ob in obs:
        if not isinstance(ob, dict):
            continue
        if (ob.get("protocol") or "").lower() in ("freedom", "blackhole", "dns"):
            continue
        node = outbound_to_clash(ob, name if len(obs) == 1 else None)
        if node:
            out.append(node)
    return out
