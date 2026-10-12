import os
import json
from pydantic import BaseModel, Field
from typing import List, Optional

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "config.json")

DEFAULT_SEARCH_PROMPT = """1. ГЕОГРАФИЯ И ФИЛЬТР ОТБОРА
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
- Объём: строго 800–1000 символов с пробелами на одну новость (хронометраж читки ~45–60 секунд).
- Стиль: телевизионный новостной, устная речь. Без канцелярских штампов («имеет место быть», «в целях реализации мероприятий»). В сокращенную новость не надо добавлять обращений к зрителям. Сухая информация по теме новости и все.
- Числа и даты: записывать словами там, где диктору легко запнуться (например: «седьмого октября», «более тридцати лет»).
- Динамика: первое предложение — суть новости (хук), далее факты и подробности, финал — последствия, статус или дата следующего события/анонса.

4. СТРУКТУРА ВЫХОДНОГО ОТВЕТА (ДЛЯ КАЖДОЙ НОВОСТИ)
Отдавай результат строго по следующему шаблону:

---
ЗАГОЛОВОК: [Краткий ёмкий заголовок до 60 символов]
РУБРИКА: [Происшествия / ЖКХ / Власть / Культура / Спорт / Афиша]
ХРОНОМЕТРАЖ: [~45–60 сек]

ТЕКСТ ДЛЯ ДИКТОРА:
[Текст строго от 800 до 1000 знаков, полностью адаптированный под выразительную устную начитку].
---"""

class NewsSource(BaseModel):
    id: str
    name: str
    url: str
    type: str = "rss"  # 'rss', 'vk', 'telegram', 'site'
    enabled: bool = True
    category: str = "Общие"

