import re
import html
from typing import List, Dict, Any, Tuple
from google import genai
from google.genai import types

# Строгий фильтр не-новостей: потери, находки, бытовые вопросы, реклама, сплетни
NON_NEWS_PATTERNS = [
    # Потери и находки животных
    r'\b(потерял[а-я]*|найден[а-я]*|потеряшк[а-я]*|пристрой[а-я]*|передержк[а-я]*|в добрые руки|ищем дом|ищет дом)\b',
    r'\b(кот[а-я]*|кошк[а-я]*|котен[а-я]*|собак[а-я]*|щен[а-я]*|чихуахуа|шпиц|ошейник|поводок|кисул[а-я]|хвостик[а-я]*)\b',
    # Потери и находки вещей и документов
    r'\b(куртк[а-я]|ключ[а-я]*|карт[а-я]|кошелек[а-я]*|телефон[а-я]*|айфон[а-я]*|паспорт[а-я]*|снилс|пропуск[а-я]*|перчатк[а-я]|шапк[а-я]|браслет[а-я]*|сережк[а-я]|пакет с вещами|кто потерял|чья потеря)\b',
    # Бытовые вопросы, просьбы, поиски услуг и анонимки
    r'\b(подскажите|посоветуйте|порекомендуйте|где сделать|кто знает|кто может|ищу мастера|кто менял|вопрос к жителям|помогите найти|где купить|подскажите номер|расписание автобус[а-я]*)\b',
    r'\b(анон|анонимно|админ анон)\b',
    # Барахолка, услуги, коммерция
    r'\b(продам|куплю|сдам|сниму|отдам даром|отдам бесплатно|репетитор|маникюр|ресниц|бров|педикюр|массаж|татуаж|шугаринг)\b',
    r'\b(акци[яи]|скидк[а-я]|распродаж[а-я]|промокод|кэшбэк|выгодн[а-я]* цен[а-я]|бронирован[а-я]*)\b',
    r'\b(ваканси[яи]|ищем сотрудник[а-я]|требу[юе]тся|зарплат[а-я]|в связи с расширением|график работы)\b',
    # Эмоциональное нытье, крики души и бытовые жалобы
    r'\b(крик души|дома дубак|дети соплив[а-я]*|в квартире холодрыг[а-я]|куда смотрит администр[а-я]*|до каких пор|беспредел|позор власт[а-я]*|накипело)\b',
    # Поздравления, открытки, погода, поэзия, календари
    r'\b(доброе утро|спокойной ночи|с добрым утром|с праздником|с днем рождения|поздравляем|подписчик[а-я]*|час поэзии|в живописи|календарь_тутаев|погода на|прогноз погоды)\b',
    r'\b(гороскоп|знак[а-я]* зодиака|астролог[а-я]*|рецепт[а-я]*|кулинарн[а-я]*|выпечк[а-я]*)\b',
    r'\b(тест: узнай|тест: кто ты|БАД[ыа-я]*|диет[а-я]*|похуден[а-я]*|снижающ[а-я]* холестерин|народн[а-я]* средств[а-я]*)\b'
]

STOP_WORDS = {
    'и', 'в', 'во', 'не', 'что', 'он', 'на', 'я', 'с', 'со', 'как', 'а', 'то', 'все', 'она', 'так',
    'его', 'но', 'да', 'ты', 'к', 'у', 'же', 'вы', 'за', 'бы', 'по', 'только', 'ее', 'мне', 'было',
    'вот', 'от', 'меня', 'еще', 'нет', 'о', 'из', 'ему', 'теперь', 'когда', 'даже', 'ну', 'вдруг',
    'ли', 'если', 'уже', 'или', 'ни', 'быть', 'был', 'него', 'до', 'вас', 'нибудь', 'опять', 'уж',
    'вам', 'ведь', 'там', 'потом', 'себя', 'ничего', 'ей', 'может', 'они', 'тут', 'где', 'есть',
    'надо', 'ней', 'для', 'мы', 'тебя', 'их', 'чем', 'была', 'сам', 'чтоб', 'без', 'будто', 'чего',
    'раз', 'тоже', 'себе', 'под', 'будет', 'ж', 'тогда', 'кто', 'этот', 'того', 'потому', 'этого',
    'какой', 'совсем', 'ним', 'здесь', 'этом', 'один', 'почти', 'мой', 'тем', 'чтобы', 'нее', 'сейчас',
    'были', 'куда', 'зачем', 'всех', 'никогда', 'можно', 'при', 'наконец', 'два', 'об', 'другой',
    'хоть', 'после', 'над', 'больше', 'тот', 'через', 'эти', 'нас', 'про', 'всего', 'них', 'какая',
    'много', 'разве', 'три', 'эту', 'моя', 'впрочем', 'хорошо', 'свою', 'этой', 'перед', 'иногда',
    'лучше', 'чуть', 'том', 'нельзя', 'такой', 'им', 'более', 'всегда', 'точной', 'кстати', 'очень'
}

