#!/usr/bin/env python3
"""
fetch_channel_info.py
One-off script: fetches YouTube channel descriptions, then uses Claude to
generate both a short SEO meta description and a rich page description
per channel. Results saved to feeds/<slug>.channel_info.json.
"""
import json
import os
import sys
import html
import requests
import anthropic
from pathlib import Path

API_KEY       = os.environ.get("YOUTUBE_API_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not API_KEY:
    print("ERROR: YOUTUBE_API_KEY not set"); sys.exit(1)
if not ANTHROPIC_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set"); sys.exit(1)

CHANNELS_FILE = Path("channels.json")
FEEDS_DIR     = Path("feeds")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def fetch_youtube_info(channel_id: str) -> dict:
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?key={API_KEY}&id={channel_id}&part=snippet"
    )
    data = requests.get(url, timeout=15).json()
    if "error" in data:
        raise Exception(data["error"]["message"])
    items = data.get("items", [])
    if not items:
        raise Exception(f"No channel found for {channel_id}")
    snippet = items[0]["snippet"]
    return {
        "title":       html.unescape(snippet["title"]),
        "description": html.unescape(snippet.get("description", "")),
    }


def generate_descriptions(name: str, yt_description: str, lang: str) -> dict:
    if lang == "he":
        language_instruction = "כתוב בעברית."
        page_desc_prompt = f"""אתה כותב תיאור עמוד לפודקאסט של {name} לקהל יהודי דתי.

תיאור ערוץ היוטיוב (להשראה בלבד):
{yt_description[:600] if yt_description else "(אין)"}

כתוב תיאור עשיר ומשכנע של 2-3 פסקאות קצרות בעברית הכולל:
1. מי הוא {name} ומה הוא מלמד (בהשראת תיאור היוטיוב)
2. יתרונות הפודקאסט: האזנה ברקע בזמן נסיעה ברכב, הליכה ברחוב עם הטלפון בכיס, ביצוע מטלות יומיומיות — ממשיכים ללמוד תורה בכל מקום ובכל זמן
3. מעקב אחר פרקים שנשמעו כבר, תחושת סיפוק והישג מהתקדמות בלימוד, העשרת הידע
4. הגנה מתוכן לא רצוי: ביוטיוב עלולים להופיע סרטונים בלתי הולמים בצד — הפודקאסט מגן על טהרת הנפש, הלב, העיניים והאוזניים. הגנה על הילדים מחשיפה לתוכן פרוץ
5. ללא פרסומות מפסיקות

טון חם, ערכי, קהילתי. אל תשתמש בכותרות או רשימות — רק פסקאות רהוטות.
החזר רק את הטקסט, ללא הקדמה."""

        meta_prompt = f"""כתוב תיאור מטא קצר בעברית (עד 155 תווים) לעמוד הפודקאסט של {name}.
הדגש: האזנה ברקע, ללא פרסומות, ללא תוכן פרוץ, טהרת הנפש.
החזר רק את הטקסט."""

    else:
        page_desc_prompt = f"""Tu rédiges la description d'une page de podcast dédiée à {name}, pour un public juif francophone.

Description de la chaîne YouTube (pour inspiration uniquement) :
{yt_description[:600] if yt_description else "(aucune)"}

Rédige une description riche et convaincante de 2-3 courts paragraphes en français incluant :
1. Qui est {name} et ce qu'il enseigne (inspiré de la description YouTube)
2. Les avantages du podcast : écoute en arrière-plan pendant le trajet en voiture, les courses, une promenade avec le téléphone dans la poche — continuer à apprendre la Torah en toutes circonstances
3. Le suivi des épisodes déjà écoutés, la satisfaction et la fierté de progresser dans l'étude, l'enrichissement des connaissances
4. La protection contre les contenus indésirables : sur YouTube, des vidéos inappropriées peuvent apparaître à côté — le podcast protège la pureté de l'âme, du cœur, des yeux et des oreilles. Protection des enfants contre l'exposition à des contenus impudiques (pritsout)
5. Aucune publicité interrompant l'écoute

Ton chaleureux, ancré dans les valeurs de la communauté. Pas de titres ni de listes — uniquement des paragraphes fluides.
Retourne uniquement le texte, sans introduction."""

        meta_prompt = f"""Écris une meta description courte en français (max 155 caractères) pour la page podcast de {name}.
Insiste sur : écoute en arrière-plan, sans pub, sans contenu inapproprié, pureté de l'âme.
Retourne uniquement le texte."""

    page_resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": page_desc_prompt}],
    )
    page_description = page_resp.content[0].text.strip()

    meta_resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": meta_prompt}],
    )
    seo_description = meta_resp.content[0].text.strip().strip('"')

    return {
        "seo_description":  seo_description,
        "page_description": page_description,
    }


def main():
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    for ch in channels:
        if not ch.get("enabled"):
            continue
        slug = ch["slug"]
        out  = FEEDS_DIR / f"{slug}.channel_info.json"

        existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        if existing.get("page_description"):
            print(f"  {slug} — already has page description, skipping")
            continue

        print(f"  {slug}: fetching YouTube info...")
        try:
            info = fetch_youtube_info(ch["youtube_channel_id"])
        except Exception as e:
            print(f"    ERROR fetching YouTube info: {e}")
            continue

        print(f"    Generating descriptions with Claude...")
        try:
            descs = generate_descriptions(
                ch["podcast_author"],
                info["description"],
                ch.get("podcast_language", "fr"),
            )
            info.update(descs)
            print(f"    SEO  → {descs['seo_description']}")
            print(f"    Page → {descs['page_description'][:120]}...")
        except Exception as e:
            print(f"    ERROR generating descriptions: {e}")
            info["seo_description"]  = ""
            info["page_description"] = ""

        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    Saved → {out}")


if __name__ == "__main__":
    main()