class AppConfig(BaseModel):
    # Провайдер нейросети для поиска, отбора и рерайта (Google Gemini)
    ai_provider: str = "gemini"
    
    # 1. Google Gemini (Google AI Studio - Бесплатный Free Tier с веб-поиском)
    gemini_api_key: str = ""
    gemini_text_model: str = "gemini-3.8-flash"
    gemini_use_search_grounding: bool = True  # Grounding with Google Search в реальном времени

    # Поисковый и редакционный промпт (может редактироваться в админке)
    search_prompt: str = Field(default=DEFAULT_SEARCH_PROMPT)

    search_mode: str = "dynamic"  # 'dynamic' (поиск без жестких списков) или 'custom'

    # Настройки синтеза речи (Google Gemini TTS: выбор из всех голосов Gemini, по умолчанию Callirrhoe)
    gemini_tts_model: str = "gemini-3.8-flash-tts"
    tts_speaker: str = "Callirrhoe"
    tts_language: str = "ru"

    # Сетевые настройки и интеграции
    proxy_url: str = ""  # Например: http://127.0.0.1:1080 или http://127.0.0.1:7890 (для работы Gemini API и Telegram в РФ)
    vk_service_token: str = ""  # Сервисный ключ доступа приложения VK (vk.com/dev)

    # Оформление и медиа
    bumper_path: str = r"F:\новостник\отбивка.mp4"
    default_music_path: str = r"F:\новостник\музыка.wav"
    logo_path: str = r"F:\новостник\логотип.png"
    logo_position: str = "top_right"  # 'top_right', 'top_left', 'bottom_right'
    city_videos_dir: str = r"F:\КОНСЕРВЫ"
    
    music_volume_db: float = -5.4
    voice_lead_in: float = 1.0
    voice_lead_out: float = 3.0
    slide_duration_target: float = 8.0
    min_images_per_news: int = 5
    max_images_per_news: int = 10
    
    news_days: int = 7
    max_chars: int = 1000

    sources: List[NewsSource] = Field(default_factory=lambda: [
        # --- Сайты и СМИ ---
        NewsSource(id="76ru", name="76.RU | Ярославль", url="https://76.ru/text/rss.xml", type="rss", enabled=True, category="СМИ и порталы"),
        NewsSource(id="vesti_yar", name="Вести Ярославль (ГТРК)", url="https://vesti-yaroslavl.ru/novosti", type="site", enabled=True, category="СМИ и порталы"),
        NewsSource(id="admtmr_site", name="Администрация Тутаевского района (Сайт)", url="https://admtmr.ru/", type="site", enabled=True, category="Официальные сайты"),
        NewsSource(id="za_tutaev_site", name="ЗА ТУТАЕВ (Сайт)", url="https://za-tutaev.ru/", type="site", enabled=True, category="СМИ и порталы"),
        NewsSource(id="yarreg_site", name="Ярославский регион", url="https://yarreg.ru/", type="site", enabled=True, category="СМИ и порталы"),
        NewsSource(id="sevkray_site", name="Северный край", url="https://sevkray.ru/", type="site", enabled=True, category="СМИ и порталы"),
        NewsSource(id="yarnovosti_site", name="ЯРНОВОСТИ (Сайт)", url="https://yarnovosti.com/", type="site", enabled=True, category="СМИ и порталы"),
        NewsSource(id="firstyar_site", name="Первый Ярославский (Сайт)", url="https://1yar.tv/", type="site", enabled=True, category="СМИ и порталы"),

        # --- ВКонтакте: Городские паблики и районы Тутаева ---
        NewsSource(id="vk_tutaev", name="ТУТАЕВ | ПОДСЛУШАНО", url="https://vk.com/tutaev", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_tutaevtoday", name="ТУТАЕВ СЕГОДНЯ", url="https://vk.com/tutaevtoday", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_tipitutaev", name="ТИПИЧНЫЙ ТУТАЕВ", url="https://vk.com/tipitutaev", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_vtutaeve", name="Мой Тутаев | Главные новости", url="https://vk.com/vtutaeve", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_podslushanovtutaeve", name="Подслушано в Тутаеве", url="https://vk.com/podslushanovtutaeve", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_overheard_by_tutaev", name="Подслушано Тутаев", url="https://vk.com/overheard_by_tutaev", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_za_tutaev", name="ЗА ТУТАЕВ", url="https://vk.com/za_tutaev", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),
        NewsSource(id="vk_vecherniy_konst", name="ВЕЧЕРНИЙ КОНСТАНТИНОВСКИЙ", url="https://vk.com/vecherniy_konstantinovskiy", type="vk", enabled=True, category="ВКонтакте (Поселения)"),
        NewsSource(id="vk_pos_konst", name="Константиновский | Тутаевский район", url="https://vk.com/pos_konstantinovskiy", type="vk", enabled=True, category="ВКонтакте (Поселения)"),
        NewsSource(id="vk_podslushano_chebakovo", name="Подслушано Чебаково", url="https://vk.com/podslushano_chebakovo", type="vk", enabled=True, category="ВКонтакте (Поселения)"),
        NewsSource(id="vk_chebakovo_selo", name="Чебаково Тутаевский район", url="https://vk.com/chebakovo_selo", type="vk", enabled=True, category="ВКонтакте (Поселения)"),
        NewsSource(id="vk_fominskoe_news", name="Фоминское | Тутаевский район", url="https://vk.com/fominskoe_news", type="vk", enabled=True, category="ВКонтакте (Поселения)"),
        NewsSource(id="vk_pshenichishe", name="Пшеничище | Новости села", url="https://vk.com/pshenichishe", type="vk", enabled=True, category="ВКонтакте (Поселения)"),
        NewsSource(id="vk_levoberezhny_tutaev", name="Левобережный Тутаев", url="https://vk.com/levoberezhny_tutaev", type="vk", enabled=True, category="ВКонтакте (Тутаев)"),

        # --- ВКонтакте: Официальные ведомства и культура Тутаева ---
        NewsSource(id="vk_tutaevadm", name="Администрация Тутаевского округа", url="https://vk.com/tutaevadm", type="vk", enabled=True, category="ВКонтакте (Официальные)"),
        NewsSource(id="vk_tutaevcrb", name="Тутаевская ЦРБ", url="https://vk.com/tutaevcrb", type="vk", enabled=True, category="ВКонтакте (Официальные)"),
        NewsSource(id="vk_dept_edu_tutaev", name="Департамент образования ТМР", url="https://vk.com/dept_edu_tutaev", type="vk", enabled=True, category="ВКонтакте (Официальные)"),
        NewsSource(id="vk_kulturatmr", name="Департамент культуры и туризма ТМР", url="https://vk.com/kulturatmr", type="vk", enabled=True, category="ВКонтакте (Официальные)"),
        NewsSource(id="vk_djkh_tutaev", name="Департамент ЖКХ и строительства ТМР", url="https://vk.com/djkh_tutaev", type="vk", enabled=True, category="ВКонтакте (Официальные)"),
        NewsSource(id="vk_rdk_malova", name="РДК им. А.Г. Малова", url="https://vk.com/rdk_malova", type="vk", enabled=True, category="ВКонтакте (Культура)"),
        NewsSource(id="vk_gortutaev", name="Городской клуб (Левый берег)", url="https://vk.com/gortutaev", type="vk", enabled=True, category="ВКонтакте (Культура)"),
        NewsSource(id="vk_dk_voshod_konst", name="ДК «Восход» (п. Константиновский)", url="https://vk.com/dk_voshod_konst", type="vk", enabled=True, category="ВКонтакте (Культура)"),
        NewsSource(id="vk_fominskiy_sdk", name="Фоминский сельский ДК", url="https://vk.com/fominskiy_sdk", type="vk", enabled=True, category="ВКонтакте (Культура)"),
        NewsSource(id="vk_chebakovskiy_sdk", name="Чебаковский сельский ДК", url="https://vk.com/chebakovskiy_sdk", type="vk", enabled=True, category="ВКонтакте (Культура)"),
        NewsSource(id="vk_visittutaev", name="Центр «Романов-Борисоглебск»", url="https://vk.com/visittutaev", type="vk", enabled=True, category="ВКонтакте (Культура)"),
        NewsSource(id="vk_mc_galaktika", name="МЦ «Галактика» Тутаевского района", url="https://vk.com/mc_galaktika", type="vk", enabled=True, category="ВКонтакте (Молодежь)"),
        NewsSource(id="vk_tutaev_bibl", name="Центральная библиотека Тутаева", url="https://vk.com/tutaev_bibl", type="vk", enabled=True, category="ВКонтакте (Культура)"),

        # --- Telegram: Официальные каналы ---
        NewsSource(id="tg_adm_tmr", name="Администрация Тутаевского района (TG)", url="https://t.me/adm_tmr", type="telegram", enabled=True, category="Telegram (Официальные)"),
        NewsSource(id="tg_nizova_tmr", name="Ольга Низова (Глава Тутаевского р-на)", url="https://t.me/nizova_tmr", type="telegram", enabled=True, category="Telegram (Официальные)"),
        NewsSource(id="tg_evraev", name="Губернатор Михаил Евраев (TG)", url="https://t.me/evraevmikhail", type="telegram", enabled=True, category="Telegram (Официальные)"),
        NewsSource(id="tg_yarregion", name="Правительство Ярославской области (TG)", url="https://t.me/yarregion", type="telegram", enabled=True, category="Telegram (Официальные)"),
        NewsSource(id="tg_mchs", name="МЧС Ярославской области (TG)", url="https://t.me/mchs_yaroslavl", type="telegram", enabled=True, category="Telegram (Официальные)"),

        # --- Telegram: Городские и районные каналы Тутаева ---
        NewsSource(id="tg_tutpodslushano", name="Подслушано Тутаев (TG)", url="https://t.me/tutpodslushano", type="telegram", enabled=True, category="Telegram (Тутаев)"),
        NewsSource(id="tg_tutaev_live", name="Тутаев Live (TG)", url="https://t.me/tutaev_live", type="telegram", enabled=True, category="Telegram (Тутаев)"),
        NewsSource(id="tg_za_tutaev", name="За Тутаев (TG)", url="https://t.me/za_tutaev", type="telegram", enabled=True, category="Telegram (Тутаев)"),
        NewsSource(id="tg_romanov_borisoglebsk", name="Романов-Борисоглебск Онлайн (TG)", url="https://t.me/romanov_borisoglebsk", type="telegram", enabled=True, category="Telegram (Тутаев)"),

        # --- Telegram: Региональные каналы и оперативные сводки ---
        NewsSource(id="tg_news76", name="76.RU | Новости Ярославля (TG)", url="https://t.me/news76", type="telegram", enabled=True, category="Telegram (Регион)"),
        NewsSource(id="tg_yarregion_news", name="Ярославский регион (TG)", url="https://t.me/yarregion_news", type="telegram", enabled=True, category="Telegram (Регион)"),
        NewsSource(id="tg_vesti_yar", name="Вести Ярославль (TG)", url="https://t.me/vesti_yaroslavl", type="telegram", enabled=True, category="Telegram (Регион)"),
        NewsSource(id="tg_firstyar", name="Первый Ярославский (TG)", url="https://t.me/firstyaroslavskiy", type="telegram", enabled=True, category="Telegram (Регион)"),
        NewsSource(id="tg_yarnovosti", name="ЯРНОВОСТИ (TG)", url="https://t.me/yarnovosti_official", type="telegram", enabled=True, category="Telegram (Регион)"),
        NewsSource(id="tg_zhest_yar", name="Жесть Ярославль (ДТП, ЧП) (TG)", url="https://t.me/zhest_yaroslavl", type="telegram", enabled=True, category="Telegram (Сводки ЧП)")
    ])

def load_config() -> AppConfig:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return AppConfig(**data)
        except Exception as e:
            print(f"Error loading config, using defaults: {e}")
    cfg = AppConfig()
    save_config(cfg)
    return cfg

def save_config(cfg: AppConfig):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg.model_dump(), f, ensure_ascii=False, indent=2)
