import os
import re
import time
import html
import hashlib
import datetime
import urllib.parse
from typing import List, Dict, Any, Callable, Optional
import feedparser
import httpx
from bs4 import BeautifulSoup

from app.news_processor import is_news_post, deduplicate_news, clean_text_for_anchor, smart_truncate_anchor_text, strip_emojis_and_smileys
from app.llm_client import query_llm

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

DEFAULT_DYNAMIC_QUERIES = [
    "Тутаев",
    "Тутаев главные новости",
    "город Тутаев",
    "Тутаевский район ЖКХ городское хозяйство",
    "Тутаев происшествия ДТП ЧП полиция МЧС",
    "Тутаев афиша мероприятий культура выставки концерты",
    "Тутаев спорт соревнования турниры",
    "Тутаев здравоохранение ЦРБ больница",
    "Ярославская область Тутаев решения губернатора дороги",
    "Константиновский Фоминское Чебаково Пшеничище Тутаев",
    "Левобережный Тутаев"
]

def parse_date_to_utc(date_val: Any) -> Optional[datetime.datetime]:
    if not date_val:
        return None
    if isinstance(date_val, datetime.datetime):
        if date_val.tzinfo is None:
            return date_val.replace(tzinfo=datetime.timezone.utc)
        return date_val.astimezone(datetime.timezone.utc)
    if isinstance(date_val, time.struct_time):
        try:
            return datetime.datetime(*date_val[:6], tzinfo=datetime.timezone.utc)
        except Exception:
            return None
    if isinstance(date_val, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(date_val, tz=datetime.timezone.utc)
        except Exception:
            return None
    if isinstance(date_val, str):
        # 1. ISO format
        try:
            dt = datetime.datetime.fromisoformat(date_val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            pass
        # 2. RFC 822 format (e.g. 'Wed, 07 Oct 2026 12:00:00 GMT')
        try:
            import email.utils
            dt = email.utils.parsedate_to_datetime(date_val)
            if dt:
                return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            pass
        # 3. '%d.%m.%Y' etc.
        for fmt in ["%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                dt = datetime.datetime.strptime(date_val, fmt).replace(tzinfo=datetime.timezone.utc)
                return dt
            except Exception:
                pass
    return None

def is_within_days(date_val: Any, days: int = 10) -> bool:
    """Проверка, что новость опубликована строго в диапазоне последних `days` дней (от 7 до 10 дней)"""
    dt = parse_date_to_utc(date_val)
    if not dt:
        return True
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    return cutoff <= dt <= (now + datetime.timedelta(days=2))

def detect_rubric(title: str, text: str) -> str:
    """Определение рубрики по ключевым тематическим направлениям ТЗ"""
    combined = f"{title} {text}".lower()
    if any(k in combined for k in ["дтп", "мчс", "пожар", "суд", "следственн", "полиц", "краж", "арест", "погиб", "пострадал", "уголовн"]):
        return "Происшествия"
    if any(k in combined for k in ["теплосет", "отоплен", "жкх", "дорог", "водоканал", "водоснабжен", "капремонт", "благоустройств", "переправ"]):
        return "ЖКХ"
    if any(k in combined for k in ["губернатор", "евраев", "правительств", "администрац", "власт", "депутат", "закон", "тариф"]):
        return "Власть"
    if any(k in combined for k in ["спорт", "турнир", "самбо", "кубок", "матч", "чемпионат", "волейбол", "футбол", "соревнован"]):
        return "Спорт"
    if any(k in combined for k in ["афиша", "выставк", "концерт", "спектакл", "фестивал", "дк ", "билет", "сеанс"]):
        return "Афиша"
    if any(k in combined for k in ["культура", "музей", "собор", "храм", "библиотек", "праздник", "малов"]):
        return "Культура"
    return "Новости"

def build_anchor_fallback_text(title: str, raw_text: str, source: str) -> str:
    """
    Принудительная локальная адаптация новости под диктора до 1000 знаков,
    если нейросеть временно недоступна, исчерпала лимиты 429 или отклонила.
    """
    cleaned = strip_emojis_and_smileys(clean_text_for_anchor(raw_text)).replace('\u200b', '').strip()
    title_clean = strip_emojis_and_smileys(title).replace('\u200b', '').strip()

    if not cleaned or len(cleaned) < 50:
        full_text = title_clean
    elif not cleaned.startswith(title_clean[:30]) and len(cleaned) < 650:
        full_text = f"{title_clean}. {cleaned}"
    else:
        full_text = cleaned

    # Срезаем обращения к зрителям
    full_text = re.sub(
        r'^(здравствуйте|добрый (день|вечер|утро)|в эфире [^.\n]+|приветствуем вас)[,!.\s]+',
        '',
        full_text,
        flags=re.IGNORECASE
    ).strip()

    return smart_truncate_anchor_text(strip_emojis_and_smileys(full_text), 1000)

def fetch_google_news_dynamic(query: str, days_back: int = 10) -> List[Dict[str, Any]]:
    """Динамический сбор свежих новостей через Google News с фильтрацией строго за 7-10 дней"""
    items = []
    try:
        encoded_q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_q}+when:{days_back}d&hl=ru&gl=RU&ceid=RU:ru"
        feed = feedparser.parse(url)

        for entry in feed.entries:
            title_full = (entry.title or "").replace('\u200b', '').strip()
            source_name = "СМИ"
            clean_title = title_full
            if " - " in title_full:
                parts = title_full.rsplit(" - ", 1)
                clean_title = parts[0].strip()
                source_name = parts[1].strip()

            # Проверка даты публикации строго за последние 10 дней
            entry_dt = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try:
                    entry_dt = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
                except Exception:
                    pass
            if not entry_dt:
                entry_dt = parse_date_to_utc(getattr(entry, 'published', ''))

            if entry_dt and not is_within_days(entry_dt, days=days_back):
                continue

            link = getattr(entry, "link", "")
            summary = getattr(entry, "summary", "")
            clean_summary = ""
            if summary:
                clean_summary = BeautifulSoup(summary, "html.parser").get_text(separator=" ").replace('\u200b', '').strip()

            pub_str = entry_dt.strftime("%d.%m.%Y %H:%M") if entry_dt else datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
            news_id = hashlib.md5(f"{clean_title}_{link}".encode("utf-8")).hexdigest()[:12]

            items.append({
                "id": news_id,
                "title": clean_title,
                "raw_text": f"{clean_title}. {clean_summary}".strip(),
                "link": link,
                "source": source_name,
                "published_at": pub_str,
                "source_images": []
            })
    except Exception as e:
        print(f"Error fetching Google News for '{query}': {e}")

    return items

def fetch_vk_newsfeed_dynamic(query: str, vk_token: str, days_back: int = 10) -> List[Dict[str, Any]]:
    """Динамический поиск новостей по всей соцсети ВКонтакте за последние 7-10 дней"""
    if not vk_token:
        return []

    items = []
    try:
        start_time = int(time.time() - (days_back * 86400))
        params = {
            "q": query,
            "count": 25,
            "start_time": start_time,
            "access_token": vk_token,
            "v": "5.199"
        }
        url = "https://api.vk.com/method/newsfeed.search"
        headers = {"User-Agent": USER_AGENT}

        with httpx.Client(headers=headers, timeout=8.0, verify=False) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                vk_items = data.get("response", {}).get("items", [])
                for post in vk_items:
                    post_ts = post.get("date", 0)
                    if post_ts and not is_within_days(post_ts, days=days_back):
                        continue

                    text = post.get("text", "").replace('\u200b', '').strip()
                    if len(text) < 50:
                        continue

                    # Извлекаем фото
                    photos = []
                    for att in post.get("attachments", []):
                        if att.get("type") == "photo" and "photo" in att:
                            sizes = att["photo"].get("sizes", [])
                            if sizes:
                                best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
                                photos.append(best["url"])

                    post_id = f"vk_{post.get('owner_id', 0)}_{post.get('id', 0)}"
                    news_id = hashlib.md5(post_id.encode("utf-8")).hexdigest()[:12]

                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    title = lines[0][:80] if lines else "Новость ВКонтакте"

                    candidate_item = {
                        "id": news_id,
                        "title": title,
                        "raw_text": text,
                        "link": f"https://vk.com/wall{post.get('owner_id')}_{post.get('id')}",
                        "source": "ВКонтакте (Поиск)",
                        "published_at": datetime.datetime.fromtimestamp(post_ts, tz=datetime.timezone.utc).strftime("%d.%m.%Y %H:%M"),
                        "source_images": photos
                    }
                    if is_news_post(candidate_item):
                        items.append(candidate_item)
    except Exception as e:
        print(f"Error searching VK for '{query}': {e}")

    return items

EDITORIAL_DIRECTIVE = """1. ГЕОГРАФИЯ И ФИЛЬТР ОТБОРА
Материал принимается в работу ТОЛЬКО если он удовлетворяет одному из условий:
- Прямое отношение: события, решения, происшествия, люди в г. Тутаев (правый/левый берег) и населённых пунктах района (п. Константиновский, п. Фоминское, с. Чебаково, с. Пшеничище и др.).
- Региональное значение: общеобластные решения губернатора и правительства Ярославской области, изменения законов, проверки МЧС или тарифы ЖКХ, которые напрямую затрагивают жителей Тутаевского района.

2. ТЕМАТИЧЕСКИЕ НАПРАВЛЕНИЯ
- Происшествия и безопасность: сводки МЧС, ГИБДД, следственного комитета, судов.
- ЖКХ и городская среда: отопительный сезон, ремонты теплосетей, дорог, благоустройство, работа паромной/теплоходной переправы.
- Социальная сфера: здравоохранение (Тутаевская ЦРБ), образование (школы, детсады).
- Культура, спорт и досуг: анонсы и итоги событий РДК им. Малова, ДК «Восход», молодежного центра «Галактика», сельских ДК, спортивных секций и турниров.
- Афиша: только конкретные даты, места и условия входа на ближайшие дни.

3. ПРАВИЛА АДАПТАЦИИ ПОД ДИКТОРА
- Объём: строго 800–1000 знаков с пробелами (хронометраж ~45–60 секунд).
- Стиль: телевизионная устная речь. Текст пишется для чтения вслух: фразы динамичные, без канцелярщины, сложных причастных оборотов и нагромождения придаточных. В сокращенную новость не надо добавлять обращений к зрителям. Сухая информация по теме новости и все.
- Числительные: сложные числа и даты прописывать словами так, как их должен произнести диктор («седьмого октября», «более тридцати лет», «двенадцать тысяч рублей»).
- Структура:
  1. Лид (первое предложение) — сразу суть и главный факт («хук»), отвечающий на вопрос «что случилось?».
  2. Развитие темы — детали, контекст, предыстория, официальные комментарии.
  3. Финал — текущий статус, последствия, контактная информация (если афиша) или дата следующего этапа событий.

4. СТРУКТУРА ВЫХОДНОГО ОТВЕТА (ДЛЯ КАЖДОЙ НОВОСТИ)
Выводи каждую новость строго в следующем формате:
---
ЗАГОЛОВОК: [Краткий ёмкий заголовок до 60 символов]
РУБРИКА: [Происшествия / ЖКХ / Власть / Культура / Спорт / Афиша]
ХРОНОМЕТРАЖ: [~45–60 сек]

ТЕКСТ ДЛЯ ДИКТОРА:
[Текст строго от 800 до 1000 знаков с пробелами, полностью адаптированный под выразительную устную начитку].
---"""

def parse_editorial_block(block_text: str) -> Optional[Dict[str, Any]]:
    """Парсинг блока новости по строгому формату ТЗ"""
    if not block_text or "ОТКЛОНЕНО" in block_text.upper():
        return None

    title_m = re.search(r'ЗАГОЛОВОК:\s*([^\n\r]+)', block_text, re.IGNORECASE)
    rubric_m = re.search(r'РУБРИКА:\s*([^\n\r]+)', block_text, re.IGNORECASE)
    timing_m = re.search(r'ХРОНОМЕТРАЖ:\s*([^\n\r]+)', block_text, re.IGNORECASE)
    source_m = re.search(r'ИСТОЧНИК:\s*([^\n\r]+)', block_text, re.IGNORECASE)
    text_m = re.search(r'ТЕКСТ ДЛЯ ДИКТОРА:\s*([\s\S]+)', block_text, re.IGNORECASE)

    if not text_m:
        return None

    raw_body = text_m.group(1).strip()
    # Очищаем завершающие разделители
    raw_body = re.sub(r'\n---.*$', '', raw_body).strip()

    # Срезаем случайные приветствия ведущих
    clean_body = re.sub(
        r'^(здравствуйте|добрый (день|вечер|утро)|в эфире [^.\n]+|приветствуем вас)[,!.\s]+',
        '',
        raw_body,
        flags=re.IGNORECASE
    ).strip()

    # Очистка от эмодзи и смайликов
    clean_body = strip_emojis_and_smileys(clean_body)

    if len(clean_body) < 180:
        return None

    # Ограничение ровно до 1000 знаков
    if len(clean_body) > 1000:
        clean_body = smart_truncate_anchor_text(clean_body, 1000)

    title = strip_emojis_and_smileys(title_m.group(1).strip()) if title_m else ""
    rubric = rubric_m.group(1).strip() if rubric_m else "Новости"
    timing = timing_m.group(1).strip() if timing_m else "~45–60 сек"
    source = source_m.group(1).strip() if source_m else "СМИ"

    return {
        "title": title[:70] if title else "Новость региона",
        "rubric": rubric,
        "timing": timing,
        "source": source,
        "adapted_text": clean_body,
        "text_length": len(clean_body)
    }

def fetch_gemini_grounding_news(config: Any, days_back: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск новостей в реальном времени через Google Gemini с функцией
    Grounding with Google Search (tools=[{"google_search": {}}]).
    Модель сама осуществляет живой веб-поиск в Google за последние 7-10 дней.
    """
    key = getattr(config, "gemini_api_key", "").strip().strip("'\"").strip()
    if not key:
        return []

    model = getattr(config, "gemini_text_model", "gemini-3.8-flash").strip()
    proxy = getattr(config, "proxy_url", "").strip()

    directive = getattr(config, "search_prompt", "").strip() if config else ""
    if not directive:
        directive = EDITORIAL_DIRECTIVE

    prompt = f"""Используй функцию живого веб-поиска Google Search в реальном времени.
Найди самые свежие, важные и проверенные новости за последние {days_back} дней (от сегодняшней даты) по следующим строгим правилам:

{directive}

Выведи от 6 до 12 актуальных новостей за последние {days_back} дней, разделяя их строкой '---'.
Перед каждым блоком укажи строку 'ИСТОЧНИК: [Название СМИ или портала]'.
"""

    try:
        from google import genai
        from google.genai import types

        http_opts = types.HttpOptions(timeout=45000)
        if proxy:
            http_opts = types.HttpOptions(timeout=45000, client_args={"proxy": proxy})
        client = genai.Client(api_key=key, http_options=http_opts)

        gen_cfg = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2
        )

        models_to_try = [model, "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-3.5-flash", "gemini-pro-latest"]
        ordered = []
        seen = set()
        for m in models_to_try:
            if m and m not in seen:
                seen.add(m)
                ordered.append(m)

        resp = None
        for m in ordered:
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=gen_cfg
                )
                if resp and resp.text:
                    break
            except Exception as em:
                err_str = str(em).lower()
                if any(err_term in err_str for err_term in ["404", "not_found", "decommissioned"]):
                    continue
                raise em

        if not resp or not resp.text:
            return []

        text = resp.text
        blocks = re.findall(r'---\s*\n(ЗАГОЛОВОК:[\s\S]+?ТЕКСТ ДЛЯ ДИКТОРА:[\s\S]+?)(?=(?:\n---|\Z))', text, re.IGNORECASE)
        if not blocks:
            raw_parts = [p.strip() for p in text.split("---") if "ТЕКСТ ДЛЯ ДИКТОРА:" in p]
            blocks = raw_parts

        items = []
        for blk in blocks:
            parsed = parse_editorial_block(blk)
            if not parsed:
                continue

            src_m = re.search(r'ИСТОЧНИК:\s*([^\n\r]+)', blk, re.IGNORECASE)
            source = src_m.group(1).strip() if src_m else "Google Grounding (Поиск)"

            title = parsed["title"]
            news_id = hashlib.md5(f"gemini_{title}".encode("utf-8")).hexdigest()[:12]
            items.append({
                "id": news_id,
                "title": title,
                "rubric": parsed["rubric"],
                "timing": parsed["timing"],
                "raw_text": parsed["adapted_text"],
                "adapted_text": parsed["adapted_text"],
                "text_length": parsed["text_length"],
                "link": "",
                "source": source,
                "published_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
                "source_images": [],
                "visual_queries": [title[:50]]
            })

        return items
    except Exception as e:
        print(f"Gemini Google Search Grounding fetch error: {e}")
        return []

def harvest_dynamic_news(
    queries: Optional[List[str]] = None,
    days_back: int = 10,
    vk_token: str = "",
    config: Optional[Any] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None
) -> List[Dict[str, Any]]:
    """
    Интеллектуальный динамический сбор новостей по всем доступным источникам
    (Google News RSS, VK, RSS СМИ) с фильтрацией строго за последние 7-10 дней.
    """
    search_queries = queries or DEFAULT_DYNAMIC_QUERIES
    all_raw: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_titles = set()

    def _add_raw_item(it: Dict[str, Any]):
        nid = it.get("id")
        norm_title = re.sub(r'[^а-яa-z0-9]', '', it.get("title", "").lower())[:45]
        if nid not in seen_ids and (not norm_title or norm_title not in seen_titles):
            seen_ids.add(nid)
            if norm_title:
                seen_titles.add(norm_title)
            all_raw.append(it)

    # 1. Поиск через Google Gemini Grounding (веб-поиск в реальном времени, если доступен)
    if config and getattr(config, "gemini_use_search_grounding", True) and getattr(config, "gemini_api_key", "").strip():
        if on_progress:
            on_progress(1, len(search_queries) + 2, "Поиск в реальном времени Google Search (Gemini)...")
        try:
            gemini_items = fetch_gemini_grounding_news(config, days_back=days_back)
            for it in gemini_items:
                _add_raw_item(it)
        except Exception as eg:
            print(f"Grounding step error: {eg}")

    # 2. Сбор из подключенных RSS-источников СМИ (76.ru, Вести Ярославль и др.)
    if config and getattr(config, "sources", None):
        cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
        for s in config.sources:
            if not getattr(s, "enabled", True):
                continue
            stype = getattr(s, "type", "rss")
            if stype == "rss":
                try:
                    from app.news_fetcher import fetch_rss_source
                    s_dict = s.dict() if hasattr(s, "dict") else dict(s)
                    rss_items = fetch_rss_source(s_dict, cutoff_date=cutoff_date)
                    for it in rss_items:
                        if is_news_post(it):
                            _add_raw_item(it)
                except Exception as er:
                    print(f"RSS source error {getattr(s, 'name', '')}: {er}")

    # 3. Поиск по Google News RSS и ВКонтакте
    total_steps = len(search_queries)
    for idx, q in enumerate(search_queries):
        if on_progress:
            on_progress(idx + 1, total_steps, f"Поиск: «{q}»...")

        # Google News RSS (строго за 7-10 дней)
        g_news = fetch_google_news_dynamic(q, days_back=days_back)
        for it in g_news:
            _add_raw_item(it)

        # VK Live Search
        if vk_token and any(k in q.lower() for k in ["афиша", "культура", "спорт", "происшествия", "здравоохранение", "тутаев"]):
            vk_news = fetch_vk_newsfeed_dynamic(q, vk_token=vk_token, days_back=days_back)
            for it in vk_news:
                _add_raw_item(it)

    return all_raw

def dynamic_editorial_selection_and_rewrite(
    items: List[Dict[str, Any]],
    config: Any,
    max_items: int = 35,
    days_back: int = 10,
    on_progress: Optional[Callable[[int, int, str], None]] = None
) -> List[Dict[str, Any]]:
    """
    Принудительный вывод ВСЕХ найденных новостей за последние 7-10 дней.
    Ни одна новость не отбрасывается.
    Если нейросеть возвращает рерайт - используется рерайт.
    Если нейросеть выдает 429 или отклоняет - новость автоматически
    адаптируется под эфирный формат без потери информации!
    """
    if not items:
        return []

    # 1. Проверка свежести: отбираем новости строго за период последних 7-10 дней
    recent_items = [it for it in items if is_within_days(it.get("published_at"), days=days_back)]
    candidates = recent_items if recent_items else items

    # Приоритет новостям с прямым упоминанием Тутаева и района
    local_kw = ["тутаев", "тутаевск", "романов", "константиновск", "фоминск", "чебаково", "пшеничищ"]
    def _priority(it):
        text = f"{it.get('title', '')} {it.get('raw_text', '')}".lower()
        has_direct = any(k in text for k in local_kw)
        return (0 if has_direct else 1)

    candidates.sort(key=_priority)

    # 2. Дедупликация идентичных сюжетов
    unique = deduplicate_news(candidates, similarity_threshold=0.38)
    if not unique:
        unique = candidates

    processed_items: List[Dict[str, Any]] = []
    total_to_process = min(len(unique), max_items)

    for i, it in enumerate(unique[:total_to_process]):
        title = it.get("title", "").replace('\u200b', '').strip()
        raw_text = it.get("raw_text", "").replace('\u200b', '').strip()
        source = it.get("source", "СМИ")

        if on_progress:
            on_progress(i + 1, total_to_process, f"Обработка [{i+1}/{total_to_process}]: «{title[:40]}...»")

        # Если новость уже адаптирована (например, из живого поиска)
        if it.get("rubric") and it.get("adapted_text") and len(it.get("adapted_text", "")) >= 250:
            processed_items.append(it)
            continue

        adapted_ok = False
        directive = getattr(config, "search_prompt", "").strip() if config else ""
        if not directive:
            directive = EDITORIAL_DIRECTIVE

        rewrite_prompt = f"""Ты — шеф-редактор и ведущий регионального новостного телеканала.
Твоя задача — сделать полный фактологический рерайт новости для диктора по следующим строгим правилам:

{directive}

Исходный материал для адаптации:
Заголовок: {title}
Источник: {source}
Текст: {raw_text}
"""
        try:
            # Небольшая пауза для соблюдения RPM Gemini Free Tier
            if i > 0 and getattr(config, "ai_provider", "gemini") == "gemini":
                time.sleep(0.5)

            llm_resp = query_llm(rewrite_prompt, config, enable_grounding=False)
            parsed = parse_editorial_block(llm_resp)
            if parsed and parsed.get("adapted_text"):
                it["title"] = parsed["title"] or title[:60]
                it["rubric"] = parsed["rubric"]
                it["timing"] = parsed["timing"]
                it["adapted_text"] = parsed["adapted_text"]
                it["text_length"] = parsed["text_length"]
                it["visual_queries"] = [it["title"][:50]]
                adapted_ok = True
        except Exception as e:
            print(f"LLM rewrite notice for '{title[:35]}': {e}")

        # ПРИНУДИТЕЛЬНЫЙ ВЫВОД: Если нейросеть выдала 429, ошибку сети или отклонила —
        # новость ОБЯЗАТЕЛЬНО сохраняется и форматируется под диктора!
        if not adapted_ok:
            fallback_text = build_anchor_fallback_text(title, raw_text, source)
            it["title"] = title[:70] if title else "Новость региона"
            it["rubric"] = detect_rubric(title, fallback_text)
            it["timing"] = "~45–60 сек"
            it["adapted_text"] = fallback_text
            it["text_length"] = len(fallback_text)
            it["visual_queries"] = [title[:50]]

        it["title"] = strip_emojis_and_smileys(it.get("title", ""))
        it["adapted_text"] = strip_emojis_and_smileys(it.get("adapted_text", ""))
        it["text_length"] = len(it["adapted_text"])
        processed_items.append(it)

    return processed_items
