"""Обратная конвертация: proxy-объект mihomo в ссылку vless:// и т.п.

Нужна для выдачи подписки в base64, как ее отдают обычные панели.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import quote, urlencode


def _transport_params(node: dict) -> dict[str, str]:
    p: dict[str, str] = {}
    net = (node.get("network") or "tcp").lower()
    p["type"] = net
    if net == "ws":
        ws = node.get("ws-opts") or {}
        if ws.get("path"):
            p["path"] = ws["path"]
        host = (ws.get("headers") or {}).get("Host") or (ws.get("headers") or {}).get("host")
        if host:
            p["host"] = host
    elif net == "grpc":
        g = node.get("grpc-opts") or {}
        if g.get("grpc-service-name"):
            p["serviceName"] = g["grpc-service-name"]
    elif net == "h2":
        h = node.get("h2-opts") or {}
        if h.get("path"):
            p["path"] = h["path"]
        if h.get("host"):
            p["host"] = ",".join(h["host"]) if isinstance(h["host"], list) else str(h["host"])
    elif net == "http":
        h = node.get("http-opts") or {}
        paths = h.get("path") or []
        if paths:
            p["path"] = paths[0] if isinstance(paths, list) else str(paths)
    return p


def _tls_params(node: dict) -> dict[str, str]:
    p: dict[str, str] = {}
    reality = node.get("reality-opts") or {}
    if reality:
        p["security"] = "reality"
        if reality.get("public-key"):
            p["pbk"] = reality["public-key"]
        if reality.get("short-id"):
            p["sid"] = reality["short-id"]
    elif node.get("tls"):
        p["security"] = "tls"
    else:
        p["security"] = "none"

    sni = node.get("servername") or node.get("sni")
    if sni:
        p["sni"] = sni
    if node.get("client-fingerprint"):
        p["fp"] = node["client-fingerprint"]
    if node.get("alpn"):
        p["alpn"] = ",".join(node["alpn"])
    if node.get("skip-cert-verify"):
        p["allowInsecure"] = "1"
    return p


def rename_uri(uri: str, name: str) -> str | None:
    """Ссылка провайдера с нашим именем. У vmess имя внутри base64-JSON."""
    if uri.lower().startswith("vmess://"):
        body = uri[len("vmess://"):].split("#", 1)[0]
        try:
            cfg = json.loads(base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            cfg = None
        if isinstance(cfg, dict):
            cfg["ps"] = name
            raw = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode()
            return "vmess://" + base64.b64encode(raw).decode()
    return uri.split("#", 1)[0] + "#" + quote(name, safe="")


def node_to_uri(node: dict) -> str | None:
    t = (node.get("type") or "").lower()
    name = str(node.get("name", ""))
    server = node.get("server")
    port = node.get("port")
    if not server or not port:
        return None
    frag = quote(name, safe="")

    if t == "vless":
        params = {**_tls_params(node), **_transport_params(node)}
        if node.get("flow"):
            params["flow"] = node["flow"]
        params["encryption"] = node.get("encryption") or "none"
        return f"vless://{node.get('uuid','')}@{server}:{port}?{urlencode(params)}#{frag}"

    if t == "vmess":
        net = (node.get("network") or "tcp").lower()
        tp = _transport_params(node)
        cfg = {
            "v": "2",
            "ps": name,
            "add": server,
            "port": str(port),
            "id": node.get("uuid", ""),
            "aid": str(node.get("alterId", 0)),
            "scy": node.get("cipher", "auto"),
            "net": net,
            "type": "none",
            "host": tp.get("host", ""),
            "path": tp.get("path", ""),
            "tls": "tls" if node.get("tls") else "",
            "sni": node.get("servername") or node.get("sni") or "",
        }
        if node.get("client-fingerprint"):
            cfg["fp"] = node["client-fingerprint"]
        if tp.get("serviceName"):
            cfg["path"] = tp["serviceName"]
        raw = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode()
        return "vmess://" + base64.b64encode(raw).decode()

    if t == "trojan":
        params = {**_tls_params(node), **_transport_params(node)}
        params.pop("security", None)
        pw = quote(str(node.get("password", "")), safe="")
        return f"trojan://{pw}@{server}:{port}?{urlencode(params)}#{frag}"

    if t == "ss":
        userinfo = f"{node.get('cipher','')}:{node.get('password','')}"
        b64 = base64.urlsafe_b64encode(userinfo.encode()).decode().rstrip("=")
        return f"ss://{b64}@{server}:{port}#{frag}"

    if t == "hysteria2":
        params: dict[str, str] = {}
        if node.get("sni"):
            params["sni"] = node["sni"]
        if node.get("obfs"):
            params["obfs"] = node["obfs"]
            if node.get("obfs-password"):
                params["obfs-password"] = node["obfs-password"]
        if node.get("skip-cert-verify"):
            params["insecure"] = "1"
        pw = quote(str(node.get("password", "")), safe="")
        qs = f"?{urlencode(params)}" if params else ""
        return f"hysteria2://{pw}@{server}:{port}{qs}#{frag}"

    if t == "tuic":
        params = {}
        if node.get("sni"):
            params["sni"] = node["sni"]
        if node.get("congestion-controller"):
            params["congestion_control"] = node["congestion-controller"]
        if node.get("udp-relay-mode"):
            params["udp_relay_mode"] = node["udp-relay-mode"]
        if node.get("skip-cert-verify"):
            params["allow_insecure"] = "1"
        qs = f"?{urlencode(params)}" if params else ""
        uuid = quote(str(node.get("uuid", "")), safe="")
        pw = quote(str(node.get("password", "")), safe="")
        return f"tuic://{uuid}:{pw}@{server}:{port}{qs}#{frag}"

    # wireguard и прочее в ссылку не сворачивается
    return None
