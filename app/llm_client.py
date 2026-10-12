import re
import time
from typing import Dict, Any, Tuple, Optional
import httpx
from google import genai
from google.genai import types

def call_gemini(
    prompt: str,
    api_key: str,
    model: str = "gemini-1.5-flash",
    proxy_url: str = "",
    enable_grounding: bool = False
) -> str:
    """Вызов Google Gemini API с опциональным поиском в реальном времени (Grounding with Google Search)"""
    http_opts = types.HttpOptions(timeout=30000)
    if proxy_url:
        http_opts = types.HttpOptions(timeout=30000, client_args={"proxy": proxy_url})
    client = genai.Client(api_key=api_key, http_options=http_opts)

    cfg = None
    if enable_grounding:
        cfg = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2
        )

    resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
    return resp.text or ""

def call_openai_compatible(
    prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    proxy_url: str = "",
    timeout: float = 25.0
) -> str:
    """Вызов любого OpenAI-совместимого API (Groq Cloud, OpenAI, DeepSeek, Qwen)"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1500
    }
    client_kwargs = {"timeout": timeout}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    with httpx.Client(**client_kwargs) as client:
        resp = client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
        if resp.status_code != 200:
            err = resp.text
            raise RuntimeError(f"HTTP {resp.status_code}: {err[:250]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

def call_groq(
    prompt: str,
    api_key: str,
    model: str = "openai/gpt-oss-120b",
    proxy_url: str = "",
    timeout: float = 25.0
) -> str:
    """Вызов Groq Cloud (Free Tier) по протоколу OpenAI с авто-подбором модели при устаревании"""
    models_to_try = [
        model.strip() if model else "openai/gpt-oss-120b",
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b"
    ]
    ordered_models = []
    seen = set()
    for m in models_to_try:
        if m and m not in seen:
            seen.add(m)
            ordered_models.append(m)

    last_err = ""
    for m in ordered_models:
        try:
            return call_openai_compatible(
                prompt=prompt,
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1",
                model=m,
                proxy_url=proxy_url,
                timeout=timeout
            )
        except Exception as e:
            last_err = str(e)
            if any(term in last_err.lower() for term in ["decommissioned", "not found", "model_not_found", "404", "400", "429", "rate limit", "rate_limit"]):
                time.sleep(1.0)
                continue
            raise

    # Если все модели исчерпали минутный лимит токенов (TPM), ждем 3 секунды и пробуем легкую модель
    if "429" in last_err or "rate limit" in last_err.lower():
        time.sleep(3.0)
        return call_openai_compatible(
            prompt=prompt,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            model="openai/gpt-oss-20b",
            proxy_url=proxy_url,
            timeout=timeout
        )

    raise RuntimeError(f"Groq API error: {last_err}")

def call_claude(
    prompt: str,
    api_key: str,
    model: str = "claude-3-5-sonnet-20241022",
    proxy_url: str = "",
    timeout: float = 25.0
) -> str:
    """Вызов Anthropic Claude API"""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "max_tokens": 1500,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    client_kwargs = {"timeout": timeout}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    with httpx.Client(**client_kwargs) as client:
        resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        if resp.status_code != 200:
            err = resp.text
            raise RuntimeError(f"Claude error {resp.status_code}: {err[:200]}")
        data = resp.json()
        content_blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in content_blocks)

def query_llm(prompt: str, config: Any, enable_grounding: bool = False) -> str:
    """
    Вызов Google Gemini API с автоматическим резервированием моделей Flash.
    """
    proxy = getattr(config, "proxy_url", "").strip()
    key = getattr(config, "gemini_api_key", "").strip().strip("'\"").strip()
    model = getattr(config, "gemini_text_model", "gemini-3.8-flash").strip()
    if not key:
        raise ValueError("Не указан Google Gemini API Key. Получите бесплатный ключ на aistudio.google.com/apikey")

    models_to_try = [
        model,
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-3.5-flash"
    ]
    ordered = []
    seen = set()
    for m in models_to_try:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)

    last_err = None
    for m in ordered:
        try:
            return call_gemini(prompt, key, model=m, proxy_url=proxy, enable_grounding=enable_grounding)
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if any(term in err_str for term in ["404", "not_found", "decommissioned", "503", "unavailable"]):
                continue
            raise e

    raise last_err or RuntimeError("Не удалось выполнить запрос к Google Gemini API")

def test_ai_provider_connection(
    provider: str,
    api_key: str,
    model: str = "",
    proxy_url: str = ""
) -> Tuple[bool, str]:
    """Проверка подключения к выбранному провайдеру ИИ (Google Gemini или Groq Cloud)"""
    p = provider.lower().strip()
    test_prompt = "Ответь одним словом: ГОТОВО."
    clean_key = api_key.strip().strip("'\"").strip()
    if not clean_key:
        return False, "API ключ не указан."

    try:
        if p == "groq":
            if not clean_key.startswith("gsk_"):
                return False, f"Ключ Groq Cloud должен начинаться на 'gsk_...'. Проверьте скопированный ключ в консоли console.groq.com/keys (вы ввели: '{clean_key[:8]}...')."

            m = model.strip() or "openai/gpt-oss-120b"
            try:
                res = call_groq(test_prompt, clean_key, model=m, proxy_url=proxy_url)
                return True, f"Успешно! Groq Cloud ({m}) отвечает: «{res.strip()}»"
            except Exception as e:
                err_str = str(e)
                err_lower = err_str.lower()
                if "401" in err_str or "invalid_api_key" in err_lower:
                    return False, "Неверный API ключ Groq Cloud (код 401). Убедитесь, что ключ активен на console.groq.com/keys и скопирован целиком."
                if "429" in err_str or "rate_limit" in err_lower:
                    return False, "Превышен минутный лимит запросов Groq Cloud (Free Tier). Подождите 30 секунд."
                if any(w in err_lower for w in ["timeout", "timed out", "connect", "winerror"]):
                    return False, f"Ошибка сети при подключении к api.groq.com: {e}. Проверьте подключение к интернету или прокси."
                return False, f"Ошибка Groq Cloud: {e}"

        elif p == "gemini":
            models_to_try = [
                model.strip() if model else "gemini-3.8-flash",
                "gemini-3.8-flash",
                "gemini-3.5-flash-lite",
                "gemini-flash-latest",
                "gemini-3.5-flash",
                "gemini-pro-latest"
            ]
            ordered_models = []
            seen = set()
            for m in models_to_try:
                if m and m not in seen:
                    seen.add(m)
                    ordered_models.append(m)

            last_err = ""
            for m in ordered_models:
                try:
                    res = call_gemini(test_prompt, clean_key, model=m, proxy_url=proxy_url)
                    return True, f"Успешно! Google Gemini ({m}) отвечает: «{res.strip()}»"
                except Exception as e:
                    last_err = str(e)
                    if any(term in last_err.lower() for term in ["404", "not_found", "503", "unavailable"]):
                        continue
                    break

            err_lower = last_err.lower()
            if "leaked" in err_lower or "compromised" in err_lower:
                return False, "Ваш API ключ был заблокирован Google из-за утечки в публичный доступ (Your API key was reported as leaked). Перейдите в Google AI Studio (aistudio.google.com/apikey), создайте новый бесплатный ключ и вставьте его сюда."
            if "resource_exhausted" in err_lower or "429" in err_lower or "quota" in err_lower:
                return False, "Превышен минутный или суточный лимит бесплатных запросов к модели (Quota Exceeded). Подождите либо создайте новый бесплатный ключ в Google AI Studio."
            if "api_key_service_blocked" in err_lower or "access_token_type_unsupported" in err_lower:
                return False, "Ключ заблокирован для сервиса Gemini API (код API_KEY_SERVICE_BLOCKED). Скопируйте основной ключ из Google AI Studio (начинающийся на 'AIzaSy...')."
            if any(w in err_lower for w in ["user location is not supported", "location is not supported", "country not supported"]):
                return False, "Геоблокировка Google: API недоступен из вашего региона напрямую. Включите системный VPN (США/Европа) либо укажите рабочий прокси."
            if any(w in err_lower for w in ["timed out", "timeout", "winerror 10060", "connection refused", "connecterror"]):
                return False, "Таймаут соединения с Google: проверьте интернет или включите VPN/прокси."
            if "api key not valid" in err_lower or ("400" in last_err and "key" in err_lower):
                return False, "Неверный API ключ Google Gemini: проверьте правильность ключа из Google AI Studio."
            return False, f"Ошибка Gemini API: {last_err}"

        else:
            return False, f"Неизвестный провайдер: {provider}. Выберите 'gemini' или 'groq'."

    except Exception as e:
        return False, f"Ошибка соединения: {e}"
