import os
import time
import subprocess
from typing import Tuple, Any, List
from google import genai
from google.genai import types

CACHE_AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "audio")
os.makedirs(CACHE_AUDIO_DIR, exist_ok=True)

# Голос по умолчанию: Callirrhoe (ТВ диктор)
DEFAULT_SPEAKER = "Callirrhoe"

# Полный список официальных голосов Google Gemini TTS
GEMINI_VOICES = [
    {"id": "Callirrhoe", "name": "Callirrhoe (ТВ диктор — энергичный, теплый голос) [По умолчанию]"},
    {"id": "Puck", "name": "Puck (Бодрый, выразительный мужской голос)"},
    {"id": "Charon", "name": "Charon (Глубокий, авторитетный мужской голос)"},
    {"id": "Kore", "name": "Kore (Спокойный, мягкий женский голос)"},
    {"id": "Fenrir", "name": "Fenrir (Уверенный, плотный мужской голос)"},
    {"id": "Aoede", "name": "Aoede (Элегантный, глубокий женский голос)"},
    {"id": "Leda", "name": "Leda (Молодой, звонкий женский голос)"},
    {"id": "Orpheus", "name": "Orpheus (Артистичный мужской голос)"},
    {"id": "Zephyr", "name": "Zephyr (Легкий, динамичный голос)"}
]

# Доступные рабочие модели синтеза речи Gemini с поддержкой Free Tier
GEMINI_FLASH_TTS_MODELS: List[str] = [
    "gemini-3.8-flash-tts",
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-3.8-flash-lite-tts"
]

def get_audio_duration(file_path: str) -> float:
    """Получение точной длительности аудиофайла через ffprobe"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception as e:
        print(f"Error reading duration of {file_path}: {e}")
        return 30.0

def generate_gemini_tts(
    text: str,
    output_path: str,
    api_key: str,
    model_name: str = "gemini-3.8-flash-tts",
    speaker: str = "Callirrhoe",
    proxy_url: str = ""
) -> float:
    """
    Синтез речи через Google Gemini TTS с выбранным голосом (по умолчанию Callirrhoe).
    Озвучивается ИСКЛЮЧИТЕЛЬНО переданный текст новости без каких-либо
    промтов, инструкций, обращений или посторонних приказов модели.
    """
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Текст новости для озвучки пуст.")

    # Ограничение по ТЗ: строго до 1000 знаков
    if len(clean_text) > 1000:
        clean_text = clean_text[:1000].rsplit(' ', 1)[0]

    chosen_speaker = speaker.strip() if speaker else DEFAULT_SPEAKER

    http_opts = types.HttpOptions(timeout=45000)
    if proxy_url:
        http_opts = types.HttpOptions(timeout=45000, client_args={"proxy": proxy_url})

    client = genai.Client(api_key=api_key, http_options=http_opts)

    # Список моделей для попытки вызова (на случай временного 429 или планового обслуживания)
    models_to_try = [model_name] + [m for m in GEMINI_FLASH_TTS_MODELS if m != model_name]
    
    last_err = None
    response = None

    for m in models_to_try:
        try:
            print(f"Synthesizing pure news text with Gemini model '{m}' (Speaker: {chosen_speaker})...")
            response = client.models.generate_content(
                model=m,
                contents=clean_text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=chosen_speaker
                            )
                        )
                    )
                )
            )
            if response and response.candidates:
                break
        except Exception as e:
            last_err = e
            err_str = str(e)
            print(f"Notice: Gemini TTS model '{m}' returned: {err_str[:120]}")
            time.sleep(1.0)
            continue

    if not response or not response.candidates:
        raise RuntimeError(f"Не удалось озвучить текст через Gemini TTS (Callirrhoe): {last_err}")

    audio_bytes = None
    mime_type = ""

    for candidate in response.candidates:
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    audio_bytes = part.inline_data.data
                    mime_type = getattr(part.inline_data, 'mime_type', '')
                    break
        if audio_bytes:
            break

    if not audio_bytes:
        raise RuntimeError("Google Gemini TTS не вернул аудиоданные для голоса Callirrhoe")

    temp_raw = output_path + ".temp.raw"
    with open(temp_raw, "wb") as f:
        f.write(audio_bytes)

    # Конвертируем в 48kHz stereo WAV для ТВ-монтажа
    if "pcm" in mime_type.lower() or "raw" in mime_type.lower() or not mime_type:
        cmd = [
            'ffmpeg', '-y',
            '-f', 's16le', '-ar', '24000', '-ac', '1',
            '-i', temp_raw,
            '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le',
            output_path
        ]
    else:
        cmd = [
            'ffmpeg', '-y', '-i', temp_raw,
            '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le',
            output_path
        ]

    subprocess.run(cmd, capture_output=True, check=True)
    if os.path.exists(temp_raw):
        os.remove(temp_raw)

    return get_audio_duration(output_path)

def generate_speech(
    text: str,
    news_id: str,
    config: Any,
    force_edge: bool = False
) -> Tuple[str, float, str]:
    """
    Генерация озвучки диктора:
    Строго и исключительно Gemini TTS с голосом Callirrhoe.
    Передаётся чистый текст новости из поля без инструкций и надстроек.
    """
    output_filename = f"{news_id}_voice.wav"
    output_path = os.path.join(CACHE_AUDIO_DIR, output_filename)

    api_key = getattr(config, 'gemini_api_key', '').strip().strip("'\"").strip()
    if not api_key:
        raise ValueError("Для генерации голоса Callirrhoe требуется Gemini API Key в настройках.")

    model = getattr(config, 'gemini_tts_model', 'gemini-3.8-flash-tts').strip()
    if not model or "pro" in model:
        # Pro модель блокируется на Free Tier (limit: 0), переключаем на проверенную Flash TTS
        model = "gemini-3.8-flash-tts"

    proxy = getattr(config, 'proxy_url', '').strip()

    speaker = getattr(config, 'tts_speaker', DEFAULT_SPEAKER) or DEFAULT_SPEAKER

    duration = generate_gemini_tts(
        text=text,
        output_path=output_path,
        api_key=api_key,
        model_name=model,
        speaker=speaker,
        proxy_url=proxy
    )

    return output_path, duration, f"Gemini ({speaker})"
