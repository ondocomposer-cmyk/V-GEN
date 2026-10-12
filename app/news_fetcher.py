import datetime
import hashlib
import re
import html
import urllib.parse
from typing import List, Dict, Any, Optional, Callable
import concurrent.futures
import feedparser
import httpx
import warnings
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = html.unescape(raw_html)
    soup = BeautifulSoup(text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()
    clean_text = soup.get_text(separator=" ")
    return re.sub(r'\s+', ' ', clean_text).strip()

def parse_entry_date(entry: Any) -> Optional[datetime.datetime]:
    parsed_time = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None)
    if parsed_time:
        try:
            return datetime.datetime(*parsed_time[:6], tzinfo=datetime.timezone.utc)
        except Exception:
            pass
    return None

def extract_images_from_entry(entry: Any) -> List[str]:
    images = []
    if hasattr(entry, 'enclosures'):
        for enc in entry.enclosures:
            if hasattr(enc, 'type') and 'image' in enc.type.lower() and hasattr(enc, 'href'):
                images.append(enc.href)
    if hasattr(entry, 'media_content'):
        for m in entry.media_content:
            if 'url' in m and ('image' in m.get('type', '').lower() or m.get('medium') == 'image' or not m.get('type')):
                images.append(m['url'])
    if hasattr(entry, 'media_thumbnail'):
        for m in entry.media_thumbnail:
            if 'url' in m:
                images.append(m['url'])
    if hasattr(entry, 'summary'):
        soup = BeautifulSoup(entry.summary, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src")
            if src and src.startswith("http"):
                images.append(src)
    return list(dict.fromkeys(images))

def fetch_rss_source(source: Dict[str, Any], cutoff_date: datetime.datetime) -> List[Dict[str, Any]]:
    items = []
    url = source.get("url", "")
    source_name = source.get("name", "Источник")

    try:
        d = feedparser.parse(url)
        for entry in d.entries:
            pub_date = parse_entry_date(entry)
            if pub_date and pub_date < cutoff_date:
                continue

            title = clean_html(getattr(entry, 'title', ''))
            if not title or len(title) < 10:
                continue

            summary = ""
            if hasattr(entry, 'summary'):
                summary = clean_html(entry.summary)
            elif hasattr(entry, 'description'):
                summary = clean_html(entry.description)
            elif hasattr(entry, 'content') and entry.content:
                summary = clean_html(entry.content[0].get('value', ''))

            link = getattr(entry, 'link', '')
            news_id = hashlib.md5((link or title).encode('utf-8')).hexdigest()[:12]
            images = extract_images_from_entry(entry)

            items.append({
                "id": news_id,
                "title": title,
                "link": link,
                "raw_text": summary or title,
                "published_at": pub_date.isoformat() if pub_date else datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "source": source_name,
                "category": source.get("category", "Новости"),
                "source_images": images
            })
    except Exception as e:
        print(f"Error fetching RSS {source_name}: {e}")
    return items

def fetch_vk_source(source: Dict[str, Any], cutoff_date: datetime.datetime, vk_token: str = "") -> List[Dict[str, Any]]:
    items = []
    url = source.get("url", "").rstrip("/")
    domain = url.split("/")[-1]
    source_name = source.get("name", "ВКонтакте")

    if not vk_token:
        # Без токена VK API требует авторизацию. Информируем пользователя.
        return items

    try:
        api_url = f"https://api.vk.com/method/wall.get?domain={domain}&count=15&v=5.199&access_token={vk_token}"
        with httpx.Client(timeout=6.0, verify=False) as client:
            resp = client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                if "response" in data and "items" in data["response"]:
                    for post in data["response"]["items"]:
                        post_time = datetime.datetime.fromtimestamp(post.get("date", 0), tz=datetime.timezone.utc)
                        if post_time < cutoff_date:
                            continue

                        text = post.get("text", "").strip()
                        if not text or len(text) < 30:
                            continue

                        # Извлекаем картинки максимального разрешения
                        images = []
                        for att in post.get("attachments", []):
                            if att.get("type") == "photo" and "photo" in att:
                                sizes = att["photo"].get("sizes", [])
                                if sizes:
                                    # Сортируем по максимальному размеру
                                    sizes.sort(key=lambda s: s.get("width", 0) * s.get("height", 0), reverse=True)
                                    images.append(sizes[0].get("url"))

                        first_line = text.split("\n")[0][:100]
                        title = first_line if len(first_line) > 15 else f"{source_name}: новость"
                        post_id = post.get("id")
                        owner_id = post.get("owner_id")
                        post_link = f"https://vk.com/wall{owner_id}_{post_id}"
                        news_id = hashlib.md5(f"vk_{owner_id}_{post_id}".encode()).hexdigest()[:12]

                        items.append({
                            "id": news_id,
                            "title": title,
                            "link": post_link,
                            "raw_text": text,
                            "published_at": post_time.isoformat(),
                            "source": source_name,
                            "category": source.get("category", "ВКонтакте"),
                            "source_images": [img for img in images if img]
                        })
    except Exception as e:
        print(f"Error fetching VK {source_name}: {e}")
    return items

def fetch_telegram_source(source: Dict[str, Any], cutoff_date: datetime.datetime, proxy_url: str = "") -> List[Dict[str, Any]]:
    items = []
    url = source.get("url", "").rstrip("/")
    channel = url.split("/")[-1]
    source_name = source.get("name", "Telegram")

    preview_url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": USER_AGENT}

    try:
        client_kwargs = {"headers": headers, "follow_redirects": True, "timeout": 4.0, "verify": False}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        with httpx.Client(**client_kwargs) as client:
            resp = client.get(preview_url)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                messages = soup.find_all("div", class_="tgme_widget_message_wrap")
                for msg in messages:
                    text_div = msg.find("div", class_="tgme_widget_message_text")
                    if not text_div:
                        continue
                    text = clean_html(text_div.get_text(separator="\n"))
                    if len(text) < 30:
                        continue

                    # Время
                    time_tag = msg.find("time")
                    post_time = None
                    if time_tag and time_tag.get("datetime"):
                        try:
                            post_time = datetime.datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
                            if post_time < cutoff_date:
                                continue
                        except Exception:
                            pass

                    # Изображения
                    images = []
                    for photo in msg.find_all("a", class_="tgme_widget_message_photo_wrap"):
                        style = photo.get("style", "")
                        bg_match = re.search(r"background-image:url\('([^']+)'\)", style)
                        if bg_match:
                            images.append(bg_match.group(1))

                    link_tag = msg.find("a", class_="tgme_widget_message_date")
                    post_link = link_tag.get("href", url) if link_tag else url
                    news_id = hashlib.md5(post_link.encode()).hexdigest()[:12]

                    first_line = text.split("\n")[0][:100]
                    title = first_line if len(first_line) > 15 else f"{source_name}: сообщение"

                    items.append({
                        "id": news_id,
                        "title": title,
                        "link": post_link,
                        "raw_text": text,
                        "published_at": post_time.isoformat() if post_time else datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "source": source_name,
                        "category": source.get("category", "Telegram"),
                        "source_images": images
                    })
    except Exception as e:
        print(f"Error fetching Telegram {source_name}: {e}")
    return items

def fetch_site_source(source: Dict[str, Any], cutoff_date: datetime.datetime) -> List[Dict[str, Any]]:
    items = []
    url = source.get("url", "")
    source_name = source.get("name", "Сайт")

    headers = {"User-Agent": USER_AGENT}
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=5.0, verify=False) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Ищем статьи
                articles = soup.find_all("article")
                if not articles:
                    articles = soup.find_all("div", class_=lambda c: c and any(w in c.lower() for w in ["news-item", "post-item", "item", "b-news"]))

                for art in articles[:10]:
                    title_tag = art.find(["h2", "h3", "h4", "a"])
                    if not title_tag:
                        continue
                    title = clean_html(title_tag.get_text())
                    if len(title) < 15:
                        continue

                    link = ""
                    a_tag = art.find("a") if art.name != "a" else art
                    if a_tag and a_tag.get("href"):
                        link = urllib.parse.urljoin(url, a_tag["href"])

                    text_elem = art.find(["p", "div", "span"])
                    text = clean_html(text_elem.get_text()) if text_elem else title

                    images = []
                    img_tag = art.find("img")
                    if img_tag and img_tag.get("src"):
                        images.append(urllib.parse.urljoin(url, img_tag["src"]))

                    news_id = hashlib.md5((link or title).encode()).hexdigest()[:12]
                    items.append({
                        "id": news_id,
                        "title": title,
                        "link": link or url,
                        "raw_text": text,
                        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "source": source_name,
                        "category": source.get("category", "Сайты"),
                        "source_images": images
                    })
    except Exception as e:
        print(f"Error fetching Site {source_name}: {e}")
    return items

