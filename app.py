import os
import time
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    """يسمح لأي صفحة ويب (مثل واجهتك الأمامية) بجلب البيانات من هذا الـ API."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ==============================
# إعدادات football-data.org
# ==============================
# يفضل وضع المفتاح كمتغير بيئة (Environment Variable) باسم FOOTBALL_DATA_TOKEN
# بدلاً من كتابته مباشرة في الكود، لكن تم توفير قيمة افتراضية هنا للتجربة السريعة.
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", "a716c5d5b9614ff89f550e0642fef54d")
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"

# البطولات الـ 12 المتاحة على الخطة المجانية لـ football-data.org
# (رمز كل بطولة + اسمها بالعربية)
COMPETITIONS = {
    "WC": "كأس العالم",
    "CL": "دوري أبطال أوروبا",
    "EC": "بطولة أمم أوروبا",
    "PL": "الدوري الإنجليزي الممتاز",
    "PD": "الدوري الإسباني",
    "SA": "الدوري الإيطالي",
    "BL1": "الدوري الألماني",
    "FL1": "الدوري الفرنسي",
    "PPL": "الدوري البرتغالي",
    "DED": "الدوري الهولندي",
    "ELC": "التشامبيونشيب الإنجليزي",
    "BSA": "الدوري البرازيلي",
}
COMPETITIONS_PARAM = ",".join(COMPETITIONS.keys())

# تطبيع حالات المباراة القادمة من football-data.org إلى رموز بسيطة
# يستخدمها الفرونت إند (نفس الأسلوب بغض النظر عن مزوّد البيانات)
STATUS_MAP = {
    "SCHEDULED": "NS",
    "TIMED": "NS",
    "IN_PLAY": "LIVE",
    "PAUSED": "HT",
    "FINISHED": "FT",
    "POSTPONED": "PST",
    "SUSPENDED": "SUSP",
    "CANCELLED": "CANC",
    "AWARDED": "AWD",
}

STATUS_LABELS_AR = {
    "NS": "لم تبدأ بعد",
    "LIVE": "مباشر الآن",
    "HT": "استراحة",
    "FT": "انتهت المباراة",
    "PST": "مؤجلة",
    "SUSP": "معلقة",
    "CANC": "ملغاة",
    "AWD": "محسومة",
}

# ==============================
# كاش بسيط في الذاكرة (In-memory cache)
# ==============================
_matches_cache = {"data": None, "timestamp": 0}
CACHE_TTL_SECONDS = 300  # 5 دقائق (طلب واحد فقط لكل تحديث، لا داعي لكاش أطول)


def parse_utc_timestamp(utc_date_str):
    """يحوّل صيغة ISO8601 (مثل 2026-07-04T16:00:00Z) إلى unix timestamp."""
    dt = datetime.strptime(utc_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_major_matches_3_days():
    """
    يجلب مباريات أمس + اليوم + غداً لكل البطولات الكبرى المتاحة على الخطة
    المجانية لـ football-data.org، بطلب واحد فقط لتوفير حصة الاستخدام اليومية.
    """
    today_utc = datetime.now(timezone.utc).date()
    date_from = today_utc - timedelta(days=1)   # أمس
    date_to = today_utc + timedelta(days=2)     # غداً + 1 (dateTo غير شامل، راجع توثيق football-data.org)

    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
    params = {
        "dateFrom": date_from.strftime("%Y-%m-%d"),
        "dateTo": date_to.strftime("%Y-%m-%d"),
        "competitions": COMPETITIONS_PARAM,
    }

    resp = requests.get(
        f"{FOOTBALL_DATA_BASE_URL}/matches",
        headers=headers,
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    matches = []
    for m in payload.get("matches", []):
        competition = m.get("competition", {})
        comp_code = competition.get("code")
        league_name = COMPETITIONS.get(comp_code, competition.get("name", comp_code))

        status_raw = m.get("status", "SCHEDULED")
        status_short = STATUS_MAP.get(status_raw, "NS")

        score = m.get("score", {}) or {}
        full_time = score.get("fullTime", {}) or {}

        home_team = m.get("homeTeam", {}) or {}
        away_team = m.get("awayTeam", {}) or {}

        matches.append({
            "id": m.get("id"),
            "date": m.get("utcDate"),
            "timestamp": parse_utc_timestamp(m["utcDate"]),
            "status_short": status_short,
            "status_long": STATUS_LABELS_AR.get(status_short, status_raw),
            "elapsed": m.get("minute"),
            "home_team": home_team.get("name") or home_team.get("shortName"),
            "away_team": away_team.get("name") or away_team.get("shortName"),
            "home_logo": home_team.get("crest"),
            "away_logo": away_team.get("crest"),
            "home_score": full_time.get("home"),
            "away_score": full_time.get("away"),
            "league_id": comp_code,
            "league_name": league_name,
            "league_logo": competition.get("emblem"),
            "round": f"الجولة {m.get('matchday')}" if m.get("matchday") else m.get("stage"),
        })

    matches.sort(key=lambda x: x["timestamp"])
    return matches


@app.route('/')
def home():
    return jsonify({
        "status": "ok",
        "message": "Matches summary API is running (football-data.org)",
        "endpoints": ["/api/matches"],
    })


@app.route('/api/matches')
def api_matches():
    """
    يرجع مباريات أمس + اليوم + غداً للبطولات الكبرى فقط
    (كأس العالم، أبطال أوروبا، الدوريات الأوروبية الكبرى، الدوري البرازيلي...).
    نتائج مخزنة مؤقتاً (كاش) لمدة CACHE_TTL_SECONDS لتقليل عدد الطلبات.
    استخدم ?refresh=1 لإجبار تحديث الكاش.
    """
    now = time.time()
    force_refresh = request.args.get("refresh") == "1"
    cache_is_fresh = (
        _matches_cache["data"] is not None
        and (now - _matches_cache["timestamp"]) < CACHE_TTL_SECONDS
    )

    if not force_refresh and cache_is_fresh:
        return jsonify({
            "success": True,
            "cached": True,
            "cached_at": _matches_cache["timestamp"],
            "count": len(_matches_cache["data"]),
            "matches": _matches_cache["data"],
        })

    try:
        matches = fetch_major_matches_3_days()
        _matches_cache["data"] = matches
        _matches_cache["timestamp"] = now
        print(f"[matches] fetched {len(matches)} major-league fixtures (yesterday/today/tomorrow)", flush=True)
        return jsonify({
            "success": True,
            "cached": False,
            "cached_at": now,
            "count": len(matches),
            "matches": matches,
        })
    except requests.exceptions.RequestException as e:
        # في حال فشل الطلب، أرجع آخر بيانات مخزنة إن وجدت بدل الفشل الكامل
        if _matches_cache["data"] is not None:
            return jsonify({
                "success": True,
                "cached": True,
                "stale": True,
                "error": str(e),
                "cached_at": _matches_cache["timestamp"],
                "count": len(_matches_cache["data"]),
                "matches": _matches_cache["data"],
            })
        return jsonify({
            "success": False,
            "error": str(e),
            "matches": [],
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
