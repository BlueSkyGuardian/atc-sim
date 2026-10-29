"""The news in three languages: French (the default), English and Arabic (right to left).

The words of the server-made pages and of the newsletter e-mail, the section and region names, and dates written
the way each language writes them (Moroccan month names in Arabic). The news page's own words are in news.html.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

LANGS = ("fr", "en", "ar")
DEFAULT = "fr"
NAMES = {"fr": "Français", "en": "English", "ar": "العربية"}
RTL = {"ar"}
OG_LOCALE = {"fr": "fr_FR", "en": "en_US", "ar": "ar_MA"}


def norm(lang: Optional[str]) -> Optional[str]:
    """A language code we write in, or None."""
    lang = str(lang or "").strip().lower()[:2]
    return lang if lang in LANGS else None


def direction(lang: str) -> str:
    return "rtl" if lang in RTL else "ltr"


SECTIONS = {
    "Air traffic control & airspace": {"fr": "Contrôle aérien et espace aérien", "ar": "المراقبة الجوية والمجال الجوي"},
    "Technology & innovation": {"fr": "Technologie et innovation", "ar": "التكنولوجيا والابتكار"},
    "Safety": {"fr": "Sécurité", "ar": "السلامة"},
    "Airlines & airports": {"fr": "Compagnies et aéroports", "ar": "شركات الطيران والمطارات"},
    "Morocco & Africa": {"fr": "Maroc et Afrique", "ar": "المغرب وإفريقيا"},
    "Defence & space": {"fr": "Défense et espace", "ar": "الدفاع والفضاء"},
}
REGIONS = {
    "morocco": {"en": "Morocco", "fr": "Maroc", "ar": "المغرب"},
    "europe": {"en": "Europe", "fr": "Europe", "ar": "أوروبا"},
    "international": {"en": "International", "fr": "International", "ar": "دولي"},
}


def section(name: Optional[str], lang: str) -> str:
    if not name:
        return ""
    return name if lang == "en" else SECTIONS.get(name, {}).get(lang, name)


def region(r: str, lang: str) -> str:
    return REGIONS.get(r, {}).get(lang) or REGIONS.get(r, {}).get("en") or r


MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
           "décembre"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "ماي", "يونيو", "يوليوز", "غشت", "شتنبر", "أكتوبر", "نونبر", "دجنبر"],
}
DAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"],
}


def long_date(day: str, lang: str) -> str:
    """'Thursday 29 October 2026', 'jeudi 29 octobre 2026', 'الخميس 29 أكتوبر 2026'."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{DAYS[lang][d.weekday()]} {d.day} {MONTHS[lang][d.month - 1]} {d.year}"


def short_date(day: str, lang: str) -> str:
    """'29 Oct', '29 oct.', '29 أكتوبر'."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    m = MONTHS[lang][d.month - 1]
    if lang == "en":
        m = m[:3]
    elif lang == "fr":
        m = FR_SHORT[d.month - 1]
    return f"{d.day} {m}"


FR_SHORT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


T = {
    "news": {"en": "Aviation news", "fr": "Actualité aéronautique", "ar": "أخبار الطيران"},
    "from_newsroom": {"en": "From our newsroom", "fr": "De notre rédaction", "ar": "من غرفة أخبارنا"},
    "around_web": {"en": "Around the web", "fr": "Sur le web", "ar": "من الويب"},
    "read_article": {"en": "Read the article →", "fr": "Lire l'article →", "ar": "اقرأ المقال ←"},
    "read_on_site": {"en": "Read it on the site", "fr": "Le lire sur le site", "ar": "اقرأه على الموقع"},
    "footer": {"en": "Headlines and short summaries; each story is on its own site.",
               "fr": "Titres et courts résumés ; chaque article se lit sur son site.",
               "ar": "عناوين وملخصات قصيرة؛ يُقرأ كل خبر على موقعه الأصلي."},
    "why": {"en": "You get this because you subscribed to the simulator's aviation newsletter.",
            "fr": "Vous recevez ce message parce que vous êtes abonné à la lettre d'actualité aéronautique du simulateur.",
            "ar": "تصلك هذه الرسالة لأنك مشترك في النشرة الإخبارية للطيران الخاصة بالمحاكي."},
    "unsubscribe": {"en": "Unsubscribe", "fr": "Se désabonner", "ar": "إلغاء الاشتراك"},
    "only_en": {"en": "", "fr": "Cet article n'existe qu'en anglais.", "ar": "هذا المقال متوفر باللغة الإنجليزية فقط."},
    "sources": {"en": "Sources", "fr": "Sources", "ar": "المصادر"},
    "newsroom": {"en": "ATC SIM newsroom", "fr": "Rédaction ATC SIM", "ar": "غرفة أخبار ATC SIM"},
    "description": {"en": "Aviation news for air traffic controllers: Morocco first, then Europe and the world - air "
                          "traffic control and airspace, technology, safety, airlines and airports.",
                    "fr": "L'actualité aéronautique pour les contrôleurs aériens : le Maroc d'abord, puis l'Europe et le "
                          "monde - contrôle et espace aérien, technologie, sécurité, compagnies et aéroports.",
                    "ar": "أخبار الطيران للمراقبين الجويين: المغرب أولاً ثم أوروبا والعالم - المراقبة الجوية والمجال "
                          "الجوي، التكنولوجيا، السلامة، شركات الطيران والمطارات."},
    "confirm_subject": {"en": "Confirm your subscription · Aviation news",
                        "fr": "Confirmez votre abonnement · Actualité aéronautique",
                        "ar": "أكّد اشتراكك · أخبار الطيران"},
    "confirm_text": {"en": "Someone (hopefully you) asked to receive the daily aviation newsletter of the ATC simulator "
                           "at this address.",
                     "fr": "Quelqu'un (vous, espérons-le) a demandé à recevoir à cette adresse la lettre quotidienne "
                           "d'actualité aéronautique du simulateur ATC.",
                     "ar": "طلب أحدهم (نأمل أن تكون أنت) تلقي النشرة اليومية لأخبار الطيران الخاصة بمحاكي المراقبة "
                           "الجوية على هذا العنوان."},
    "confirm_button": {"en": "Confirm my subscription", "fr": "Confirmer mon abonnement", "ar": "تأكيد اشتراكي"},
    "confirm_ignore": {"en": "If it was not you, ignore this e-mail: nothing will be sent.",
                       "fr": "Si ce n'était pas vous, ignorez ce message : rien ne vous sera envoyé.",
                       "ar": "إذا لم تكن أنت، فتجاهل هذه الرسالة: لن يُرسل إليك شيء."},
}


def t(key: str, lang: str) -> str:
    d = T.get(key, {})
    return d.get(lang) or d.get("en") or key