def is_news_post(item: Dict[str, Any]) -> bool:
    """
    Проверка поста на соответствие настоящему новостному инфоповоду СМИ.
    Отсеивает бытовой мусор, находки/потери, сплетни и требует связи с Тутаевым или регионом.
    """
    title = item.get("title", "").strip()
    raw_text = (item.get("raw_text") or item.get("adapted_text") or item.get("text") or "").strip()
    combined = f"{title} {raw_text}".lower()

    if len(title) < 10 or len(raw_text) < 50:
        return False

    # Проверка на бытовые/мусорные шаблоны
    for pat in NON_NEWS_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            return False

    # Географические и отраслевые маркеры Тутаева, района и ключевых событий области
    local_keywords = [
        "тутаев", "тутаевск", "романов", "борисоглебск", "константиновск",
        "фоминск", "чебаково", "чёбаково", "пшеничищ", "левый берег", "правый берег",
        "левобережн", "тмр", "тмз", "црб", "галактик", "малова", "восход", "низов"
    ]
    regional_keywords = [
        "ярославск", "губернатор", "евраев", "правительств", "мчс", "отопительн",
        "теплоснабжен", "водоканал", "водоснабжен", "жкх", "дорог", "трасс",
        "следственн", "прокуратур", "суд", "больниц", "школ", "детск", "благоустройств"
    ]

    src = item.get("source", "").lower()
    is_local_source = any(k in src for k in local_keywords)
    has_local_kw = any(k in combined for k in local_keywords)
    has_regional_kw = any(k in combined for k in regional_keywords)

    if is_local_source:
        return True

    if has_local_kw or has_regional_kw:
        return True

    return False

def get_tokens(text: str) -> set:
    words = re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]{3,}', text.lower())
    return {w for w in words if w not in STOP_WORDS}

def calculate_similarity(tokens1: set, tokens2: set) -> float:
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union)

def deduplicate_news(items: List[Dict[str, Any]], similarity_threshold: float = 0.38) -> List[Dict[str, Any]]:
    unique_items = []
    clusters: List[List[Dict[str, Any]]] = []

    for item in items:
        item_tokens = get_tokens(f"{item.get('title', '')} {item.get('raw_text', '')}")
        matched_cluster = None

        for cluster in clusters:
            rep_tokens = cluster[0]["_tokens"]
            sim = calculate_similarity(item_tokens, rep_tokens)
            if sim >= similarity_threshold:
                matched_cluster = cluster
                break

        item["_tokens"] = item_tokens
        if matched_cluster is not None:
            matched_cluster.append(item)
        else:
            clusters.append([item])

    for cluster in clusters:
        # Приоритет официальным источникам и наиболее полному тексту
        best = max(
            cluster,
            key=lambda x: (
                1 if any(k in x.get("source", "").lower() for k in ["администрация", "департамент", "црб", "губернатор", "правительство", "76.ru", "вести"]) else 0,
                len(x.get("source_images", [])),
                len(x.get("raw_text", ""))
            )
        )
        best_clean = {k: v for k, v in best.items() if not k.startswith("_")}
        unique_items.append(best_clean)

    return unique_items