def fetch_single_source(source: Dict[str, Any], cutoff_date: datetime.datetime, vk_token: str = "", proxy_url: str = "") -> List[Dict[str, Any]]:
    if not source.get("enabled", True):
        return []

    src_type = source.get("type", "rss")
    src_url = source.get("url", "")

    # Автоопределение типа по URL
    if "vk.com" in src_url:
        src_type = "vk"
    elif "t.me" in src_url:
        src_type = "telegram"
    elif "rss" in src_url or ".xml" in src_url:
        src_type = "rss"

    try:
        if src_type == "rss":
            return fetch_rss_source(source, cutoff_date)
        elif src_type == "vk":
            return fetch_vk_source(source, cutoff_date, vk_token=vk_token)
        elif src_type == "telegram":
            return fetch_telegram_source(source, cutoff_date, proxy_url=proxy_url)
        else:
            return fetch_site_source(source, cutoff_date)
    except Exception as e:
        print(f"Error fetching source {source.get('name')}: {e}")
        return []

def fetch_news_from_sources(
    sources: List[Dict[str, Any]],
    days_back: int = 7,
    vk_token: str = "",
    proxy_url: str = "",
    on_progress: Optional[Callable[[int, int, int], None]] = None
) -> List[Dict[str, Any]]:
    cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
    enabled_sources = [s for s in sources if s.get("enabled", True)]
    total = len(enabled_sources)
    all_news = []
    completed = 0

    if total == 0:
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_src = {
            executor.submit(fetch_single_source, s, cutoff_date, vk_token, proxy_url): s
            for s in enabled_sources
        }
        for future in concurrent.futures.as_completed(future_to_src):
            completed += 1
            src = future_to_src[future]
            try:
                found = future.result()
                if found:
                    all_news.extend(found)
            except Exception as e:
                print(f"Error in source {src.get('name')}: {e}")

            if on_progress:
                on_progress(completed, total, len(all_news))

    return all_news
