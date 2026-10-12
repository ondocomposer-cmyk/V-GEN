import os
import shutil
import time
import asyncio
import subprocess
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from google import genai
from google.genai import types
import feedparser
import httpx

from app.config import load_config, save_config, AppConfig, NewsSource, DEFAULT_SEARCH_PROMPT
from app.tts_engine import GEMINI_VOICES
from app.storage import load_news_items, get_news_item, upsert_news_item, delete_news_item
from app.news_processor import strip_emojis_and_smileys
from app.pipeline import (
    get_fetch_status,
    start_background_news_fetch,
    regenerate_voice_for_item,
    start_background_video_render,
    add_image_from_query
)
import urllib.parse
import hashlib
from app.llm_client import test_ai_provider_connection
from app.video_generator import RENDER_STATUS, CACHE_VIDEOS_DIR, sanitize_russian_filename
from app.image_manager import CACHE_IMG_DIR, download_and_uncrop_image, smart_uncrop_to_full_hd
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

app = FastAPI(title="V-GEN", version="1.0.0")

# Static and Templates
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.mount("/cache", StaticFiles(directory=os.path.join(BASE_DIR, "cache")), name="cache")

# Files from F:\новостник for bumper, music, logo preview
MEDIA_ROOT = BASE_DIR
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# --- HTML Endpoints ---

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    config = load_config()
    news = load_news_items()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"config": config, "news": news}
    )

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    config = load_config()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "config": config,
            "gemini_voices": GEMINI_VOICES,
            "default_search_prompt": DEFAULT_SEARCH_PROMPT
        }
    )

@app.get("/api/config/default-prompt")
async def get_default_prompt():
    return {"prompt": DEFAULT_SEARCH_PROMPT}

@app.get("/archive", response_class=HTMLResponse)
async def archive_page(request: Request):
    news = load_news_items()
    # Find all videos in cache/videos
    videos = []
    if os.path.exists(CACHE_VIDEOS_DIR):
        for f in os.listdir(CACHE_VIDEOS_DIR):
            if f.endswith(".mp4") and not f.startswith("temp"):
                full_path = os.path.join(CACHE_VIDEOS_DIR, f)
                stat = os.stat(full_path)
                # Find matching news item
                matching_title = "Новостной видеоролик"
                for n in news:
                    if n.get("video_filename") == f or n.get("id") in f:
                        matching_title = n.get("title", matching_title)
                        break
                videos.append({
                    "filename": f,
                    "title": matching_title,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified": time.strftime("%d.%m.%Y %H:%M", time.localtime(stat.st_mtime)),
                    "url": f"/cache/videos/{urllib.parse.quote(f)}"
                })
    videos.sort(key=lambda x: x["filename"], reverse=True)
    return templates.TemplateResponse(
        request=request,
        name="archive.html",
        context={"videos": videos}
    )

# --- Direct File Preview Endpoints ---
@app.get("/media/preview/{file_type}")
async def media_preview(file_type: str):
    config = load_config()
    path = ""
    if file_type == "bumper":
        path = config.bumper_path
    elif file_type == "music":
        path = config.default_music_path
    elif file_type == "logo":
        path = config.logo_path
    
    if path and os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="File not found")

