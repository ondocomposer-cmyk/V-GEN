import os
import re
import io
import time
import urllib.parse
from typing import List, Dict, Any, Optional
import concurrent.futures
import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageFont

CACHE_IMG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "images")
os.makedirs(CACHE_IMG_DIR, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
FAST_TIMEOUT = httpx.Timeout(connect=2.5, read=3.0, write=2.5, pool=2.5)

def smart_uncrop_to_full_hd(img: Image.Image, target_w: int = 1920, target_h: int = 1080) -> Image.Image:
    """
    ТВ-стандарт UNCROP: приведение любого изображения к 1920x1080 Full HD.
    Если пропорции не 16:9 — размытый затемненный задний фон + четкий центрированный оригинал.
    """
    img = img.convert("RGB")
    w, h = img.size
    aspect = w / h
    target_aspect = target_w / target_h

    # Если уже близко к 16:9 (погрешность до 6%)
    if abs(aspect - target_aspect) < 0.06:
        scale = max(target_w / w, target_h / h)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    # Метод UNCROP для не-16:9
    # 1. Задний план: растягиваем на весь экран 1920x1080
    bg_scale = max(target_w / w, target_h / h)
    bg_w, bg_h = int(w * bg_scale), int(h * bg_scale)
    bg = img.resize((bg_w, bg_h), Image.Resampling.BILINEAR)
    bg_left = (bg_w - target_w) // 2
    bg_top = (bg_h - target_h) // 2
    bg = bg.crop((bg_left, bg_top, bg_left + target_w, bg_top + target_h))

    # Быстрое размытие по Гауссу заднего плана
    bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
    # Затемнение фона на 30%
    enhancer = ImageEnhance.Brightness(bg)
    bg = enhancer.enhance(0.70)

    # 2. Передний план: масштабируем с сохранением пропорций
    safe_h = target_h - 40
    fg_scale = min(target_w / w, safe_h / h)
    fg_w, fg_h = int(w * fg_scale), int(h * fg_scale)
    fg = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)

    fg_x = (target_w - fg_w) // 2
    fg_y = (target_h - fg_h) // 2

    bg.paste(fg, (fg_x, fg_y))
    return bg

def extract_article_page_images(article_url: str) -> List[str]:
    """Быстрый парсинг изображений со страницы новости с отсеиванием иконок и превью"""
    if not article_url or not article_url.startswith("http") or "vk.com" in article_url:
        return []

    images = []
    headers = {"User-Agent": USER_AGENT}
    bad_patterns = ["logo", "icon", "banner", "pixel", "avatar", "thumb", "small", "mini", "100x", "150x", "50x", "preview"]
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=FAST_TIMEOUT) as client:
            resp = client.get(article_url)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                og_img = soup.find("meta", property="og:image")
                if og_img and og_img.get("content"):
                    u = og_img["content"]
                    if not any(b in u.lower() for b in bad_patterns):
                        images.append(u)
                tw_img = soup.find("meta", attrs={"name": "twitter:image"})
                if tw_img and tw_img.get("content"):
                    u = tw_img["content"]
                    if not any(b in u.lower() for b in bad_patterns):
                        images.append(u)
                for img_tag in soup.find_all("img"):
                    src = img_tag.get("src") or img_tag.get("data-src")
                    if src and src.startswith("http") and not any(bad in src.lower() for bad in bad_patterns):
                        images.append(src)
                        if len(images) >= 6:
                            break
    except Exception:
        pass

    return images

def search_yandex_images(query: str, max_results: int = 10) -> List[str]:
    """Быстрый поиск картинок высокого разрешения в Яндекс Картинках"""
    headers = {"User-Agent": USER_AGENT}
    images = []
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=FAST_TIMEOUT) as client:
            resp = client.get("https://yandex.ru/images/search", params={"text": query, "isize": "large"})
            if resp.status_code == 200:
                raw_matches = re.findall(r'img_url=([^&]+)', resp.text)
                for raw in raw_matches:
                    u = urllib.parse.unquote(raw)
                    if u.startswith("http") and not any(ext in u.lower() for ext in [".svg", ".gif", "thumb", "icon"]):
                        images.append(u)
                        if len(images) >= max_results:
                            break
    except Exception:
        pass
    return images

def search_bing_raw(query: str, max_results: int = 10) -> List[str]:
    """Быстрый поиск картинок высокого разрешения в Bing"""
    headers = {"User-Agent": USER_AGENT}
    images = []
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=FAST_TIMEOUT) as client:
            resp = client.get("https://www.bing.com/images/search", params={"q": query, "qft": "+filterui:imagesize-large", "form": "HDRSC2"})
            if resp.status_code == 200:
                matches = re.findall(r'murl&quot;:&quot;(http[^&]+)&quot;', resp.text)
                for url in matches:
                    decoded = urllib.parse.unquote(url)
                    if decoded.startswith("http") and not any(ext in decoded.lower() for ext in [".svg", ".gif", "thumb", "icon"]):
                        images.append(decoded)
                        if len(images) >= max_results:
                            break
    except Exception:
        pass
    return images

