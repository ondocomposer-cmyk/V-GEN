import os
import json
import time
from typing import List, Dict, Any, Optional

NEWS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "news_items.json")

def load_news_items() -> List[Dict[str, Any]]:
    if os.path.exists(NEWS_FILE):
        try:
            with open(NEWS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading news items: {e}")
    return []

def save_news_items(items: List[Dict[str, Any]]):
    os.makedirs(os.path.dirname(NEWS_FILE), exist_ok=True)
    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def get_news_item(news_id: str) -> Optional[Dict[str, Any]]:
    items = load_news_items()
    for it in items:
        if it.get("id") == news_id:
            return it
    return None

def upsert_news_item(item: Dict[str, Any]):
    items = load_news_items()
    found = False
    for i, it in enumerate(items):
        if it.get("id") == item.get("id"):
            items[i] = item
            found = True
            break
    if not found:
        items.insert(0, item)
    save_news_items(items)

def delete_news_item(news_id: str):
    items = load_news_items()
    items = [it for it in items if it.get("id") != news_id]
    save_news_items(items)
