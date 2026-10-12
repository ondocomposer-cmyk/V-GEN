import os
import re
import urllib.parse
import subprocess
import time
import shutil
from typing import List, Dict, Any, Optional
from PIL import Image
from app.image_manager import smart_uncrop_to_full_hd

CACHE_VIDEOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "videos")
CACHE_IMG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "images")
os.makedirs(CACHE_VIDEOS_DIR, exist_ok=True)

# Глобальный словарь прогресса рендеринга
RENDER_STATUS: Dict[str, Dict[str, Any]] = {}

def sanitize_russian_filename(title: str, max_len: int = 80) -> str:
    """
    Формирует корректное, читаемое имя файла на русском языке
    на основе заголовка новости для экспорта и сохранения видео.
    """
    if not title:
        return "Новость_ТВТ"
    clean = re.sub(r'["\'«»]', '', title)
    clean = clean.replace(':', ' - ').replace('/', ' ').replace('\\', ' ')
    clean = re.sub(r'[^\w\s\-\.\,\(\)]', '', clean, flags=re.UNICODE)
    clean = re.sub(r'\s+', ' ', clean).strip(' .-_')
    if not clean:
        clean = "Новость_ТВТ"
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(' ', 1)[0].strip(' .-_')
    return clean

def render_photo_ken_burns_clip(
    img_path: str,
    output_path: str,
    duration: float,
    index: int,
    total_slides: int,
    fps: int = 30
):
    """
    Рендеринг фотослайда с идеально плавным кинематографичным зумом БЕЗ эффекта дрожания (jitter).
    - Исключительно один эффект на кадр: либо отдаление (Zoom Out), либо приближение (Zoom In).
    - Никакого хаотичного движения камеры: объектив строго центрирован.
    - Последняя фотография в видео ВСЕГДА рендерится с Zoom Out (отъезд).
    - Предшествующие фотографии строго чередуются: Zoom In <-> Zoom Out.
    - Субпиксельная интерполяция float box в Pillow исключает ступенчатые рывки и дрожание пикселей.
    """
    total_frames = max(1, int(round(duration * fps)))

    if total_slides > 1:
        is_zoom_out = ((total_slides - 1 - index) % 2 == 0)
    else:
        is_zoom_out = True

    try:
        with Image.open(img_path) as orig:
            base_img = smart_uncrop_to_full_hd(orig, 1920, 1080)
    except Exception as e:
        base_img = Image.new("RGB", (1920, 1080), color=(15, 23, 42))

    cmd = [
        'ffmpeg', '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', '1920x1080',
        '-pix_fmt', 'rgb24',
        '-r', str(fps),
        '-i', '-',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        output_path
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    max_zoom = 1.15
    for f in range(total_frames):
        progress = f / max(1, (total_frames - 1))
        if is_zoom_out:
            scale = max_zoom - (max_zoom - 1.0) * progress  # Плавный отъезд от центра: 1.15 -> 1.0
        else:
            scale = 1.0 + (max_zoom - 1.0) * progress        # Плавный наезд в центр: 1.0 -> 1.15

        cw = 1920.0 / scale
        ch = 1080.0 / scale
        left = (1920.0 - cw) / 2.0
        top = (1080.0 - ch) / 2.0
        right = left + cw
        bottom = top + ch

        # Субпиксельный центрированный ресайз
        frame = base_img.resize((1920, 1080), box=(left, top, right, bottom), resample=Image.Resampling.BILINEAR)
        try:
            proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            break

    if proc.stdin:
        proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Ошибка рендеринга слайда {index} в FFmpeg")

def render_news_video(
    news_id: str,
    image_filenames: List[str],
    voice_path: str,
    voice_duration: float,
    config: Any,
    music_option: str = "default",
    custom_music_path: str = "",
    news_title: str = ""
) -> str:
    """
    Полный пайплайн генерации новостного видеоролика для телеканала ТВТ:
    1. Отбивка канала (F:\новостник\отбивка.mp4)
    2. Слайды картинок с абсолютно стабильным зумом БЕЗ дрожания
       ИЛИ видеокусочки города (b-roll) со строгим ограничением по таймингу
    3. Спустя 1 секунду показа первого слайда стартует голос диктора
    4. После голоса последний слайд еще 3 секунды
    5. Затухание (fade out) в конце видео
    6. Фоновая музыка с -5.4 dB, затихает одновременно с затуханием последнего кадра
    7. Логотип ТВТ поверх видео
    """
    RENDER_STATUS[news_id] = {
        "status": "processing",
        "progress": 5,
        "message": "Подготовка материалов...",
        "video_path": ""
    }

    temp_dir = os.path.join(CACHE_VIDEOS_DIR, f"temp_{news_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        lead_in = float(getattr(config, 'voice_lead_in', 1.0))
        lead_out = float(getattr(config, 'voice_lead_out', 3.0))
        total_slides_duration = lead_in + voice_duration + lead_out

        # Проверяем медиафайлы: фото или кусочки видео (b-roll города)
        city_videos_dir = getattr(config, 'city_videos_dir', r"F:\КОНСЕРВЫ")
        valid_media = []
        for media_name in image_filenames:
            if not media_name:
                continue
            p1 = os.path.join(CACHE_IMG_DIR, media_name)
            p2 = os.path.join(city_videos_dir, media_name)
            if os.path.exists(p1):
                valid_media.append(p1)
            elif os.path.exists(p2):
                valid_media.append(p2)
            elif os.path.exists(media_name):
                valid_media.append(media_name)

        if not valid_media:
            raise ValueError("Нет доступных медиафайлов (фото или видео) для новости")

        num_slides = len(valid_media)
        # Длина каждого слайда или ролика строго рассчитывается исходя из общего хронометража озвучки!
        slide_duration = total_slides_duration / num_slides

        RENDER_STATUS[news_id]["message"] = f"Монтаж {num_slides} кадров (фото и видео)..."
        RENDER_STATUS[news_id]["progress"] = 15

        # 1. Рендерим слайды фото и кусочки видео
        slide_clips = []
        video_extensions = [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]

        for i, media_path in enumerate(valid_media):
            clip_path = os.path.join(temp_dir, f"slide_{i}.mp4")
            is_video = any(media_path.lower().endswith(ext) for ext in video_extensions)

            if is_video:
                # Видеоролик города (b-roll): строго ограничиваем длину до slide_duration,
                # масштабируем в 1080p, нормализуем SAR 1:1, 30 fps и отключаем исходный звук (-an)
                cmd = [
                    'ffmpeg', '-y',
                    '-stream_loop', '-1',
                    '-ss', '0',
                    '-t', f"{slide_duration:.3f}",
                    '-i', media_path,
                    '-vf', 'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=30',
                    '-an',
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',
                    '-crf', '18',
                    '-pix_fmt', 'yuv420p',
                    '-r', '30',
                    clip_path
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                slide_clips.append(clip_path)
            else:
                # Фотослайд: ультра-плавный кинематографичный зум без дрожания
                render_photo_ken_burns_clip(
                    img_path=media_path,
                    output_path=clip_path,
                    duration=slide_duration,
                    index=i,
                    total_slides=num_slides,
                    fps=30
                )
                slide_clips.append(clip_path)

            pct = 15 + int((i + 1) / num_slides * 40)
            RENDER_STATUS[news_id]["progress"] = pct

        RENDER_STATUS[news_id]["message"] = "Сборка видеоряда слайдов..."
        RENDER_STATUS[news_id]["progress"] = 60

        # Склеиваем видео слайдов
        slides_list_txt = os.path.join(temp_dir, "slides.txt")
        with open(slides_list_txt, "w", encoding="utf-8") as f:
            for c in slide_clips:
                # В Windows пути для concat demuxer экранируем
                clean_path = c.replace("\\", "/")
                f.write(f"file '{clean_path}'\n")

        # 2. Подготовка аудиодорожки слайдов (голос + музыка)
        # Определяем музыку
        music_file = ""
        if music_option == "default":
            music_file = getattr(config, 'default_music_path', r"F:\новостник\музыка.wav")
        elif music_option == "custom" and custom_music_path and os.path.exists(custom_music_path):
            music_file = custom_music_path

        has_music = bool(music_file and os.path.exists(music_file) and music_option != "none")
        music_db = getattr(config, 'music_volume_db', -5.4)

        fade_start = max(0.5, total_slides_duration - 1.5)
        fade_len = min(1.5, total_slides_duration - fade_start)

        slides_av_mp4 = os.path.join(temp_dir, "slides_av.mp4")

        RENDER_STATUS[news_id]["message"] = "Микширование звука и затухание (fade out)..."
        RENDER_STATUS[news_id]["progress"] = 70

        delay_ms = int(lead_in * 1000)

        if has_music:
            # Микшируем отложенный голос и фоновую музыку.
            # Музыка звучит на протяжении всего видеоряда и затихает СИНХРОННО с затуханием последнего кадра (fade out).
            filter_complex = (
                f"[0:v]fade=t=out:st={fade_start:.2f}:d={fade_len:.2f}[v_fade];"
                f"[1:a]adelay={delay_ms}|{delay_ms},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[speech_delayed];"
                f"[2:a]volume={music_db}dB,atrim=0:{total_slides_duration:.2f},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bgm_trimmed];"
                f"[speech_delayed][bgm_trimmed]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed_audio];"
                f"[mixed_audio]afade=t=out:st={fade_start:.2f}:d={fade_len:.2f}[a_fade]"
            )
            cmd_mix = [
                'ffmpeg', '-y',
                '-f', 'concat', '-safe', '0', '-i', slides_list_txt,
                '-i', voice_path,
                '-stream_loop', '-1', '-i', music_file,
                '-filter_complex', filter_complex,
                '-map', '[v_fade]', '-map', '[a_fade]',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                '-t', f"{total_slides_duration:.2f}",
                slides_av_mp4
            ]
        else:
            # Только голос без музыки, с синхронным фейдаутом в конце
            filter_complex = (
                f"[0:v]fade=t=out:st={fade_start:.2f}:d={fade_len:.2f}[v_fade];"
                f"[1:a]adelay={delay_ms}|{delay_ms},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,apad=whole_dur={total_slides_duration:.2f},afade=t=out:st={fade_start:.2f}:d={fade_len:.2f}[a_fade]"
            )
            cmd_mix = [
                'ffmpeg', '-y',
                '-f', 'concat', '-safe', '0', '-i', slides_list_txt,
                '-i', voice_path,
                '-filter_complex', filter_complex,
                '-map', '[v_fade]', '-map', '[a_fade]',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                '-t', f"{total_slides_duration:.2f}",
                slides_av_mp4
            ]

        res_mix = subprocess.run(cmd_mix, capture_output=True, text=True)
        if res_mix.returncode != 0:
            raise RuntimeError(f"Ошибка микширования аудио в FFmpeg: {res_mix.stderr[-500:]}")

        RENDER_STATUS[news_id]["message"] = "Монтаж с отбивкой и наложение логотипа..."
        RENDER_STATUS[news_id]["progress"] = 85

        # 3. Финальный монтаж: Отбивка + Слайды + Логотип
        bumper_path = getattr(config, 'bumper_path', r"F:\новостник\отбивка.mp4")
        logo_path = getattr(config, 'logo_path', r"F:\новостник\логотип.png")

        # Автоматическое формирование имени файла на русском языке по заголовку новости
        safe_title = sanitize_russian_filename(news_title)
        if safe_title and safe_title != "Новость_ТВТ":
            final_filename = f"{safe_title}.mp4"
        else:
            final_filename = f"Новость_{news_id}.mp4"

        final_output_path = os.path.join(CACHE_VIDEOS_DIR, final_filename)

        has_bumper = os.path.exists(bumper_path)
        has_logo = os.path.exists(logo_path)

        logo_pos = getattr(config, 'logo_position', 'top_right')
        if logo_pos == 'top_left':
            overlay_coord = "40:40"
        elif logo_pos == 'bottom_right':
            overlay_coord = "W-w-40:H-h-40"
        else:
            overlay_coord = "W-w-40:40"

        if has_bumper and has_logo:
            # Склеиваем отбивку и слайды, предварительно нормализуя SAR (1:1), FPS (30) и звук (48k stereo)
            cmd_final = [
                'ffmpeg', '-y',
                '-i', bumper_path,
                '-i', slides_av_mp4,
                '-i', logo_path,
                '-filter_complex',
                '[0:v]setsar=1,fps=30[v0];'
                '[1:v]setsar=1,fps=30[v1];'
                '[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];'
                '[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];'
                '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v_concat][a_concat];'
                f'[v_concat][2:v]overlay={overlay_coord}[v_out]',
                '-map', '[v_out]', '-map', '[a_concat]',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                final_output_path
            ]
        elif has_bumper and not has_logo:
            cmd_final = [
                'ffmpeg', '-y',
                '-i', bumper_path,
                '-i', slides_av_mp4,
                '-filter_complex',
                '[0:v]setsar=1,fps=30[v0];'
                '[1:v]setsar=1,fps=30[v1];'
                '[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];'
                '[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];'
                '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v_out][a_out]',
                '-map', '[v_out]', '-map', '[a_out]',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                final_output_path
            ]
        elif has_logo:
            cmd_final = [
                'ffmpeg', '-y',
                '-i', slides_av_mp4,
                '-i', logo_path,
                '-filter_complex', f'[0:v]setsar=1[v_src];[v_src][1:v]overlay={overlay_coord}[v_out]',
                '-map', '[v_out]', '-map', '0:a',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                final_output_path
            ]
        else:
            shutil.copy2(slides_av_mp4, final_output_path)
            cmd_final = []

        if cmd_final:
            res_final = subprocess.run(cmd_final, capture_output=True, text=True)
            if res_final.returncode != 0:
                raise RuntimeError(f"Ошибка финального монтажа в FFmpeg: {res_final.stderr[-500:]}")

        RENDER_STATUS[news_id]["status"] = "completed"
        RENDER_STATUS[news_id]["progress"] = 100
        RENDER_STATUS[news_id]["message"] = "Видеоролик успешно создан!"
        RENDER_STATUS[news_id]["video_path"] = final_output_path
        encoded_fn = urllib.parse.quote(final_filename)
        RENDER_STATUS[news_id]["video_url"] = f"/cache/videos/{encoded_fn}"
        RENDER_STATUS[news_id]["video_filename"] = final_filename
        RENDER_STATUS[news_id]["download_url"] = f"/api/news/{news_id}/download"

        return final_output_path

    except Exception as e:
        print(f"Error in render_news_video: {e}")
        RENDER_STATUS[news_id]["status"] = "error"
        RENDER_STATUS[news_id]["message"] = str(e)
        raise e
    finally:
        # Очистка временных файлов
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