def search_bing_images(query: str, max_results: int = 8) -> List[str]:
    """Основной метод поиска картинок (Яндекс -> Bing fallback)"""
    res = search_yandex_images(query, max_results=max_results)
    if len(res) >= 4:
        return res
    bing_res = search_bing_raw(query, max_results=max_results)
    for b in bing_res:
        if b not in res:
            res.append(b)
    return res

def download_and_uncrop_image(img_url: str, save_path: str) -> bool:
    """
    Скачивание картинки с коротким таймаутом и строгим фильтром качества:
    - Минимальный размер файла > 15 КБ (отсеивает иконки и пиксели)
    - Минимальное разрешение: 640x360 или от 230 000 пикселей
    - Отсев узких полосок-баннеров (соотношение сторон от 0.45 до 2.6)
    - Конвертация в Full HD UNCROP (1920x1080)
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
    }
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=FAST_TIMEOUT) as client:
            resp = client.get(img_url)
            if resp.status_code == 200 and len(resp.content) > 15000:
                image_data = io.BytesIO(resp.content)
                img = Image.open(image_data)
                w, h = img.size
                if w < 640 or h < 360 or (w * h < 230000):
                    return False
                aspect = w / h
                if aspect < 0.45 or aspect > 2.6:
                    return False
                
                uncropped = smart_uncrop_to_full_hd(img, 1920, 1080)
                uncropped.save(save_path, "JPEG", quality=92)
                return True
    except Exception:
        pass
    return False

def collect_images_for_news(news_item: Dict[str, Any], target_count: int = 7) -> List[str]:
    """
    Сбор 4-7 фотографий для новости:
    1. Из источника (VK / RSS)
    2. Из поисковика по прямым и ассоциативным запросам (только если не хватает)
    3. Автоматический быстрый UNCROP до 1920x1080 Full HD
    """
    news_id = news_item.get("id", str(int(time.time())))
    candidate_urls: List[str] = []

    # 1. Фото прямо из источника (VK / RSS)
    candidate_urls.extend(news_item.get("source_images", []))

    # 2. Фото со страницы статьи (только если фото < 4)
    article_link = news_item.get("link", "")
    if len(candidate_urls) < 4 and article_link and "vk.com" not in article_link:
        page_imgs = extract_article_page_images(article_link)
        candidate_urls.extend(page_imgs)

    # 3. Фото из поисковика (только если всё ещё не хватает < 4)
    if len(candidate_urls) < 4:
        title = news_item.get("title", "")
        # Берем чистый поисковый запрос без лишних символов
        clean_q = re.sub(r'[^\w\s-]', '', title)[:45].strip()
        if clean_q:
            found = search_bing_images(clean_q, max_results=6)
            candidate_urls.extend(found)

    # Дедупликация URL
    seen_urls = set()
    deduped_urls = []
    for u in candidate_urls:
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped_urls.append(u)

    # Параллельное скачивание и обработка в Full HD (макс 6 задач)
    processed_files = []

    def _worker(item):
        idx, u = item
        save_filename = f"{news_id}_slide_{idx}.jpg"
        save_path = os.path.join(CACHE_IMG_DIR, save_filename)
        if download_and_uncrop_image(u, save_path):
            return save_filename
        return None

    tasks = [(i + 1, u) for i, u in enumerate(deduped_urls[:8])]
    if tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for res in pool.map(_worker, tasks):
                if res:
                    processed_files.append(res)
                    if len(processed_files) >= target_count:
                        break

    # Если картинок собрано меньше 3 (например, нет интернета / сайт заблокирован), создадим брендовый слайд ТВТ
    if len(processed_files) < 3:
        title = news_item.get("title", "Новости телеканала ТВТ")
        rubric = news_item.get("rubric", "НОВОСТИ")
        for i in range(len(processed_files), 4):
            slide_name = f"{news_id}_slide_{i+1}.jpg"
            save_path = os.path.join(CACHE_IMG_DIR, slide_name)
            card = Image.new("RGB", (1920, 1080), color=(15, 23, 42))
            d = ImageDraw.Draw(card)
            # Декоративная ТВ-рамка
            d.rectangle([80, 80, 1840, 1000], outline=(225, 29, 72), width=5)
            d.rectangle([90, 90, 1830, 990], outline=(56, 189, 248), width=2)
            
            # Плашка ТВТ
            d.rounded_rectangle([140, 140, 420, 230], radius=16, fill=(225, 29, 72))
            try:
                font_badge = ImageFont.truetype('arialbd.ttf', 52)
                font_title = ImageFont.truetype('arialbd.ttf', 44)
                font_rubric = ImageFont.truetype('arialbd.ttf', 36)
            except Exception:
                font_badge = ImageFont.load_default()
                font_title = ImageFont.load_default()
                font_rubric = ImageFont.load_default()

            d.text((170, 155), "ТВТ ЭФИР", fill=(255, 255, 255), font=font_badge)
            d.text((140, 270), f"РУБРИКА: {rubric.upper()}", fill=(56, 189, 248), font=font_rubric)
            
            # Заголовок новости (до 60 символов)
            d.text((140, 480), title[:65], fill=(255, 255, 255), font=font_title)
            d.text((140, 560), title[65:130], fill=(203, 213, 225), font=font_title)
            
            card.save(save_path, "JPEG", quality=90)
            processed_files.append(slide_name)

    return processed_files