# --- Favicon Endpoints ---
@app.get("/favicon.ico")
async def favicon_ico():
    ico_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "img", "favicon.ico")
    if os.path.exists(ico_path):
        return FileResponse(ico_path, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/favicon.svg")
async def favicon_svg():
    svg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "img", "favicon.svg")
    if os.path.exists(svg_path):
        return FileResponse(svg_path, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")

# --- API Endpoints ---

@app.get("/api/news")
async def get_news():
    return load_news_items()

@app.post("/api/news/fetch")
async def trigger_fetch(max_items: int = 35):
    started = start_background_news_fetch(max_items)
    return {"success": started, "status": get_fetch_status()}

@app.get("/api/news/fetch-status")
async def get_fetch_status_route():
    return get_fetch_status()

class TextUpdateRequest(BaseModel):
    text: str

@app.post("/api/news/{news_id}/text")
async def update_news_text(news_id: str, payload: TextUpdateRequest):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")
    
    clean_text = strip_emojis_and_smileys(payload.text.strip())
    if len(clean_text) > 1000:
        clean_text = clean_text[:1000]
    item["adapted_text"] = clean_text
    item["text_length"] = len(clean_text)
    upsert_news_item(item)
    return {"success": True, "item": item}

@app.post("/api/news/{news_id}/regenerate-voice")
async def regen_voice(news_id: str, payload: Optional[TextUpdateRequest] = None):
    new_text = payload.text if payload else ""
    try:
        updated = await asyncio.to_thread(regenerate_voice_for_item, news_id, new_text)
        return {"success": True, "item": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class MusicSettingsRequest(BaseModel):
    music_option: str  # 'default', 'none', 'custom'
    custom_music_path: Optional[str] = ""

@app.post("/api/news/{news_id}/music-settings")
async def update_music_settings(news_id: str, payload: MusicSettingsRequest):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")
    item["music_option"] = payload.music_option
    item["custom_music_path"] = payload.custom_music_path or ""
    upsert_news_item(item)
    return {"success": True, "item": item}

class ReorderImagesRequest(BaseModel):
    images: List[str]

@app.post("/api/news/{news_id}/images/reorder")
async def reorder_images(news_id: str, payload: ReorderImagesRequest):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")
    item["images"] = payload.images
    upsert_news_item(item)
    return {"success": True, "images": payload.images}

@app.delete("/api/news/{news_id}/images/{img_filename}")
async def delete_image(news_id: str, img_filename: str):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")
    current_images = item.get("images", [])
    if img_filename in current_images:
        current_images.remove(img_filename)
        item["images"] = current_images
        upsert_news_item(item)
    return {"success": True, "images": current_images}

class SearchImageRequest(BaseModel):
    query: str

@app.post("/api/news/{news_id}/images/search")
async def search_more_images(news_id: str, payload: SearchImageRequest):
    try:
        updated_images = add_image_from_query(news_id, payload.query)
        return {"success": True, "images": updated_images}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/news/{news_id}/images/upload")
async def upload_custom_media(news_id: str, file: UploadFile = File(...)):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")

    ext = os.path.splitext(file.filename or "")[1].lower()
    video_exts = [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]
    is_video = ext in video_exts

    try:
        if is_video:
            filename = f"{news_id}_video_{int(time.time())}{ext}"
            dest_path = os.path.join(CACHE_IMG_DIR, filename)
            with open(dest_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # Генерация превью-кадра для интерфейса
            thumb_path = f"{dest_path}.jpg"
            cmd_thumb = [
                'ffmpeg', '-y', '-ss', '00:00:01', '-i', dest_path,
                '-vframes', '1',
                '-vf', 'scale=640:360:force_original_aspect_ratio=increase,crop=640:360',
                thumb_path
            ]
            try:
                subprocess.run(cmd_thumb, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            except Exception:
                pass
        else:
            filename = f"{news_id}_custom_{int(time.time())}.jpg"
            dest_path = os.path.join(CACHE_IMG_DIR, filename)
            img = Image.open(file.file)
            uncropped = smart_uncrop_to_full_hd(img, 1920, 1080)
            uncropped.save(dest_path, "JPEG", quality=92)

        images = item.get("images", [])
        images.append(filename)
        item["images"] = images
        upsert_news_item(item)

        return {"success": True, "filename": filename, "images": images, "is_video": is_video}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Media upload error: {e}")

# --- B-Roll (База видео города) Endpoints ---

def get_safe_broll_path(broll_dir: str, rel_path: str) -> str:
    if not rel_path:
        return os.path.normpath(broll_dir)
    clean = rel_path.replace('\\', '/').strip('/')
    parts = clean.split('/')
    if any(p == '..' or p == '' for p in parts):
        raise HTTPException(status_code=400, detail="Invalid path traversal")
    full = os.path.normpath(os.path.join(broll_dir, *parts))
    try:
        if os.path.commonpath([os.path.abspath(broll_dir), os.path.abspath(full)]) != os.path.abspath(broll_dir):
            raise HTTPException(status_code=403, detail="Path outside b-roll directory")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path resolution")
    return full

@app.get("/api/broll/browse")
async def browse_broll(subpath: str = "", search: str = ""):
    config = load_config()
    broll_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
    os.makedirs(broll_dir, exist_ok=True)
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

    search_query = search.strip()
    if search_query:
        q = search_query.lower()
        matched_videos = []
        count = 0
        for root, dirs, files in os.walk(broll_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in video_exts:
                    full_p = os.path.join(root, f)
                    rel_v = os.path.relpath(full_p, broll_dir).replace('\\', '/')
                    rel_f = os.path.relpath(root, broll_dir).replace('\\', '/')
                    if rel_f == '.':
                        rel_f = ""
                    if q in f.lower() or q in rel_f.lower() or q in rel_v.lower():
                        try:
                            size_mb = round(os.path.getsize(full_p) / (1024 * 1024), 2)
                        except Exception:
                            size_mb = 0.0
                        matched_videos.append({
                            "name": f,
                            "rel_path": rel_v,
                            "folder": rel_f,
                            "size_mb": size_mb,
                            "stream_url": f"/media/broll/stream?path={urllib.parse.quote(rel_v)}",
                            "thumb_url": f"/media/broll/thumb?path={urllib.parse.quote(rel_v)}"
                        })
                        count += 1
                        if count >= 150:
                            break
            if count >= 150:
                break
        matched_videos.sort(key=lambda x: x["name"].lower())
        return {
            "broll_dir": broll_dir,
            "is_search": True,
            "search_query": search_query,
            "current_path": "",
            "parent_path": None,
            "breadcrumbs": [{"name": "КОНСЕРВЫ", "path": ""}, {"name": f"Поиск: «{search_query}»", "path": ""}],
            "folders": [],
            "videos": matched_videos,
            "total_folders": 0,
            "total_videos": len(matched_videos)
        }

    target_dir = get_safe_broll_path(broll_dir, subpath)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Папка не найдена")

    clean_subpath = os.path.relpath(target_dir, broll_dir).replace('\\', '/')
    if clean_subpath == '.':
        clean_subpath = ""

    folders = []
    videos = []
    try:
        for entry in os.scandir(target_dir):
            if entry.is_dir() and not entry.name.startswith('.'):
                try:
                    v_count = sum(1 for f in os.scandir(entry.path) if f.is_file() and os.path.splitext(f.name)[1].lower() in video_exts)
                except Exception:
                    v_count = 0
                rel_f = os.path.relpath(entry.path, broll_dir).replace('\\', '/')
                folders.append({
                    "name": entry.name,
                    "rel_path": rel_f,
                    "video_count": v_count
                })
            elif entry.is_file():
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in video_exts:
                    try:
                        size_mb = round(entry.stat().st_size / (1024 * 1024), 2)
                    except Exception:
                        size_mb = 0.0
                    rel_v = os.path.relpath(entry.path, broll_dir).replace('\\', '/')
                    videos.append({
                        "name": entry.name,
                        "rel_path": rel_v,
                        "folder": clean_subpath,
                        "size_mb": size_mb,
                        "stream_url": f"/media/broll/stream?path={urllib.parse.quote(rel_v)}",
                        "thumb_url": f"/media/broll/thumb?path={urllib.parse.quote(rel_v)}"
                    })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения папки: {e}")

    folders.sort(key=lambda x: x["name"].lower())
    videos.sort(key=lambda x: x["name"].lower())

    breadcrumbs = [{"name": "КОНСЕРВЫ", "path": ""}]
    if clean_subpath:
        parts = clean_subpath.split('/')
        acc = []
        for p in parts:
            acc.append(p)
            breadcrumbs.append({
                "name": p,
                "path": "/".join(acc)
            })
        parent_parts = parts[:-1]
        parent_path = "/".join(parent_parts) if parent_parts else ""
    else:
        parent_path = None

    return {
        "broll_dir": broll_dir,
        "is_search": False,
        "search_query": "",
        "current_path": clean_subpath,
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "folders": folders,
        "videos": videos,
        "total_folders": len(folders),
        "total_videos": len(videos)
    }

@app.get("/api/broll/list")
async def get_broll_list(subpath: str = "", search: str = ""):
    return await browse_broll(subpath=subpath, search=search)

@app.get("/media/broll/thumb")
async def get_broll_thumb(path: str):
    config = load_config()
    broll_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
    full_path = get_safe_broll_path(broll_dir, path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Видеофайл не найден")

    thumbs_dir = os.path.join(CACHE_IMG_DIR, "broll_thumbs")
    os.makedirs(thumbs_dir, exist_ok=True)

    try:
        mtime = int(os.path.getmtime(full_path))
    except Exception:
        mtime = 0

    h = hashlib.md5(f"{path}_{mtime}".encode('utf-8', errors='ignore')).hexdigest()
    thumb_file = os.path.join(thumbs_dir, f"{h}.jpg")

    if not os.path.exists(thumb_file) or os.path.getsize(thumb_file) == 0:
        try:
            subprocess.run([
                'ffmpeg', '-y', '-ss', '00:00:01', '-i', full_path,
                '-vframes', '1',
                '-vf', 'scale=480:270:force_original_aspect_ratio=increase,crop=480:270',
                thumb_file
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
            if not os.path.exists(thumb_file) or os.path.getsize(thumb_file) == 0:
                subprocess.run([
                    'ffmpeg', '-y', '-ss', '00:00:00', '-i', full_path,
                    '-vframes', '1',
                    '-vf', 'scale=480:270:force_original_aspect_ratio=increase,crop=480:270',
                    thumb_file
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
        except Exception:
            pass

    if os.path.exists(thumb_file) and os.path.getsize(thumb_file) > 0:
        return FileResponse(thumb_file, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Не удалось сформировать превью")

@app.get("/media/broll/stream")
async def stream_broll_video(path: str):
    config = load_config()
    broll_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
    full_path = get_safe_broll_path(broll_dir, path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Видеофайл не найден")
    return FileResponse(full_path, media_type="video/mp4")

@app.get("/media/broll/{filename:path}")
async def get_broll_file(filename: str):
    config = load_config()
    broll_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
    full_path = get_safe_broll_path(broll_dir, filename)
    if os.path.isfile(full_path):
        return FileResponse(full_path, media_type="video/mp4")
    raise HTTPException(status_code=404, detail="B-roll video not found")

@app.post("/api/broll/upload")
async def upload_broll_file(file: UploadFile = File(...), subpath: str = Form("")):
    config = load_config()
    broll_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
    target_dir = get_safe_broll_path(broll_dir, subpath)
    os.makedirs(target_dir, exist_ok=True)

    orig_name, orig_ext = os.path.splitext(file.filename or "видео_город.mp4")
    dest_filename = f"{sanitize_russian_filename(orig_name, max_len=40)}{orig_ext.lower()}"
    dest_path = os.path.join(target_dir, dest_filename)

    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Pre-generate thumb
        rel_v = os.path.relpath(dest_path, broll_dir).replace('\\', '/')
        thumbs_dir = os.path.join(CACHE_IMG_DIR, "broll_thumbs")
        os.makedirs(thumbs_dir, exist_ok=True)
        mtime = int(time.time())
        h = hashlib.md5(f"{rel_v}_{mtime}".encode('utf-8', errors='ignore')).hexdigest()
        thumb_file = os.path.join(thumbs_dir, f"{h}.jpg")
        try:
            subprocess.run([
                'ffmpeg', '-y', '-ss', '00:00:01', '-i', dest_path,
                '-vframes', '1',
                '-vf', 'scale=480:270:force_original_aspect_ratio=increase,crop=480:270',
                thumb_file
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
        except Exception:
            pass

        return {"success": True, "filename": dest_filename, "rel_path": rel_v}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddBrollRequest(BaseModel):
    filename: Optional[str] = None
    rel_path: Optional[str] = None

@app.post("/api/news/{news_id}/media/add-broll")
async def add_broll_to_news(news_id: str, payload: AddBrollRequest):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")

    target_rel = payload.rel_path or payload.filename
    if not target_rel:
        raise HTTPException(status_code=400, detail="Не указан файл видео")

    config = load_config()
    broll_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
    src_path = get_safe_broll_path(broll_dir, target_rel)
    if not os.path.isfile(src_path):
        raise HTTPException(status_code=404, detail="Видео в базе города не найдено")

    base_name = os.path.basename(src_path)
    name_part, ext = os.path.splitext(base_name)
    dest_filename = f"{news_id}_broll_{int(time.time())}_{sanitize_russian_filename(name_part, max_len=25)}{ext.lower()}"
    dest_path = os.path.join(CACHE_IMG_DIR, dest_filename)

    shutil.copy2(src_path, dest_path)

    thumb_path = f"{dest_path}.jpg"
    try:
        subprocess.run([
            'ffmpeg', '-y', '-ss', '00:00:01', '-i', dest_path,
            '-vframes', '1',
            '-vf', 'scale=640:360:force_original_aspect_ratio=increase,crop=640:360',
            thumb_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
    except Exception:
        pass

    images = item.get("images", [])
    images.append(dest_filename)
    item["images"] = images
    upsert_news_item(item)

    return {"success": True, "filename": dest_filename, "images": images}

@app.post("/api/news/{news_id}/render-video")
async def render_video(news_id: str):
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="News not found")
    if not item.get("images"):
        raise HTTPException(status_code=400, detail="Нет картинок для новости")
    if not item.get("voice_filename"):
        raise HTTPException(status_code=400, detail="Нет аудио озвучки для новости")

    start_background_video_render(news_id)
    return {"success": True, "news_id": news_id}

@app.get("/api/news/{news_id}/render-status")
async def get_render_status(news_id: str):
    if news_id in RENDER_STATUS:
        return RENDER_STATUS[news_id]
    item = get_news_item(news_id)
    if item and item.get("status") == "rendered":
        vfn = item.get("video_filename", "")
        encoded = urllib.parse.quote(vfn)
        return {
            "status": "completed",
            "progress": 100,
            "message": "Видеоролик готов",
            "video_url": f"/cache/videos/{encoded}",
            "video_filename": vfn,
            "download_url": f"/api/news/{news_id}/download"
        }
    return {"status": "idle", "progress": 0, "message": "Ожидание запуска"}

@app.get("/api/news/{news_id}/download")
async def download_news_video(news_id: str):
    """Экспорт видеоролика с автоматическим формированием русского имени файла по заголовку новости"""
    item = get_news_item(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="Новость не найдена")

    title = item.get("title", "")
    safe_title = sanitize_russian_filename(title)
    video_filename = item.get("video_filename")

    # Поиск файла на диске
    file_path = None
    if video_filename:
        p = os.path.join(CACHE_VIDEOS_DIR, video_filename)
        if os.path.exists(p):
            file_path = p

    if not file_path and os.path.exists(CACHE_VIDEOS_DIR):
        for f in os.listdir(CACHE_VIDEOS_DIR):
            if f.endswith(".mp4") and (safe_title in f or news_id in f):
                file_path = os.path.join(CACHE_VIDEOS_DIR, f)
                break

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл видеоролика еще не готов или не найден")

    export_name = f"{safe_title}.mp4"
    quoted = urllib.parse.quote(export_name)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"
    }
    return FileResponse(file_path, media_type="video/mp4", filename=export_name, headers=headers)

@app.get("/api/archive/{video_filename:path}/download")
async def download_archive_video(video_filename: str):
    """Экспорт видеофайла из архива с русским наименованием"""
    video_filename = urllib.parse.unquote(video_filename)
    file_path = os.path.join(CACHE_VIDEOS_DIR, video_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Видеофайл не найден")

    news = load_news_items()
    title = ""
    for n in news:
        if n.get("video_filename") == video_filename or n.get("id") in video_filename:
            title = n.get("title", "")
            break

    if title:
        export_name = f"{sanitize_russian_filename(title)}.mp4"
    else:
        # Если заголовок не найден, убираем id_ префикс если был
        name_no_ext = os.path.splitext(video_filename)[0]
        export_name = f"{sanitize_russian_filename(name_no_ext)}.mp4"

    quoted = urllib.parse.quote(export_name)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"
    }
    return FileResponse(file_path, media_type="video/mp4", filename=export_name, headers=headers)

@app.delete("/api/news/{news_id}")
async def remove_news(news_id: str):
    delete_news_item(news_id)
    return {"success": True}

# --- Settings & Config Endpoints ---

@app.get("/api/config")
async def get_config():
    return load_config()

@app.post("/api/config")
async def update_config(config_data: AppConfig):
    save_config(config_data)
    return {"success": True, "config": config_data}

class TestAiRequest(BaseModel):
    provider: str = "gemini"
    api_key: str
    model: Optional[str] = ""
    proxy_url: Optional[str] = ""

@app.post("/api/config/test-ai")
async def test_ai_provider_route(payload: TestAiRequest):
    provider = payload.provider.strip().lower()
    api_key = payload.api_key.strip()
    if not api_key:
        return {"success": False, "message": "API ключ не указан"}
    
    cfg = load_config()
    proxy = payload.proxy_url.strip() if payload.proxy_url else cfg.proxy_url.strip()
    model = payload.model.strip() if payload.model else ""

    success, msg = test_ai_provider_connection(
        provider=provider,
        api_key=api_key,
        model=model,
        proxy_url=proxy
    )
    return {"success": success, "message": msg}

class TestGeminiRequest(BaseModel):
    api_key: str
    model: str = "gemini-3.5-flash-lite"
    proxy_url: Optional[str] = ""

@app.post("/api/config/test-gemini")
async def test_gemini(payload: TestGeminiRequest):
    key = payload.api_key.strip()
    if not key:
        return {"success": False, "message": "API ключ не указан"}
    cfg = load_config()
    proxy = payload.proxy_url.strip() if payload.proxy_url else cfg.proxy_url.strip()
    success, msg = test_ai_provider_connection(
        provider="gemini",
        api_key=key,
        model=payload.model.strip(),
        proxy_url=proxy
    )
    return {"success": success, "message": msg}

class TestSourceRequest(BaseModel):
    url: str

@app.post("/api/config/test-source")
async def test_source(payload: TestSourceRequest):
    url = payload.url.strip()
    cfg = load_config()

    # 1. VKontakte
    if "vk.com/" in url:
        token = cfg.vk_service_token.strip()
        if not token:
            return {
                "success": False,
                "message": "Для проверки сообществ ВКонтакте сначала укажите Сервисный ключ доступа (VK Service Token) в настройках выше и сохраните."
            }
        domain = url.rstrip("/").split("/")[-1]
        try:
            api_url = f"https://api.vk.com/method/wall.get?domain={domain}&count=5&v=5.199&access_token={token}"
            async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                resp = await client.get(api_url)
                data = resp.json()
                if "error" in data:
                    err_msg = data["error"].get("error_msg", "Неизвестная ошибка VK API")
                    err_code = data["error"].get("error_code", 0)
                    if "domain not domain" in err_msg.lower() or err_code == 100:
                        return {
                            "success": False,
                            "message": f"Сообщество «{domain}» не найдено во ВКонтакте (ошибка 404). Адрес группы не существует или был изменен. Проверьте правильность ссылки."
                        }
                    elif "access denied" in err_msg.lower() or err_code == 15:
                        return {
                            "success": False,
                            "message": f"Доступ к сообществу «{domain}» ограничен (закрытая группа или требуется подтверждение)."
                        }
                    return {"success": False, "message": f"Ошибка VK API: {err_msg}"}
                items = data.get("response", {}).get("items", [])
                photos_count = sum(len([a for a in p.get("attachments", []) if a.get("type") == "photo"]) for p in items)
                return {
                    "success": True,
                    "message": f"Сообщество ВКонтакте «{domain}» успешно подключено! Найдено {len(items)} свежих постов, {photos_count} фото для видеоряда."
                }
        except Exception as e:
            return {"success": False, "message": f"Ошибка соединения с VK API: {e}"}

    # 2. Telegram
    if "t.me/" in url:
        channel = url.rstrip("/").split("/")[-1]
        t_url = f"https://t.me/s/{channel}"
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
                r = await client.get(t_url)
                if r.status_code == 200 and "tgme_widget_message" in r.text:
                    return {"success": True, "message": f"Telegram-канал «@{channel}» доступен! Превью постов получено."}
                elif r.status_code == 200:
                    return {"success": True, "message": f"Telegram-канал «@{channel}» доступен!"}
                else:
                    return {"success": False, "message": f"Telegram ответил со статусом {r.status_code}"}
        except Exception as e:
            return {"success": False, "message": f"Ошибка подключения к Telegram: {e}"}

    # 3. RSS feeds & News portals
    try:
        d = feedparser.parse(url)
        entries_count = len(d.entries)
        if entries_count > 0:
            title = d.feed.get("title", url)
            return {"success": True, "message": f"RSS-лента «{title}» доступна! Найдено {entries_count} записей."}
    except Exception:
        pass

    # 4. Fallback: check web page availability
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return {"success": True, "message": f"Сайт доступен (код 200)! Парсер статей сможет собирать новости."}
            else:
                return {"success": False, "message": f"Сайт вернул HTTP код {r.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"Ошибка подключения к сайту: {e}"}

@app.post("/api/upload/logo")
async def upload_logo_file(file: UploadFile = File(...)):
    dest = r"F:\новостник\логотип.png"
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        cfg = load_config()
        cfg.logo_path = dest
        save_config(cfg)
        return {"success": True, "path": dest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload/music")
async def upload_music_file(file: UploadFile = File(...)):
    music_filename = f"custom_music_{int(time.time())}.wav"
    dest = os.path.join(BASE_DIR, "cache", "music", music_filename)
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"success": True, "path": dest, "filename": music_filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/archive/{filename:path}")
async def delete_archive_video(filename: str):
    filename = urllib.parse.unquote(filename)
    file_path = os.path.join(CACHE_VIDEOS_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    return {"success": True}
