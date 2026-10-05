"""Единые имена и теги для нод из разных подписок.

Итоговое имя: ``[RU][WL] 🇷🇺 Россия · s1-07``
Фильтры на роутере тогда сводятся к ``^\\[RU\\]``, ``\\[TOR\\]``, ``\\[MY\\]``.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- #
# страны
# --------------------------------------------------------------------------- #

COUNTRY_NAMES: dict[str, str] = {
    "RU": "Россия",
    "BY": "Беларусь",
    "KZ": "Казахстан",
    "UZ": "Узбекистан",
    "AZ": "Азербайджан",
    "AM": "Армения",
    "GE": "Грузия",
    "UA": "Украина",
    "DE": "Германия",
    "NL": "Нидерланды",
    "FR": "Франция",
    "GB": "Британия",
    "US": "США",
    "CA": "Канада",
    "FI": "Финляндия",
    "SE": "Швеция",
    "NO": "Норвегия",
    "DK": "Дания",
    "PL": "Польша",
    "LT": "Литва",
    "LV": "Латвия",
    "EE": "Эстония",
    "CZ": "Чехия",
    "SK": "Словакия",
    "AT": "Австрия",
    "CH": "Швейцария",
    "IT": "Италия",
    "ES": "Испания",
    "PT": "Португалия",
    "GR": "Греция",
    "TR": "Турция",
    "RO": "Румыния",
    "BG": "Болгария",
    "HU": "Венгрия",
    "HR": "Хорватия",
    "RS": "Сербия",
    "SI": "Словения",
    "IE": "Ирландия",
    "BE": "Бельгия",
    "LU": "Люксембург",
    "IL": "Израиль",
    "AE": "ОАЭ",
    "SA": "Саудовская Аравия",
    "IN": "Индия",
    "JP": "Япония",
    "KR": "Южная Корея",
    "CN": "Китай",
    "HK": "Гонконг",
    "TW": "Тайвань",
    "SG": "Сингапур",
    "MY": "Малайзия",
    "TH": "Таиланд",
    "VN": "Вьетнам",
    "ID": "Индонезия",
    "AU": "Австралия",
    "NZ": "Новая Зеландия",
    "BR": "Бразилия",
    "AR": "Аргентина",
    "CL": "Чили",
    "CO": "Колумбия",
    "PE": "Перу",
    "MX": "Мексика",
    "ZA": "ЮАР",
    "NG": "Нигерия",
    "EG": "Египет",
    "KE": "Кения",
    "MD": "Молдова",
    "CY": "Кипр",
    "IS": "Исландия",
    "EU": "Европа",
}

# Ключевые слова в названии ноды, если флага нет
NAME_HINTS: list[tuple[str, str]] = [
    (r"росси|russia|moscow|моск|санкт|спб|\bru\b|\brus\b", "RU"),
    (r"белар|belarus|минск|\bby\b", "BY"),
    (r"казахс|kazakh|алмат|астан|\bkz\b", "KZ"),
    (r"узбек|uzbek|ташкент|\buz\b", "UZ"),
    (r"азербайдж|azerbaij|баку|\baz\b", "AZ"),
    (r"армени|armenia|ереван|\bam\b", "AM"),
    (r"грузи|georgia|тбилиси", "GE"),
    (r"украин|ukrain|киев|kyiv", "UA"),
    (r"герман|german|франкфурт|frankfurt|берлин|berlin|\bde\b", "DE"),
    (r"нидерл|netherl|амстердам|amsterdam|holland|\bnl\b", "NL"),
    (r"франц|france|париж|paris|\bfr\b", "FR"),
    (r"британ|britain|england|лондон|london|united kingdom|\bgb\b|\buk\b", "GB"),
    (r"\bсша\b|\busa\b|united states|нью-йорк|new york|майами|miami|лос-андж|los ang|"
     r"хьюстон|houston|сан-хосе|san jose|даллас|dallas|сиэтл|seattle|чикаго|chicago", "US"),
    (r"канад|canada|торонто|toronto|монреаль|montreal", "CA"),
    (r"финлянд|finland|хельсинки|helsinki|оулу|oulu", "FI"),
    (r"швеци|sweden|стокгольм|stockholm", "SE"),
    (r"норвег|norway|осло|oslo", "NO"),
    (r"дани|denmark|копенгаген|copenhagen", "DK"),
    (r"польш|poland|варшав|warsaw", "PL"),
    (r"литв|lithuania|вильнюс|vilnius", "LT"),
    (r"латви|latvia|рига|riga", "LV"),
    (r"эстони|estonia|таллин|tallinn", "EE"),
    (r"чехи|czech|прага|prague", "CZ"),
    (r"словаки|slovakia|братислав|bratislava", "SK"),
    (r"австри|austria|вена|vienna", "AT"),
    (r"швейцар|switzerl|цюрих|zurich|женев|geneva", "CH"),
    (r"итали|italy|милан|milan|рим\b|rome", "IT"),
    (r"испани|spain|мадрид|madrid|барселон|barcelona", "ES"),
    (r"португал|portugal|лиссабон|lisbon", "PT"),
    (r"греци|greece|афины|athens", "GR"),
    (r"турци|turkey|стамбул|istanbul|türkiye", "TR"),
    (r"румыни|romania|бухарест|bucharest", "RO"),
    (r"болгари|bulgaria|софия|sofia", "BG"),
    (r"венгри|hungary|будапешт|budapest", "HU"),
    (r"хорвати|croatia|загреб|zagreb", "HR"),
    (r"ирланди|ireland|дублин|dublin", "IE"),
    (r"бельги|belgium|брюссель|brussels", "BE"),
    (r"люксембург|luxembourg", "LU"),
    (r"израил|israel|тель-авив|tel aviv", "IL"),
    (r"оаэ|\buae\b|эмират|dubai|дубай|фуджейра|fujairah", "AE"),
    (r"япони|japan|токио|tokyo", "JP"),
    (r"корея|korea|сеул|seoul", "KR"),
    (r"гонконг|hong kong", "HK"),
    (r"сингапур|singapore", "SG"),
    (r"малайзи|malaysia|куала", "MY"),
    (r"австрали|australia|сидней|sydney", "AU"),
    (r"бразил|brazil|сан-паулу|sao paulo", "BR"),
    (r"аргентин|argentina|буэнос", "AR"),
    (r"колумби|colombia|богота|bogota", "CO"),
    (r"перу\b|peru|лима\b|lima", "PE"),
    (r"мексик|mexico|керетаро|queretaro", "MX"),
    (r"юар\b|south africa|йоханнесбург|johannesburg", "ZA"),
    (r"нигери|nigeria|лагос|lagos", "NG"),
    (r"индонези|indonesia|джакарта", "ID"),
    (r"индия|india|мумбаи|mumbai", "IN"),
    (r"китай|china|шанхай|shanghai", "CN"),
]

_HINT_RES = [(re.compile(p, re.IGNORECASE), c) for p, c in NAME_HINTS]

# Флаг - две буквы Regional Indicator, U+1F1E6..U+1F1FF
_FLAG_RE = re.compile("[\U0001f1e6-\U0001f1ff]{2}")


def country_from_flag(name: str) -> str | None:
    m = _FLAG_RE.search(name)
    if not m:
        return None
    return "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in m.group(0))


def country_from_name(name: str) -> str | None:
    for rx, code in _HINT_RES:
        if rx.search(name):
            return code
    return None


def detect_country(name: str) -> str | None:
    return country_from_flag(name) or country_from_name(name)


def flag_emoji(code: str) -> str:
    if not code or len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(ord(c) - ord("A") + 0x1F1E6) for c in code.upper())


# --------------------------------------------------------------------------- #
# теги
# --------------------------------------------------------------------------- #

TORRENT_RE = re.compile(r"torrent|торрент|µtorrent|utorrent|p2p", re.IGNORECASE)
WHITELIST_RE = re.compile(r"whitelist|вайтлист|белый\s*список|обход|\blte\b|мобильн", re.IGNORECASE)
GAME_RE = re.compile(r"игров|gaming|game\b", re.IGNORECASE)


def detect_tags(name: str, country: str | None, source: str, force: list[str] | None = None) -> list[str]:
    tags: list[str] = []
    if source == "own":
        tags.append("MY")
    elif country == "RU":
        tags.append("RU")
    else:
        tags.append("XX")

    if WHITELIST_RE.search(name):
        tags.append("WL")
    if TORRENT_RE.search(name):
        tags.append("TOR")
    if GAME_RE.search(name):
        tags.append("GAME")

    for t in force or []:
        t = t.strip().upper()
        if t and t not in tags:
            tags.append(t)

    # порядок: сначала регион, потом свойства
    order = {"RU": 0, "XX": 0, "MY": 0, "WL": 1, "TOR": 2, "GAME": 3}
    return sorted(dict.fromkeys(tags), key=lambda t: (order.get(t, 9), t))


# --------------------------------------------------------------------------- #
# имя
# --------------------------------------------------------------------------- #

_CLEAN_RE = re.compile(r"[\U0001f000-\U0001faff☀-➿️]")


def _clean_label(name: str) -> str:
    s = _CLEAN_RE.sub("", name)
    s = re.sub(r"\s*[|·,/]\s*", " · ", s)
    s = re.sub(r"\s+", " ", s).strip(" ·-")
    return unicodedata.normalize("NFKC", s)


def build_display_name(
    original: str,
    country: str | None,
    tags: list[str],
    slot: str,
) -> str:
    """slot - короткий суффикс вида s1-07, чтобы имена были уникальны."""
    tagstr = "".join(f"[{t}]" for t in tags)
    code = country or "??"
    label = COUNTRY_NAMES.get(code, _clean_label(original)[:24] or code)
    flag = flag_emoji(code)
    parts = [tagstr, flag, label, f"· {slot}"]
    return " ".join(p for p in parts if p)


def normalize_node(node: dict, source: str, slot: str, force_tags: list[str] | None = None) -> dict:
    """Вернуть {country, tags, display_name} для ноды."""
    original = str(node.get("name", ""))
    country = detect_country(original)
    tags = detect_tags(original, country, source, force_tags)
    return {
        "country": country,
        "tags": tags,
        "display_name": build_display_name(original, country, tags, slot),
    }
