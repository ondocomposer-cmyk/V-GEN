import os
import time
import threading
from typing import Dict, Any, List
from app.config import load_config
from app.dynamic_news_engine import harvest_dynamic_news, dynamic_editorial_selection_and_rewrite
from app.image_manager import collect_images_for_news, search_bing_images, download_and_uncrop_image, CACHE_IMG_DIR
from app.tts_engine import generate_speech
from app.video_generator import render_news_video, RENDER_STATUS
from app.storage import load_news_items, save_news_items, get_news_item, upsert_news_item

FETCH_STATUS: Dict[str, Any] = {
    "status": "idle",
    "progress": 0,
    "message": "",
    "total_found": 0,
    "processed_count": 0
}

def get_fetch_status() -> Dict[str, Any]:
    return dict(FETCH_STATUS)

def run_fetch_and_process_task(max_items_to_process: int = 35, max_items: int = None):
    """
    Основной конвейер интеллектуального поиска и обработки новостей:
    1. Динамический сбор новостей за последние 7-10 дней (без жестко прописанных источников).
       Поиск по Google News, ВКонтакте и СМИ: Тутаев, Тутаевский район, ЖКХ, происшествия,
       афиши мероприятий, культура, спорт, здравоохранение, важные региональные события.
    2. Шеф-редакторский отбор и фактологический рерайт строго на 1000 знаков через нейросеть
       (Gemini или Groq) БЕЗ обращений к зрителям, с устранением канцеляризмов.
    3. Принудительный вывод ВСЕХ найденных новостей без отбрасывания.
    4. Подбор Full HD UNCROP фотографий (1080p).
    * Озвучка при первичном сборе НЕ генерируется (создается по разрешению пользователя).
    """
    if max_items is not None:
        max_items_to_process = max_items
    cfg = load_config()

    FETCH_STATUS.clear()
    FETCH_STATUS.update({
        "status": "running",
        "progress": 2,
        "message": "Запуск динамического поиска новостей за последние 7 дней...",
        "total_found": 0,
        "processed_count": 0
    })

    try:
        # 1. Динамический сбор новостей за 7 дней
        def _on_search_progress(completed: int, total: int, query_text: str):
            pct = 2 + int((completed / max(total, 1)) * 38)
            FETCH_STATUS["progress"] = pct
            FETCH_STATUS["message"] = f"Динамический поиск [{completed}/{total}]: {query_text}"

        raw_news = harvest_dynamic_news(
            days_back=cfg.news_days,
            vk_token=cfg.vk_service_token,
            config=cfg,
            on_progress=_on_search_progress
        )

        FETCH_STATUS["total_found"] = len(raw_news)
        FETCH_STATUS["progress"] = 42
        FETCH_STATUS["message"] = f"Найдено {len(raw_news)} актуальных материалов. Интеллектуальный отбор и рерайт ({cfg.ai_provider.upper()})..."

        # 2. Интеллектуальный редакторский отбор и рерайт строго на 1000 знаков через выбранную модель ИИ
        def _on_rewrite_progress(completed: int, total: int, title_text: str):
            pct = 45 + int((completed / max(total, 1)) * 45)
            FETCH_STATUS["progress"] = pct
            FETCH_STATUS["message"] = f"[{completed}/{total}] {title_text}"

        processed_news = dynamic_editorial_selection_and_rewrite(
            raw_news,
            config=cfg,
            max_items=max_items_to_process,
            on_progress=_on_rewrite_progress
        )

        FETCH_STATUS["progress"] = 90
        # 3. Подбор фотографий и сохранение (без авто-озвучки)
        existing_items = {it["id"]: it for it in load_news_items()}
        total_to_save = len(processed_news)

        for i, item in enumerate(processed_news):
            news_id = item["id"]
            title = item.get("title", "")

            # Плавный и информативный прогресс с 90% до 99%, чтобы UI никогда не висел на 90%
            pct = 90 + int((i / max(total_to_save, 1)) * 9)
            FETCH_STATUS["progress"] = min(pct, 98)
            FETCH_STATUS["message"] = f"Подготовка Full HD слайдов [{i+1}/{total_to_save}]: «{title[:40]}...»"

            # Быстрое переиспользование: если для сюжета уже есть скачанные слайды на диске, используем их мгновенно
            images = []
            if news_id in existing_items and existing_items[news_id].get("images"):
                cached_imgs = [
                    img for img in existing_items[news_id]["images"]
                    if os.path.exists(os.path.join(CACHE_IMG_DIR, img))
                ]
                if len(cached_imgs) >= 3:
                    images = cached_imgs

            if not images:
                try:
                    images = collect_images_for_news(item, target_count=7)
                except Exception as e_img:
                    print(f"Notice: Error collecting images for {news_id}: {e_img}")
                    images = []

            voice_filename = ""
            voice_duration = 0.0
            voice_engine = "Ожидает генерации"

            if news_id in existing_items and existing_items[news_id].get("voice_filename"):
                voice_filename = existing_items[news_id]["voice_filename"]
                voice_duration = existing_items[news_id].get("voice_duration", 0.0)
                voice_engine = existing_items[news_id].get("voice_engine", "Сгенерирована")

            record = {
                "id": news_id,
                "title": title,
                "rubric": item.get("rubric", "Новости"),
                "timing": item.get("timing", "~45–60 сек"),
                "source": item.get("source", "СМИ"),
                "link": item.get("link", ""),
                "published_at": item.get("published_at", ""),
                "raw_text": item.get("raw_text", item.get("adapted_text", "")),
                "adapted_text": item.get("adapted_text", ""),
                "text_length": item.get("text_length", len(item.get("adapted_text", ""))),
                "visual_queries": item.get("visual_queries", []),
                "images": images,
                "voice_filename": voice_filename,
                "voice_duration": round(voice_duration, 1),
                "voice_engine": voice_engine,
                "music_option": "default",
                "custom_music_path": "",
                "status": "ready",
                "video_filename": "",
                "created_at": time.time()
            }
            upsert_news_item(record)
            FETCH_STATUS["processed_count"] += 1

        FETCH_STATUS["status"] = "completed"
        FETCH_STATUS["progress"] = 100
        FETCH_STATUS["message"] = f"Готово! Подготовлено {FETCH_STATUS['processed_count']} актуальных инфоповодов (модель: {cfg.ai_provider.upper()})."

    except Exception as e:
        print(f"Error in dynamic news pipeline: {e}")
        FETCH_STATUS["status"] = "error"
        FETCH_STATUS["message"] = f"Ошибка: {e}"

