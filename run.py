import os
import sys

# Ensure UTF-8 output encoding on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import uvicorn
from app.config import load_config, save_config

def setup_environment():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for d in ["data", "cache/images", "cache/audio", "cache/videos", "cache/music", "static", "templates"]:
        os.makedirs(os.path.join(base_dir, d), exist_ok=True)

    # Check and generate default broadcast logo if missing
    logo_path = os.path.join(base_dir, "логотип.png")
    if not os.path.exists(logo_path):
        from PIL import Image, ImageDraw
        img = Image.new('RGBA', (380, 100), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 10, 370, 90], radius=16, fill=(15, 23, 42, 210), outline=(255, 255, 255, 120), width=2)
        d.rounded_rectangle([12, 22, 110, 78], radius=10, fill=(225, 29, 72, 240))
        d.text((28, 38), "ЭФИР", fill=(255, 255, 255, 255))
        d.text((125, 26), "НОВОСТИ", fill=(255, 255, 255, 255))
        d.text((125, 54), "24  ТВ КАНАЛ", fill=(148, 163, 184, 255))
        img.save(logo_path, 'PNG')
        print(f"Created default broadcast logo at {logo_path}")

    # Ensure config file exists
    load_config()

if __name__ == "__main__":
    setup_environment()
    print("=" * 70)
    print("   V-GEN — АВТОМАТИЧЕСКАЯ ГЕНЕРАЦИЯ ВИДЕОНОВОСТЕЙ ДЛЯ ТВ")
    print("   Автор скрипта: Дмитрий Асанов (@sandefiord, asanov@lux.tv.ru)")
    print("=" * 70)
    print("Веб-интерфейс доступен по адресам:")
    print("  -> Главная (Новости и генерация): http://localhost:8000")
    print("  -> Админ-панель (Настройки V-GEN): http://localhost:8000/admin")
    print("  -> Архив видеороликов:           http://localhost:8000/archive")
    print("=" * 70)
    uvicorn.run("app.web_server:app", host="0.0.0.0", port=8000, reload=False)
