"""Разбор подписок в формат proxy-объектов mihomo.

На вход приходит одно из трех:
  - Clash/mihomo YAML с ключом proxies
  - base64 от списка ссылок vless:// vmess:// ...
  - тот же список ссылок в открытом виде
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

import yaml

log = logging.getLogger(__name__)

# Заглушки, которые панели подсовывают вместо нод незнакомому клиенту
JUNK_NAME_RE = re.compile(
    r"(app\s*not\s*supported|приложение\s+не|свяжитесь\s+с\s+нами|обновите\s+приложение|"
    r"подписка\s+истекла|subscription\s+expired|please\s+update)",
    re.IGNORECASE,
)


def _b64_fix(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    return s + "=" * (-len(s) % 4)


def b64decode_loose(s: str) -> bytes:
    return base64.b64decode(_b64_fix(s))


def _qs_first(q: dict[str, list[str]], *names: str, default: str = "") -> str:
    for n in names:
        if n in q and q[n]:
            v = q[n][0]
            if v != "":
                return v
    return default


def _truthy(v: str) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# транспорт
# --------------------------------------------------------------------------- #


XHTTP_MODES = {"auto", "packet-up", "stream-up", "stream-one"}

# xray extra (camelCase) -> xhttp-opts mihomo
_XHTTP_EXTRA = {
    "headers": "headers",
    "noGRPCHeader": "no-grpc-header",
    "noSSEHeader": "no-sse-header",
    "xPaddingBytes": "x-padding-bytes",
    "scMaxEachPostBytes": "sc-max-each-post-bytes",
    "scMinPostsIntervalMs": "sc-min-posts-interval-ms",
    "scMaxBufferedPosts": "sc-max-buffered-posts",
}
_XMUX = {
    "maxConcurrency": "max-concurrency",
    "maxConnections": "max-connections",
    "cMaxReuseTimes": "c-max-reuse-times",
    "hMaxRequestTimes": "h-max-request-times",
    "hMaxReusableSecs": "h-max-reusable-secs",
    "hKeepAlivePeriod": "h-keep-alive-period",
}


def _range(v):
    # xray пишет диапазоны и строкой "100-1000", и объектом {"from":..,"to":..}
    if isinstance(v, dict) and "from" in v:
        return f"{v['from']}-{v.get('to', v['from'])}"
    return v


def xhttp_opts(path: str, host: str = "", mode: str = "", extra: dict | None = None) -> dict:
    """Опции xhttp для mihomo из того, что дают ссылка или xray-конфиг.

    Без этого xhttp-ноды уезжали в старый network: http, теряли mode и
    xmux, и сервер под stream-one/packet-up отвечал таймаутами.
    """
    opts: dict = {"path": path or "/"}
    if host:
        opts["host"] = host
    extra = extra or {}
    mode = (mode or extra.get("mode") or "").lower()
    # Неизвестный mode ядро не принимает вовсе, лучше оставить auto
    if mode in XHTTP_MODES:
        opts["mode"] = mode
    for src, dst in _XHTTP_EXTRA.items():
        if extra.get(src) not in (None, "", {}):
            opts[dst] = _range(extra[src])
    xmux = extra.get("xmux") or {}
    reuse = {dst: _range(xmux[src]) for src, dst in _XMUX.items() if xmux.get(src) not in (None, "")}
    if reuse:
        opts["reuse-settings"] = reuse
    if extra.get("downloadSettings"):
        # Отдельный канал на скачивание описывается в mihomo иначе, не переносим.
        # Base64-выдача отдает исходную ссылку, там он сохранится
        log.info("xhttp downloadSettings не переносится в clash-конфиг")
    return opts


def _apply_transport(out: dict, net: str, q: dict[str, list[str]]) -> None:
    """Общая для vless/vmess/trojan часть: network + его опции."""
    net = (net or "tcp").lower()
    if net in ("h2", "http/2"):
        net = "h2"
    out["network"] = net

    path = unquote(_qs_first(q, "path", default="/"))
    host = _qs_first(q, "host")
    service = _qs_first(q, "serviceName", "servicename")

    if net == "ws":
        ws: dict = {"path": path}
        if host:
            ws["headers"] = {"Host": host}
        ed = _qs_first(q, "ed")
        if ed.isdigit():
            ws["max-early-data"] = int(ed)
            ws["early-data-header-name"] = _qs_first(q, "eh", default="Sec-WebSocket-Protocol")
        out["ws-opts"] = ws
    elif net == "grpc":
        out["grpc-opts"] = {"grpc-service-name": service or path.lstrip("/")}
    elif net == "h2":
        h2: dict = {"path": path}
        if host:
            h2["host"] = [h for h in host.split(",") if h]
        out["h2-opts"] = h2
    elif net == "http":
        http: dict = {"method": "GET", "path": [path]}
        if host:
            http["headers"] = {"Host": host.split(",")}
        out["http-opts"] = http
    elif net in ("xhttp", "splithttp"):
        out["network"] = "xhttp"
        try:
            extra = json.loads(unquote(_qs_first(q, "extra", default="{}")))
        except ValueError:
            extra = {}
        out["xhttp-opts"] = xhttp_opts(path, host, _qs_first(q, "mode"),
                                       extra if isinstance(extra, dict) else {})
    elif net == "tcp" and _qs_first(q, "headerType").lower() == "http":
        # tcp с http-маскировкой - в mihomo это network: http
        out["network"] = "http"
        http = {"method": "GET", "path": [path]}
        if host:
            http["headers"] = {"Host": host.split(",")}
        out["http-opts"] = http
    # tcp - ничего дополнительного


def _apply_tls(out: dict, q: dict[str, list[str]], default_sni: str = "") -> None:
    security = _qs_first(q, "security", default="none").lower()
    sni = _qs_first(q, "sni", "peer", "host") or default_sni
    fp = _qs_first(q, "fp")
    alpn = _qs_first(q, "alpn")

    if security in ("tls", "reality", "xtls"):
        out["tls"] = True
        if sni:
            out["servername"] = sni
        if fp:
            out["client-fingerprint"] = fp
        if alpn:
            out["alpn"] = [a for a in unquote(alpn).split(",") if a]
    if security == "reality":
        opts = {}
        pbk = _qs_first(q, "pbk", "publicKey")
        sid = _qs_first(q, "sid", "shortId")
        spx = _qs_first(q, "spx", "spiderX")
        if pbk:
            opts["public-key"] = pbk
        if sid:
            opts["short-id"] = sid
        if spx:
            opts["support-x25519mlkem768"] = False
        out["reality-opts"] = opts

    if _truthy(_qs_first(q, "allowInsecure", "insecure", default="0")):
        out["skip-cert-verify"] = True


# --------------------------------------------------------------------------- #
# парсеры отдельных схем
# --------------------------------------------------------------------------- #


def parse_vless(uri: str) -> dict | None:
    u = urlparse(uri)
    if not u.hostname or not u.port or not u.username:
        return None
    q = parse_qs(u.query)
    out: dict = {
        "name": unquote(u.fragment) or f"{u.hostname}:{u.port}",
        "type": "vless",
        "server": u.hostname,
        "port": int(u.port),
        "uuid": u.username,
        "udp": True,
    }
    flow = _qs_first(q, "flow")
    if flow:
        out["flow"] = flow
    enc = _qs_first(q, "encryption")
    if enc and enc != "none":
        out["encryption"] = enc
    pe = _qs_first(q, "packetEncoding", "packet-encoding")
    if pe in ("xudp", "packetaddr"):
        out["packet-encoding"] = pe
    _apply_tls(out, q, default_sni=u.hostname)
    _apply_transport(out, _qs_first(q, "type", default="tcp"), q)
    return out


def parse_vmess(uri: str) -> dict | None:
    body = uri[len("vmess://") :]
    # Вариант 1: base64 от JSON (v2rayN)
    try:
        cfg = json.loads(b64decode_loose(body).decode("utf-8", "ignore"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        cfg = None

    if isinstance(cfg, dict):
        host = str(cfg.get("add", ""))
        port = cfg.get("port")
        if not host or not port:
            return None
        out: dict = {
            "name": str(cfg.get("ps") or f"{host}:{port}"),
            "type": "vmess",
            "server": host,
            "port": int(port),
            "uuid": str(cfg.get("id", "")),
            "alterId": int(cfg.get("aid", 0) or 0),
            "cipher": str(cfg.get("scy") or "auto"),
            "udp": True,
        }
        if str(cfg.get("tls", "")).lower() in ("tls", "reality", "1", "true"):
            out["tls"] = True
            sni = cfg.get("sni") or cfg.get("host") or host
            if sni:
                out["servername"] = str(sni)
            if cfg.get("fp"):
                out["client-fingerprint"] = str(cfg["fp"])
            if cfg.get("alpn"):
                out["alpn"] = [a for a in str(cfg["alpn"]).split(",") if a]
        q = {}
        if cfg.get("host"):
            q["host"] = [str(cfg["host"])]
        if cfg.get("path"):
            q["path"] = [str(cfg["path"])]
        if cfg.get("serviceName"):
            q["serviceName"] = [str(cfg["serviceName"])]
        _apply_transport(out, str(cfg.get("net") or "tcp"), q)
        return out

    # Вариант 2: vmess://uuid@host:port?... как у vless
    u = urlparse(uri)
    if not u.hostname or not u.port or not u.username:
        return None
    q = parse_qs(u.query)
    out = {
        "name": unquote(u.fragment) or f"{u.hostname}:{u.port}",
        "type": "vmess",
        "server": u.hostname,
        "port": int(u.port),
        "uuid": u.username,
        "alterId": int(_qs_first(q, "alterId", "aid", default="0") or 0),
        "cipher": _qs_first(q, "encryption", default="auto"),
        "udp": True,
    }
    _apply_tls(out, q, default_sni=u.hostname)
    _apply_transport(out, _qs_first(q, "type", default="tcp"), q)
    return out


def parse_trojan(uri: str) -> dict | None:
    u = urlparse(uri)
    if not u.hostname or not u.port or not u.username:
        return None
    q = parse_qs(u.query)
    out: dict = {
        "name": unquote(u.fragment) or f"{u.hostname}:{u.port}",
        "type": "trojan",
        "server": u.hostname,
        "port": int(u.port),
        "password": unquote(u.username),
        "udp": True,
    }
    sni = _qs_first(q, "sni", "peer")
    if sni:
        out["sni"] = sni
    fp = _qs_first(q, "fp")
    if fp:
        out["client-fingerprint"] = fp
    alpn = _qs_first(q, "alpn")
    if alpn:
        out["alpn"] = [a for a in unquote(alpn).split(",") if a]
    if _truthy(_qs_first(q, "allowInsecure", "insecure", default="0")):
        out["skip-cert-verify"] = True
    net = _qs_first(q, "type", default="tcp")
    if net != "tcp":
        _apply_transport(out, net, q)
    return out


def parse_ss(uri: str) -> dict | None:
    body = uri[len("ss://") :]
    frag = ""
    if "#" in body:
        body, frag = body.split("#", 1)
    query = ""
    if "?" in body:
        body, query = body.split("?", 1)
    q = parse_qs(query)

    if "@" in body:
        userinfo, hostpart = body.rsplit("@", 1)
        try:
            decoded = b64decode_loose(userinfo).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            decoded = unquote(userinfo)
        if ":" not in decoded:
            return None
        method, password = decoded.split(":", 1)
    else:
        try:
            decoded = b64decode_loose(body).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            return None
        if "@" not in decoded or ":" not in decoded:
            return None
        creds, hostpart = decoded.rsplit("@", 1)
        method, password = creds.split(":", 1)

    if ":" not in hostpart:
        return None
    host, port = hostpart.rsplit(":", 1)
    host = host.strip("[]")
    if not port.isdigit():
        return None

    out: dict = {
        "name": unquote(frag) or f"{host}:{port}",
        "type": "ss",
        "server": host,
        "port": int(port),
        "cipher": method,
        "password": password,
        "udp": True,
    }
    plugin = _qs_first(q, "plugin")
    if plugin:
        parts = unquote(plugin).split(";")
        pname = parts[0]
        popts = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        if pname in ("obfs-local", "simple-obfs"):
            out["plugin"] = "obfs"
            out["plugin-opts"] = {"mode": popts.get("obfs", "http"), "host": popts.get("obfs-host", "")}
        elif pname == "v2ray-plugin":
            out["plugin"] = "v2ray-plugin"
            out["plugin-opts"] = {
                "mode": popts.get("mode", "websocket"),
                "host": popts.get("host", ""),
                "path": popts.get("path", "/"),
                "tls": "tls" in popts,
            }
    return out


def parse_hysteria2(uri: str) -> dict | None:
    u = urlparse(uri)
    if not u.hostname:
        return None
    q = parse_qs(u.query)
    out: dict = {
        "name": unquote(u.fragment) or f"{u.hostname}:{u.port or 443}",
        "type": "hysteria2",
        "server": u.hostname,
        "port": int(u.port or 443),
        "password": unquote(u.username or "") or _qs_first(q, "password"),
        "udp": True,
    }
    sni = _qs_first(q, "sni", "peer")
    if sni:
        out["sni"] = sni
    obfs = _qs_first(q, "obfs")
    if obfs:
        out["obfs"] = obfs
        pw = _qs_first(q, "obfs-password")
        if pw:
            out["obfs-password"] = pw
    if _truthy(_qs_first(q, "insecure", "allowInsecure", default="0")):
        out["skip-cert-verify"] = True
    alpn = _qs_first(q, "alpn")
    if alpn:
        out["alpn"] = [a for a in unquote(alpn).split(",") if a]
    return out


def parse_tuic(uri: str) -> dict | None:
    u = urlparse(uri)
    if not u.hostname:
        return None
    q = parse_qs(u.query)
    out: dict = {
        "name": unquote(u.fragment) or f"{u.hostname}:{u.port or 443}",
        "type": "tuic",
        "server": u.hostname,
        "port": int(u.port or 443),
        "uuid": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "udp": True,
    }
    sni = _qs_first(q, "sni")
    if sni:
        out["sni"] = sni
    cc = _qs_first(q, "congestion_control", "congestion-controller")
    if cc:
        out["congestion-controller"] = cc
    udp_mode = _qs_first(q, "udp_relay_mode")
    if udp_mode:
        out["udp-relay-mode"] = udp_mode
    if _truthy(_qs_first(q, "allow_insecure", "insecure", default="0")):
        out["skip-cert-verify"] = True
    alpn = _qs_first(q, "alpn")
    if alpn:
        out["alpn"] = [a for a in unquote(alpn).split(",") if a]
    return out


SCHEME_PARSERS = {
    "vless": parse_vless,
    "vmess": parse_vmess,
    "trojan": parse_trojan,
    "ss": parse_ss,
    "hysteria2": parse_hysteria2,
    "hy2": parse_hysteria2,
    "tuic": parse_tuic,
}


def parse_uri(uri: str) -> dict | None:
    uri = uri.strip()
    if "://" not in uri:
        return None
    scheme = uri.split("://", 1)[0].lower()
    fn = SCHEME_PARSERS.get(scheme)
    if not fn:
        return None
    try:
        node = fn(uri)
    except Exception as exc:  # noqa: BLE001 - одна битая ссылка не должна ронять подписку
        log.warning("не разобрана ссылка %s...: %s", uri[:24], exc)
        return None
    if node:
        # Исходник отдаем клиентам в base64 как есть: наша обратная сборка
        # из proxy-объекта теряет то, что mihomo не описывает (xhttp, pqv, ...)
        node["_uri"] = uri
    return node


# --------------------------------------------------------------------------- #
# разбор всего тела подписки
# --------------------------------------------------------------------------- #


def _looks_like_uri_list(text: str) -> bool:
    return bool(re.search(r"^(vless|vmess|trojan|ss|hysteria2|hy2|tuic)://", text.strip(), re.MULTILINE))


def parse_subscription_body(body: str) -> list[dict]:
    """Вернуть список proxy-объектов в формате mihomo."""
    text = body.strip()
    if not text:
        return []

    # 1. Clash YAML
    if "proxies:" in text[:4096]:
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict) and isinstance(data.get("proxies"), list):
                return [p for p in data["proxies"] if isinstance(p, dict) and p.get("server")]
        except yaml.YAMLError as exc:
            log.warning("тело похоже на YAML, но не разобралось: %s", exc)

    # 2. Список ссылок в открытом виде
    if _looks_like_uri_list(text):
        return _parse_uri_lines(text)

    # 3. base64
    try:
        decoded = b64decode_loose(text).decode("utf-8", "ignore")
    except (binascii.Error, ValueError):
        decoded = ""
    if decoded:
        if "proxies:" in decoded[:4096]:
            try:
                data = yaml.safe_load(decoded)
                if isinstance(data, dict) and isinstance(data.get("proxies"), list):
                    return [p for p in data["proxies"] if isinstance(p, dict) and p.get("server")]
            except yaml.YAMLError:
                pass
        if _looks_like_uri_list(decoded):
            return _parse_uri_lines(decoded)

    return []


def _parse_uri_lines(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        node = parse_uri(line)
        if node:
            out.append(node)
    return out


def public_config(node: dict) -> dict:
    """Конфиг ноды без наших служебных ключей (_uri, _warn)."""
    return {k: v for k, v in node.items() if not str(k).startswith("_")}


def is_junk(node: dict) -> bool:
    """Заглушка от панели, а не настоящая нода."""
    name = str(node.get("name", ""))
    if JUNK_NAME_RE.search(name):
        return True
    server = str(node.get("server", ""))
    if not server or server in ("127.0.0.1", "0.0.0.0", "localhost", "example.com"):
        return True
    return False


def _routing_key(node: dict) -> str:
    """То, чем ноды на одном адресе и с одним UUID ведут в разные выходы.

    Провайдеры сажают десятки стран на один входной IP:порт и различают их
    по SNI и short-id (reality) или по host/path (ws, grpc, h2).
    """
    ws = node.get("ws-opts") or {}
    h2 = node.get("h2-opts") or {}
    http = node.get("http-opts") or {}
    xh = node.get("xhttp-opts") or {}
    parts = {
        "net": node.get("network") or "",
        "sni": node.get("servername") or node.get("sni") or "",
        "sid": (node.get("reality-opts") or {}).get("short-id") or "",
        "path": ws.get("path") or h2.get("path") or http.get("path") or xh.get("path") or "",
        "host": (ws.get("headers") or {}).get("Host") or h2.get("host")
                or (http.get("headers") or {}).get("Host") or xh.get("host") or "",
        "grpc": (node.get("grpc-opts") or {}).get("grpc-service-name") or "",
    }
    if not any(parts.values()):
        return ""
    return json.dumps(parts, sort_keys=True, ensure_ascii=False)


def fingerprint(node: dict) -> str:
    """Стабильный отпечаток для дедупликации между подписками."""
    secret = str(
        node.get("uuid") or node.get("password") or node.get("private-key") or node.get("cipher") or ""
    )
    base = f"{node.get('type')}|{node.get('server')}|{node.get('port')}|{secret}|{_routing_key(node)}"
    return hashlib.sha1(base.encode()).hexdigest()[:32]