def select_editorial_news(
    items: List[Dict[str, Any]],
    api_key: str = "",
    model_name: str = "gemini-3.5-flash-lite",
    proxy_url: str = "",
    max_items: int = 15
) -> List[Dict[str, Any]]:
    """
    Интеллектуальный отбор настоящих журналистских инфоповодов (СМИ)
    с помощью Gemini. Отсеивает бытовой мусор и выделяет ключевые темы недели.
    """
    if not items:
        return []

    if len(items) <= max_items and not api_key:
        return items

    if not api_key:
        return items[:max_items]

    try:
        candidates_to_eval = items[:40]
        cand_lines = []
        for idx, it in enumerate(candidates_to_eval):
            s_name = it.get("source", "Источник")
            t_name = it.get("title", "")
            snippet = it.get("raw_text", "")[:130].replace("\n", " ")
            cand_lines.append(f"{idx+1}. [{s_name}] {t_name} | {snippet}")

        prompt = f"""Ты — шеф-редактор службы новостей регионального телеканала.
Перед тобой список постов и сообщений из региональных источников за прошедшие 7 дней.
Твоя задача — отобрать ТОЛЬКО те записи, которые представляют собой НАСТОЯЩИЙ журналистский инфоповод для выпуска новостей СМИ (городское хозяйство, ЖКХ, решения администрации и губернатора, благоустройство, крупные происшествия, образование, медицина, культура, спорт, важные события Ярославской области затрагивающие Тутаев и район).

КАТЕГОРИЧЕСКИ ОТСЕИВАЙ:
- Потери и находки животных (собаки, кошки) и вещей (куртки, ключи, карты, документы)
- Частные бытовые вопросы («подскажите мастера», «где сделать фото»)
- Рекламу, барахолку, акции, поздравления, открытки

Выведи номера отобранных записей через запятую СТРОГО в таком формате:
ОТОБРАНО: 1, 3, 5, 8, ...
""" + "\n".join(cand_lines)

        http_opts = types.HttpOptions(timeout=12000)
        if proxy_url:
            http_opts = types.HttpOptions(timeout=12000, client_args={"proxy": proxy_url})
        client = genai.Client(api_key=api_key, http_options=http_opts)
        resp = client.models.generate_content(model=model_name, contents=prompt)
        text_resp = resp.text or ""

        match = re.search(r'ОТОБРАНО:\s*([0-9,\s]+)', text_resp, re.IGNORECASE)
        if match:
            raw_nums = match.group(1).split(",")
            selected_indices = []
            for n in raw_nums:
                try:
                    num = int(n.strip()) - 1
                    if 0 <= num < len(candidates_to_eval):
                        selected_indices.append(num)
                except ValueError:
                    continue

            if selected_indices:
                selected = [candidates_to_eval[i] for i in selected_indices]
                # Дополняем до max_items остальными качественными новостями, если Gemini выбрал меньше
                seen_ids = {x["id"] for x in selected}
                for it in items:
                    if len(selected) >= max_items:
                        break
                    if it["id"] not in seen_ids:
                        selected.append(it)
                        seen_ids.add(it["id"])
                return selected[:max_items]

    except Exception as e:
        print(f"Gemini editorial selection error: {e}")

    return items[:max_items]

EMOJI_PATTERN = re.compile(
    '['
    '\U00010000-\U0010ffff'
    '\u2600-\u27bf'
    '\u2300-\u23ff'
    '\u2b50-\u2b55'
    '\ufe00-\ufe0f'
    '\u200d'
    '\u20e3'
    '\u3030\u303d\u3297\u3299'
    ']+',
    flags=re.UNICODE
)

