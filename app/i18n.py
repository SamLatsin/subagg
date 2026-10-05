"""Перевод админки. Ключ - русская строка, как в шаблонах: _('Имя').

Язык берется из cookie lang, переключатель в шапке ставит его через /lang/{code}.
Строки с подстановками переводятся целиком, а .format() зовется уже на
переводе: _('до {date}').format(date=...).
"""

from __future__ import annotations

from contextvars import ContextVar

LANGS = ("ru", "en")
DEFAULT_LANG = "ru"

current_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT_LANG)


def gettext(text: str) -> str:
    if current_lang.get() == "en":
        return EN.get(text, text)
    return text


EN: dict[str, str] = {
    # шапка и фоновые операции
    "Агрегатор подписок. Обновление каждые {fetch} мин, проверка нод каждые {check} мин.":
        "Subscription aggregator. Refresh every {fetch} min, node check every {check} min.",
    "{title} идет в фоне. Страница обновится сама, когда закончится.":
        "{title} is running in the background. The page will reload when it finishes.",
    "{title}: ошибка.": "{title} failed.",
    "Обновление подписок": "Subscription refresh",
    "Проверка нод": "Node check",
    "Все ноды из подписок плюс свои": "All nodes from subscriptions plus your own",
    "нод всего": "nodes total",
    "Прошли последнюю проверку": "Passed the last check",
    "живых": "alive",
    "Ноды с выходом в России": "Nodes exiting in Russia",
    "Ноды, где разрешены торренты": "Nodes where torrents are allowed",
    "торрент": "torrent",
    "Добавленные вручную в разделе «Свои ноды»": "Added by hand in the “Own nodes” section",
    "своих": "own",
    "Заново скачать все включенные подписки и обновить список нод. Идет в фоне":
        "Download all enabled subscriptions again and update the node list. Runs in the background",
    "Обновить подписки": "Refresh subscriptions",
    "Проверить все ноды через ядро mihomo. Идет в фоне, можно уйти со страницы":
        "Check all nodes through the mihomo core. Runs in the background, you can leave the page",
    "Проверить ноды": "Check nodes",
    "обе операции идут в фоне, страницу можно не ждать": "both run in the background, no need to wait",
    "Скопировано": "Copied",

    # подписки
    "Подписки": "Subscriptions",
    "Имя": "Name",
    "Нод": "Nodes",
    "UA / заголовки": "UA / headers",
    "Обновлена": "Updated",
    "Статус": "Status",
    "выкл": "off",
    "Принудительный тег: ставится всем нодам подписки": "Forced tag: added to every node of the subscription",
    "Дата окончания подписки у провайдера": "Subscription expiry date at the provider",
    "до {date}": "until {date}",
    "ок": "ok",
    "Скачать эту подписку заново прямо сейчас": "Download this subscription again right now",
    "Обновить": "Refresh",
    "Перестать обновлять подписку. Уже скачанные ноды останутся":
        "Stop refreshing this subscription. Nodes already downloaded stay",
    "Выключить": "Disable",
    "Снова обновлять подписку по расписанию": "Refresh this subscription on schedule again",
    "Включить": "Enable",
    "Удалить подписку «{name}» и её ноды?": "Delete subscription “{name}” and its nodes?",
    "Удалить подписку вместе со всеми её нодами": "Delete the subscription with all its nodes",
    "Удалить": "Delete",
    "Пока пусто": "Nothing yet",
    "Добавить подписку": "Add subscription",
    "Каким клиентом представляться провайдеру": "Which client to present as to the provider",
    "Принудительные теги": "Forced tags",
    "Через запятую. Добавятся ко всем нодам подписки": "Comma separated. Added to every node of the subscription",
    "Заголовки, JSON": "Headers, JSON",
    "Добавить": "Add",
    "подписка отдала 0 пригодных нод, старый список сохранен":
        "subscription returned 0 usable nodes, the previous list was kept",

    # свои ноды
    "Свои ноды": "Own nodes",
    "Добавить: ссылки, clash YAML или xray outbound JSON": "Add: links, clash YAML or xray outbound JSON",
    "Теги": "Tags",
    "Через запятую. Тег MY ставится своим нодам всегда": "Comma separated. Own nodes always get the MY tag",

    # цели проверки
    "Цели проверки": "Check targets",
    "Код": "Code",
    "Обязательная": "Required",
    "да": "yes",
    "нет": "no",
    "Поменять имя, адрес, код ответа или таймаут цели": "Change the target name, URL, expected code or timeout",
    "Изменить": "Edit",
    "Не использовать эту цель при проверке нод": "Do not use this target when checking nodes",
    "Снова проверять ноды на этой цели": "Use this target for node checks again",
    "Удалить цель «{name}»?": "Delete target “{name}”?",
    "Удалить цель проверки": "Delete the check target",
    "Ожидаемый код": "Expected code",
    "Таймаут, мс": "Timeout, ms",
    "Если эта цель не прошла, нода считается мертвой": "If this target fails, the node is considered dead",
    "обязательная": "required",
    "Сохранить": "Save",
    "Отмена": "Cancel",
    "Добавить цель": "Add target",
    "Нода считается живой, если прошли все обязательные цели и хотя бы одна любая. "
    "Мертвой становится после {n} неудачных прогонов подряд.":
        "A node is alive if all required targets and at least one target of any kind pass. "
        "It is marked dead after {n} failed runs in a row.",
    "нет нод или нет включенных целей проверки": "no nodes or no enabled check targets",

    # токены
    "Токены выдачи": "Access tokens",
    "Один токен на человека или устройство. «Настроить» открывает страницу токена: "
    "там видно, какие серверы он получает, и их можно выключать по одному.":
        "One token per person or device. “Configure” opens the token page: it shows which "
        "servers the token gets, and you can turn them off one by one.",
    "Ссылка": "Link",
    "Формат": "Format",
    "Фильтры": "Filters",
    "Использован": "Last used",
    "приостановлен": "paused",
    "Столько нод выключено вручную на странице токена": "Number of nodes turned off by hand on the token page",
    "−{n} вручную": "−{n} by hand",
    "Скопировать ссылку, чтобы отправить человеку": "Copy the link to send it to someone",
    "Копировать": "Copy",
    "Мертвые ноды в выдачу не попадают": "Dead nodes are not served",
    "только живые": "alive only",
    "{n} раз": "{n} times",
    "Какие серверы получает этот токен, включить и выключить отдельные, поменять фильтры":
        "See which servers this token gets, turn single ones on and off, change filters",
    "Настроить": "Configure",
    "Удалить токен «{name}»? Ссылка перестанет работать.": "Delete token “{name}”? The link will stop working.",
    "Удалить токен. Ссылка у человека перестанет работать": "Delete the token. The link will stop working",
    "Токенов нет, устройства ничего не получат": "No tokens, devices will get nothing",
    "Создать токен": "Create token",
    "clash YAML для mihomo/Clash/Nikki, base64 для v2rayN, Hiddify, Happ, Streisand и т.п.":
        "clash YAML for mihomo/Clash/Nikki, base64 for v2rayN, Hiddify, Happ, Streisand etc.",
    "список ссылок": "link list",
    "Отдавать только ноды, чье имя подходит под выражение. Пусто - все":
        "Serve only nodes whose name matches. Empty means all",
    "Не отдавать ноды, чье имя подходит под выражение": "Do not serve nodes whose name matches",
    "Токен или старая ссылка": "Token or old link",
    "пусто - сгенерируется случайный": "empty means a random one",
    "Чтобы восстановить выданную раньше ссылку, вставь ее целиком или только часть после /sub/":
        "To restore a link you handed out before, paste it whole or just the part after /sub/",
    "Заполняй только при восстановлении: вставь старую ссылку, и у людей все заработает без перенастройки.":
        "Fill in only when restoring: paste the old link and clients keep working without changes.",
    "Создать": "Create",

    # ноды
    "Ноды": "Nodes",
    "«Выключить» убирает ноду из выдачи всех токенов. Чтобы убрать ее только у одного человека, "
    "используй страницу токена.":
        "“Disable” removes the node from every token. To remove it for one person only, "
        "use the token page.",
    "Сервер": "Server",
    "Задержка": "Latency",
    "{n} мс": "{n} ms",
    "выключена": "disabled",
    "жива": "alive",
    "не проверялась": "not checked yet",
    "Новая нода, проверки еще не было. До первой проверки отдается как живая":
        "New node, not checked yet. Served as alive until the first check",
    "сбоит, {k} из {n}": "flapping, {k} of {n}",
    "Последние проверки провалились, но нода еще отдается: мертвой станет после {n} провалов подряд":
        "Recent checks failed but the node is still served: it is marked dead after {n} failures in a row",
    "мертва": "dead",
    "Отдается всегда, даже если проверка говорит, что мертва": "Always served, even if the check says it is dead",
    "всегда": "always",
    "не прошла:": "failed:",
    "Убрать ноду из выдачи всех токенов и не проверять ее": "Remove the node from every token and stop checking it",
    "Вернуть ноду в выдачу": "Serve the node again",
    "Снова учитывать проверку: мертвая нода пропадет из выдачи токенов «только живые»":
        "Respect checks again: a dead node disappears from “alive only” tokens",
    "Открепить": "Unpin",
    "Отдавать ноду всегда, даже если проверка считает ее мертвой. Полезно для whitelist-нод, "
    "которые отсюда не проверить":
        "Always serve the node, even if the check says it is dead. Useful for whitelist nodes "
        "that cannot be checked from here",
    "Всегда отдавать": "Always serve",
    "Пинг": "Ping",
    "Источник": "Source",
    "свои": "own",
    "выход": "exit",
    "заявлена {claimed}, выход": "claims {claimed}, exits in",
    "Реальный IP выхода и его страна по геобазе: так ноду видят сайты":
        "Actual exit IP and its country by GeoIP: this is how websites see the node",
    "Проверяю…": "Checking…",
    "Проверить только эту ноду прямо сейчас: задержка и какие цели не прошли. Несколько секунд":
        "Check just this node right now: latency and which targets fail. Takes a few seconds",
    "Идет другая проверка, попробуй через минуту": "Another check is running, try again in a minute",
    "Удалить свою ноду «{name}»?": "Delete own node “{name}”?",
    "Удалить свою ноду насовсем": "Delete this own node for good",

    # страница токена
    "к админке": "back to admin",
    "Токен «{name}»": "Token “{name}”",
    "Сейчас отдает {given} из {total} нод.": "Currently serves {given} of {total} nodes.",
    "Последний запрос: {when}": "Last request: {when}",
    "с {ip}": "from {ip}",
    "всего {n} раз.": "{n} times total.",
    "Запросов еще не было.": "No requests yet.",
    "Ссылка начнет отвечать 404. Настройки сохранятся, можно включить обратно":
        "The link will answer 404. Settings are kept, you can resume later",
    "Приостановить": "Pause",
    "Ссылка снова заработает": "The link will work again",
    "Возобновить": "Resume",
    "Изменения на этой странице ссылку не меняют. Устройство получит новый список при следующем "
    "обновлении подписки у себя (или сразу, если обновить вручную).":
        "Changes on this page keep the link the same. The device gets the new list on its next "
        "subscription update (or right away if refreshed by hand).",
    "Настройки": "Settings",
    "Токен в ссылке": "Token in the link",
    "Часть ссылки после /sub/. Можно вставить ссылку целиком": "The part of the link after /sub/. You can paste the whole link",
    "Если поменять токен, старая ссылка перестанет работать у всех, кому ее раздали. Меняй, только "
    "чтобы восстановить прежнюю ссылку после потери базы.":
        "Changing the token breaks the old link for everyone who has it. Change it only to restore "
        "a previous link after losing the database.",
    "Сохранить настройки": "Save settings",
    "Серверы": "Servers",
    "Порядок как в выдаче: сначала WL, потом свои, дальше по стране и задержке. Снятая галочка "
    "выключает ноду только для этого токена. Новые ноды из подписок включаются сами. Серые строки "
    "не уходят в выдачу по другой причине, она написана справа.":
        "Same order as served: WL first, then own, then by country and latency. Unchecking turns "
        "the node off for this token only. New nodes from subscriptions are on by default. Grey rows "
        "are not served for another reason, shown on the right.",
    "Поставить все галочки": "Check all",
    "Отметить все": "Select all",
    "Снять все галочки": "Uncheck all",
    "Снять все": "Select none",
    "поиск по имени": "search by name",
    "Фильтрует список на странице. «Отметить все» и «Снять все» действуют только на видимые строки":
        "Filters the list on the page. “Select all” and “Select none” affect visible rows only",
    "Применить галочки": "Apply the checkboxes",
    "Сохранить выбор": "Save selection",
    "В выдаче": "Served",
    "Отдавать эту ноду по этому токену": "Serve this node to this token",
    "нет:": "no:",
    "выключена для всех": "disabled for everyone",
    "выключена для этого токена": "disabled for this token",
    "мертвая, а токен отдает только живые": "dead, and the token serves alive only",
    "не подходит под include": "does not match include",
    "попала под exclude": "matches exclude",

    # ошибки
    "неверный логин или пароль": "wrong login or password",
    "токен: от 8 до 64 символов, только латиница, цифры, - и _":
        "token: 8 to 64 characters, Latin letters, digits, - and _ only",
    "токен уже занят токеном «{name}»": "token is already used by “{name}”",
    "{field}: кривой regex: {error}": "{field}: invalid regex: {error}",
    "заголовки: {error}": "headers: {error}",
    "ожидается JSON-объект": "a JSON object is expected",
    "не разобран JSON: {error}": "could not parse JSON: {error}",
    "ничего не разобрано из вставленного текста": "nothing could be parsed from the pasted text",
    "нет такой подписки": "no such subscription",
    "нет такой ноды": "no such node",
    "нет такой цели": "no such target",
    "нет такого токена": "no such token",
    "цель с таким именем уже есть": "a target with this name already exists",
}