def start_background_news_fetch(max_items: int = 35):
    if FETCH_STATUS["status"] == "running":
        return False
    t = threading.Thread(target=run_fetch_and_process_task, args=(max_items,), daemon=True)
    t.start()
    return True

def regenerate_voice_for_item(news_id: str, new_text: str = "") -> Dict[str, Any]:
    """Генерация или перегенерация озвучки по запросу пользователя"""
    item = get_news_item(news_id)
    if not item:
        raise ValueError("Новость не найдена")

    cfg = load_config()
    text = new_text.strip() if new_text.strip() else item.get("adapted_text", "")
    if len(text) > 1000:
        text = text[:1000]

    voice_path, duration, engine_name = generate_speech(
        text=text,
        news_id=news_id,
        config=cfg
    )
    item["adapted_text"] = text
    item["text_length"] = len(text)
    item["voice_filename"] = os.path.basename(voice_path)
    item["voice_duration"] = round(duration, 1)
    item["voice_engine"] = engine_name
    upsert_news_item(item)
    return item

def start_background_video_render(news_id: str):
    item = get_news_item(news_id)
    if not item:
        raise ValueError("Новость не найдена")

    def _render_thread():
        cfg = load_config()
        # Если озвучка еще не была создана пользователем, генерируем ее перед сборкой видео
        if not item.get("voice_filename"):
            try:
                voice_path, duration, engine_name = generate_speech(
                    text=item.get("adapted_text", ""),
                    news_id=news_id,
                    config=cfg
                )
                item["voice_filename"] = os.path.basename(voice_path)
                item["voice_duration"] = round(duration, 1)
                item["voice_engine"] = engine_name
                upsert_news_item(item)
            except Exception as e:
                print(f"Voice generation before render failed: {e}")
                item["status"] = "error"
                upsert_news_item(item)
                return

        voice_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "cache", "audio", item["voice_filename"]
        )
        try:
            final_path = render_news_video(
                news_id=news_id,
                image_filenames=item.get("images", []),
                voice_path=voice_path,
                voice_duration=float(item.get("voice_duration", 30.0)),
                config=cfg,
                music_option=item.get("music_option", "default"),
                custom_music_path=item.get("custom_music_path", ""),
                news_title=item.get("title", "")
            )
            item["status"] = "rendered"
            item["video_filename"] = os.path.basename(final_path)
            upsert_news_item(item)
        except Exception as e:
            item["status"] = "error"
            upsert_news_item(item)

    t = threading.Thread(target=_render_thread, daemon=True)
    t.start()
    return True

def add_image_from_query(news_id: str, query: str) -> List[str]:
    item = get_news_item(news_id)
    if not item:
        raise ValueError("Новость не найдена")

    urls = search_bing_images(query, max_results=6)
    added = []
    current_images = item.get("images", [])

    for u in urls:
        idx = len(current_images) + len(added) + 1
        filename = f"{news_id}_slide_{idx}_{int(time.time())}.jpg"
        save_p = os.path.join(CACHE_IMG_DIR, filename)
        if download_and_uncrop_image(u, save_p):
            added.append(filename)
            if len(added) >= 3:
                break

    if added:
        current_images.extend(added)
        item["images"] = current_images
        upsert_news_item(item)

    return current_images