def strip_emojis_and_smileys(text: str) -> str:
    """
    Полное удаление эмодзи, смайликов и мусорных символов из текста:
    - Все диапазоны Unicode Emoji и пиктограмм
    - Текстовые смайлики: :-), :), :-(, :(, :D, :-D, ;), XD, :P
    - Скобочные смайлики русскоязычных соцсетей: ))), (((
    - Одиночные смайловые скобки на концах фраз
    - Корректная нормализация пробелов и пунктуации
    """
    if not text:
        return ""
    # 1. Удаление всех юникод-эмодзи и пиктограмм
    t = EMOJI_PATTERN.sub("", text)
    # 2. Удаление типовых смайлов
    t = re.sub(r'[:;=8]-?[)(/\\|pPDd3>]+', '', t)
    t = re.sub(r'\b[xX][dD]+\b', '', t)
    # 3. Удаление скобочных смайликов соцсетей (две и более скобки)
    t = re.sub(r'\){2,}', '', t)
    t = re.sub(r'\({2,}', '', t)
    # 4. Удаление одиночной смайловой скобки в конце слова/фразы
    t = re.sub(r'(?<=[!?.а-яА-ЯёЁ\w])\)(?=\s|$)', '', t)
    # 5. Устранение пробелов перед знаками препинания
    t = re.sub(r'\s+([,.:;!?])', r'\1', t)
    # 6. Нормализация пробельных символов
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r' \n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()

def clean_text_for_anchor(text: str) -> str:
    # Удаление ссылок и HTML
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    # Удаление эмодзи и смайликов
    text = strip_emojis_and_smileys(text)
    # Удаление журналистских клише и штампов
    cliches = [
        r'читайте (также|подробнее|далее)[^.\n]*',
        r'подробности (в материале|по ссылке|на сайте)[^.\n]*',
        r'об этом сообщает[^.\n]*',
        r'как передает корреспондент[^.\n]*',
        r'как стало известно[^.\n]*',
        r'источник (сообщил|уточнил|отметил)[^.\n]*',
        r'фото:\s*[^\n.]*',
        r'видео:\s*[^\n.]*',
    ]
    for c in cliches:
        text = re.sub(c, '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def smart_truncate_anchor_text(text: str, max_chars: int = 1000) -> str:
    """Ограничение текста строго до max_chars знаков по границе предложения"""
    if len(text) <= max_chars:
        return text.strip()
    truncated = text[:max_chars]
    last_punct = max(truncated.rfind('. '), truncated.rfind('! '), truncated.rfind('? '))
    if last_punct > 500:
        return truncated[:last_punct + 1].strip()
    last_space = truncated.rfind(' ')
    return (truncated[:last_space] + '...').strip()

def adapt_with_gemini(
    title: str,
    text: str,
    api_key: str,
    model_name: str = "gemini-3.5-flash-lite",
    proxy_url: str = ""
) -> Tuple[str, List[str]]:
    """
    Рерайт новости:
    - СТРОГО на 900–1000 знаков (до 1000 знаков с пробелами)
    - БЕЗ обращений к зрителям («Здравствуйте», «В эфире» — ЗАПРЕЩЕНО)
    - Сухая фактологическая информация
    - Упрощение чиновничьих канцеляризмов
    """
    try:
        http_opts = types.HttpOptions(timeout=12000)
        if proxy_url:
            http_opts = types.HttpOptions(timeout=12000, client_args={"proxy": proxy_url})
        client = genai.Client(api_key=api_key, http_options=http_opts)

        prompt = f"""Ты — редактор новостной службы регионального СМИ.
Твоя задача — сделать полный рерайт новости для выпуска новостей.

Исходный заголовок: {title}
Исходный текст: {text}

СТРОГИЕ ТРЕБОВАНИЯ:
1. ОБЪЕМ: СТРОГО 900–1000 знаков с пробелами (не более 1000 знаков, ориентируйся на ровно 1000 знаков). Текст должен быть развернутым, информативным и подробным.
2. БЕЗ ОБРАЩЕНИЙ К ЗРИТЕЛЯМ: Категорически ЗАПРЕЩЕНО добавлять любые обращения к зрителям («Здравствуйте», «В эфире новости», «Дорогие телезрители», «Оставайтесь с нами», «С вами телеканал» и т.п.).
3. ТОЛЬКО СУХАЯ ФАКТИЧЕСКАЯ ИНФОРМАЦИЯ: Только факты по теме события — что произошло, где, когда, детали, предыстория, принятые меры, последствия, комментарии ответственных лиц.
4. УПРОЩАТЬ КАНЦЕЛЯРИЗМЫ: Убери любой чиновничий протокольный язык и бюрократические штампы («в целях реализации мероприятий», «имеет место быть», «осуществляется контроль за проведением работ»). Перепиши ясным, живым, грамотным литературным русским языком.
5. СТРУКТУРА:
   - В первом предложении четко и сухо обозначь главное событие.
   - Далее подробно изложи обстоятельства, адреса, цифры и контекст.
   - Заверши актуальным статусом, сроками или официальными выводами.

Требования к поиску картинок:
Подбери 4-5 конкретных поисковых запросов для иллюстрации новости (названия улиц, зданий, объектов, персоналии, техника, сфера ЖКХ/медицины/культуры).

Формат ответа СТРОГО следующий:
[ТЕКСТ]
(здесь сухой текст новости строго 900-1000 знаков)
[/ТЕКСТ]
[ЗАПРОСЫ]
(по одному запросу на строку)
[/ЗАПРОСЫ]
"""
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        content = response.text or ""

        # Парсим [ТЕКСТ] и [ЗАПРОСЫ]
        text_match = re.search(r'\[ТЕКСТ\](.*?)\[/ТЕКСТ\]', content, re.DOTALL)
        queries_match = re.search(r'\[ЗАПРОСЫ\](.*?)\[/ЗАПРОСЫ\]', content, re.DOTALL)

        adapted_text = ""
        if text_match:
            adapted_text = text_match.group(1).strip()
        else:
            adapted_text = content.strip()

        # Удаление любых случайных приветствий или обращений к зрителям
        adapted_text = re.sub(
            r'^(здравствуйте|добрый (день|вечер|утро)|в эфире [^.\n]+|приветствуем вас)[,!.\s]+',
            '',
            adapted_text,
            flags=re.IGNORECASE
        ).strip()

        queries = []
        if queries_match:
            lines = queries_match.group(1).strip().splitlines()
            for line in lines:
                clean_q = re.sub(r'^[\d\.\-\*\s]+', '', line).strip()
                if clean_q and len(clean_q) > 2:
                    queries.append(clean_q)

        # Жесткое ограничение не более 1000 знаков
        adapted_text = smart_truncate_anchor_text(adapted_text, 1000)
        return adapted_text, queries

    except Exception as e:
        print(f"Gemini adaptation error: {e}")
        return "", []

def fallback_adaptation(title: str, raw_text: str) -> Tuple[str, List[str]]:
    cleaned = clean_text_for_anchor(raw_text)
    # Убираем обращения
    cleaned = re.sub(r'^(здравствуйте|добрый день|в эфире)[,!.\s]+', '', cleaned, flags=re.IGNORECASE).strip()

    if not cleaned.startswith(title[:20]):
        combined = f"{title}. {cleaned}"
    else:
        combined = cleaned

    lead_text = smart_truncate_anchor_text(combined, 1000)

    words = [w for w in re.findall(r'[a-zA-Zа-яА-ЯёЁ]{4,}', title) if w.lower() not in STOP_WORDS]
    queries = []
    if len(words) >= 2:
        queries.append(" ".join(words[:4]))
        queries.append(" ".join(words[-3:]))
    queries.append(title[:60])
    return lead_text, queries

def process_news_item(
    item: Dict[str, Any],
    api_key: str = "",
    model_name: str = "gemini-3.5-flash-lite",
    proxy_url: str = ""
) -> Dict[str, Any]:
    title = item.get("title", "")
    raw_text = item.get("raw_text", "")

    adapted_text = ""
    visual_queries = []

    if api_key:
        adapted_text, visual_queries = adapt_with_gemini(
            title, raw_text, api_key, model_name, proxy_url=proxy_url
        )

    if not adapted_text:
        adapted_text, fallback_q = fallback_adaptation(title, raw_text)
        if not visual_queries:
            visual_queries = fallback_q

    item["adapted_text"] = adapted_text
    item["text_length"] = len(adapted_text)
    item["visual_queries"] = visual_queries
    return item
