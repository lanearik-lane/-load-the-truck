"""
LOAD THE TRUCK — MLB Home Run Intelligence
--------------------------------------------
By-Game Matchup Board: pick a game from the slate, see a matchup banner,
top "barrel signal" reads, and a full color-heatmapped lineup table.

Run locally with:
    streamlit run app.py
"""

import random
import math
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

try:
    import pybaseball as pyb
    from pybaseball import cache as pyb_cache
    pyb_cache.enable()
    PYBASEBALL_OK = True
except Exception:
    PYBASEBALL_OK = False

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
ESPN_MLB_BASE = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb"

# ----------------------------------------------------------------------
# Page config + query params (?game=CWS-BAL)
# ----------------------------------------------------------------------
st.set_page_config(page_title="LOAD THE TRUCK", layout="wide", page_icon="🚚")

params = st.query_params

# ----------------------------------------------------------------------
# Theming -- black theme only
# ----------------------------------------------------------------------
bg, card_bg, text, subtext, border, accent = (
    "#000000", "#141414", "#f5f5f5", "#9aa0a6", "#2a2a2a", "#ff5a1f",
)

st.markdown(
    f"""
    <style>
        .stApp {{ background-color: {bg}; }}
        .brand {{
            font-size: 13px;
            font-weight: 900;
            letter-spacing: 0.08em;
            color: {accent};
            margin-bottom: 2px;
        }}
        .eyebrow {{
            font-size: 12px;
            font-weight: 800;
            letter-spacing: 0.08em;
            color: {accent};
            text-transform: uppercase;
            margin-bottom: 4px;
        }}
        .page-title {{
            font-size: 34px;
            font-weight: 900;
            color: {text};
            margin: 0 0 4px 0;
        }}
        .page-subtitle {{
            font-size: 15px;
            color: {subtext};
            margin-bottom: 18px;
        }}
        .accent-bar {{
            border-left: 5px solid {accent};
            padding-left: 16px;
        }}
        .section-title {{
            font-size: 24px;
            font-weight: 800;
            color: {text};
            margin: 22px 0 10px 0;
        }}
        .section-subtitle {{
            font-size: 18px;
            font-weight: 700;
            color: {text};
            margin: 4px 0 14px 0;
        }}
        .matchup-banner {{
            background-color: {card_bg};
            border: 1px solid {border};
            border-radius: 12px;
            padding: 18px 22px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .matchup-title {{
            font-size: 22px;
            font-weight: 800;
            color: {text};
        }}
        .matchup-meta {{
            font-size: 13px;
            color: {subtext};
            margin-top: 4px;
        }}
        .tag-pill {{
            background-color: #fff3e8;
            color: {accent};
            font-weight: 700;
            font-size: 12px;
            padding: 2px 8px;
            border-radius: 999px;
        }}
        .read-card {{
            background-color: {card_bg};
            border: 1px solid {border};
            border-radius: 12px;
            padding: 14px 16px;
        }}
        .read-name {{
            font-size: 16px;
            font-weight: 800;
            color: {text};
        }}
        .read-score {{
            font-size: 26px;
            font-weight: 900;
            color: {accent};
            float: right;
        }}
        .read-meta {{
            font-size: 12px;
            color: {subtext};
            margin-bottom: 6px;
        }}
        .barrel-badge {{
            background-color: #fff3e8;
            color: {accent};
            font-weight: 700;
            font-size: 11px;
            padding: 2px 7px;
            border-radius: 6px;
            display: inline-block;
            margin-bottom: 6px;
        }}
        .metric-card {{
            background-color: {card_bg};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }}
        .metric-label {{
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.05em;
            color: {subtext};
            text-transform: uppercase;
            margin-bottom: 3px;
        }}
        .metric-value {{
            font-size: 18px;
            font-weight: 700;
            color: {text};
        }}
        .slate-card {{
            background-color: {card_bg};
            border: 2px solid {border};
            border-radius: 10px;
            padding: 10px 14px;
            text-align: center;
        }}
        .slate-card-active {{
            background-color: {card_bg};
            border: 2px solid {accent};
            border-radius: 10px;
            padding: 10px 14px;
            text-align: center;
        }}
        .slate-time {{
            font-size: 12px;
            color: {subtext};
            font-weight: 700;
        }}
        .slate-park {{
            font-size: 11px;
            color: {subtext};
        }}
        .howto-row {{
            background-color: {card_bg};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 10px 14px;
            margin-bottom: 8px;
            font-size: 13px;
            color: {text};
        }}
        .howto-good {{
            color: #1a9e5c;
            font-weight: 800;
        }}
        .howto-caveat {{
            color: {subtext};
            font-weight: 400;
            font-size: 12px;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------
# Helper: defensive column lookup (Savant/FanGraphs column names can
# vary slightly by pybaseball version -- never crash on a rename)
# ----------------------------------------------------------------------
def _find_col(df, candidates):
    if df is None or getattr(df, "empty", True):
        return None
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    for c in df.columns:
        cl = c.lower()
        for cand in candidates:
            if cand.lower() in cl:
                return c
    return None


def _safe_float(val, default=0.0):
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _pct_scale(val):
    """Normalize a percentage-like value to a 0-100 scale regardless of whether the
    source stored it as a fraction (0.095) or already as a percent (9.5). Savant/FanGraphs
    leaderboards aren't 100% consistent about this across endpoints/versions, so every real
    percentage field routes through this before use."""
    v = _safe_float(val)
    return v * 100 if 0 <= v <= 1 else v


# ----------------------------------------------------------------------
# Fallback team list (all 30 MLB teams) -- used only if the live team
# fetch fails, so logos and roster lookups still work offline.
# ----------------------------------------------------------------------
FALLBACK_TEAMS = {
    "LAA": {"name": "Angels", "id": 108}, "ARI": {"name": "Diamondbacks", "id": 109},
    "BAL": {"name": "Orioles", "id": 110}, "BOS": {"name": "Red Sox", "id": 111},
    "CHC": {"name": "Cubs", "id": 112}, "CIN": {"name": "Reds", "id": 113},
    "CLE": {"name": "Guardians", "id": 114}, "COL": {"name": "Rockies", "id": 115},
    "DET": {"name": "Tigers", "id": 116}, "HOU": {"name": "Astros", "id": 117},
    "KC": {"name": "Royals", "id": 118}, "LAD": {"name": "Dodgers", "id": 119},
    "WSH": {"name": "Nationals", "id": 120}, "NYM": {"name": "Mets", "id": 121},
    "ATH": {"name": "Athletics", "id": 133}, "PIT": {"name": "Pirates", "id": 134},
    "SD": {"name": "Padres", "id": 135}, "SEA": {"name": "Mariners", "id": 136},
    "SF": {"name": "Giants", "id": 137}, "STL": {"name": "Cardinals", "id": 138},
    "TB": {"name": "Rays", "id": 139}, "TEX": {"name": "Rangers", "id": 140},
    "TOR": {"name": "Blue Jays", "id": 141}, "MIN": {"name": "Twins", "id": 142},
    "PHI": {"name": "Phillies", "id": 143}, "ATL": {"name": "Braves", "id": 144},
    "CWS": {"name": "White Sox", "id": 145}, "MIA": {"name": "Marlins", "id": 146},
    "NYY": {"name": "Yankees", "id": 147}, "MIL": {"name": "Brewers", "id": 158},
}


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_all_teams():
    """Live team list (id + abbreviation) from the free MLB Stats API."""
    try:
        r = requests.get(f"{MLB_API_BASE}/teams", params={"sportId": 1}, timeout=15)
        r.raise_for_status()
        teams = {}
        for t in r.json().get("teams", []):
            abbr = t.get("abbreviation")
            if abbr:
                teams[abbr] = {"name": t.get("teamName", t.get("name", abbr)), "id": t["id"]}
        return teams if teams else None
    except Exception:
        return None


def team_logo(team_code, size=28):
    """Return an <img> tag pointing at MLB's official static logo CDN."""
    team_id = TEAMS.get(team_code, {}).get("id")
    if not team_id:
        return f'<span style="font-size:{size}px;">⚾</span>'
    url = f"https://www.mlbstatic.com/team-logos/{team_id}.svg"
    return (
        f'<img src="{url}" alt="{team_code}" '
        f'style="height:{size}px; width:{size}px; object-fit:contain; vertical-align:middle;">'
    )


# ----------------------------------------------------------------------
# Live schedule + roster fetchers (MLB Stats API -- free, no key)
# ----------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_schedule_for_date(date_str):
    try:
        r = requests.get(
            f"{MLB_API_BASE}/schedule",
            params={"sportId": 1, "date": date_str, "hydrate": "team,venue,probablePitcher"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        games = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                away_t = g["teams"]["away"]["team"]
                home_t = g["teams"]["home"]["team"]
                games.append(
                    {
                        "game_pk": g.get("gamePk"),
                        "away_id": away_t["id"],
                        "away_abbr": away_t.get("abbreviation"),
                        "home_id": home_t["id"],
                        "home_abbr": home_t.get("abbreviation"),
                        "venue": g.get("venue", {}).get("name", ""),
                        "game_dt_utc": g.get("gameDate"),
                        "away_pitcher": g["teams"]["away"].get("probablePitcher"),
                        "home_pitcher": g["teams"]["home"].get("probablePitcher"),
                    }
                )
        return games
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_active_hitters(team_id):
    try:
        r = requests.get(
            f"{MLB_API_BASE}/teams/{team_id}/roster",
            params={"rosterType": "active"},
            timeout=15,
        )
        r.raise_for_status()
        hitters = []
        for entry in r.json().get("roster", []):
            pos = entry.get("position", {}).get("abbreviation", "")
            if pos == "P":
                continue
            hitters.append({"id": entry["person"]["id"], "name": entry["person"]["fullName"]})
        return hitters
    except Exception:
        return None


def format_game_time(iso_utc):
    try:
        d = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        d_et = d.astimezone(ZoneInfo("America/New_York"))
        return d_et.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return ""


# ----------------------------------------------------------------------
# Real sportsbook odds via ESPN's public (free, keyless, unofficial) API.
# ESPN surfaces real DraftKings/consensus lines in its scoreboard/summary
# JSON -- this is the same data shown on espn.com/mlb/odds. Not an official
# API and could change without notice, so every step here degrades
# gracefully to our own model estimate if anything doesn't match.
# ----------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def fetch_espn_scoreboard(date_str_yyyymmdd):
    try:
        r = requests.get(f"{ESPN_MLB_BASE}/scoreboard", params={"dates": date_str_yyyymmdd}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_espn_summary(event_id):
    try:
        r = requests.get(f"{ESPN_MLB_BASE}/summary", params={"event": event_id}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def find_espn_event(scoreboard_json, away_abbr, home_abbr):
    if not scoreboard_json:
        return None
    for ev in scoreboard_json.get("events", []):
        comps = ev.get("competitions") or [{}]
        competitors = comps[0].get("competitors", [])
        abbrs = {c.get("homeAway"): (c.get("team", {}) or {}).get("abbreviation") for c in competitors}
        if abbrs.get("away") == away_abbr and abbrs.get("home") == home_abbr:
            return ev
    return None


def _espn_moneyline(team_odds):
    if not team_odds:
        return None
    if "moneyLine" in team_odds and team_odds["moneyLine"] not in (None, ""):
        return team_odds["moneyLine"]
    current = team_odds.get("current", {}) or {}
    ml = current.get("moneyLine", {}) or current.get("moneyline", {})
    if isinstance(ml, dict):
        return ml.get("american") or ml.get("value")
    return ml or None


def _espn_side_price(team_odds, key_names):
    """Pull a spread/total price (e.g. -110) from a team odds block, trying a few known shapes."""
    if not team_odds:
        return None
    for key in key_names:
        if key in team_odds and team_odds[key] not in (None, ""):
            return team_odds[key]
    current = team_odds.get("current", {}) or {}
    for key in key_names:
        block = current.get(key, {}) or {}
        if isinstance(block, dict):
            price = block.get("american") or block.get("value")
            if price not in (None, ""):
                return price
    return None


def extract_real_odds(espn_event):
    """Real moneyline / spread(run line) / total(over-under), from ESPN's embedded odds
    or its summary pickcenter. Returns None if no usable odds were found for this event."""
    if not espn_event:
        return None
    comps = espn_event.get("competitions") or [{}]
    odds_list = comps[0].get("odds") or []

    if not odds_list:
        event_id = espn_event.get("id")
        if event_id:
            summary = fetch_espn_summary(event_id)
            if summary:
                odds_list = summary.get("pickcenter") or []

    if not odds_list:
        return None

    o = odds_list[0]
    for cand in odds_list:
        provider_name = ((cand.get("provider") or {}).get("name") or "").lower()
        if provider_name in ("consensus", "espn bet", "draftkings"):
            o = cand
            break

    away_odds = o.get("awayTeamOdds", {}) or {}
    home_odds = o.get("homeTeamOdds", {}) or {}

    home_ml = _espn_moneyline(home_odds)
    away_ml = _espn_moneyline(away_odds)
    total = o.get("overUnder")
    home_spread = o.get("spread")  # ESPN convention: negative favors the home team
    over_price = _espn_side_price(o, ["overOdds"]) or o.get("overOdds")
    under_price = _espn_side_price(o, ["underOdds"]) or o.get("underOdds")
    home_spread_price = _espn_side_price(home_odds, ["spreadOdds", "pointSpreadOdds"])
    away_spread_price = _espn_side_price(away_odds, ["spreadOdds", "pointSpreadOdds"])

    if home_ml is None and away_ml is None and total is None and home_spread is None:
        return None

    return {
        "provider": (o.get("provider") or {}).get("name", "consensus"),
        "home_moneyline": home_ml,
        "away_moneyline": away_ml,
        "total": total,
        "over_price": over_price,
        "under_price": under_price,
        "home_spread": home_spread,
        "away_spread": (-home_spread if isinstance(home_spread, (int, float)) else None),
        "home_spread_price": home_spread_price if home_spread_price is not None else -110,
        "away_spread_price": away_spread_price if away_spread_price is not None else -110,
    }


def american_odds_str(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)
    return f"+{int(round(v))}" if v > 0 else f"{int(round(v))}"


def mock_weather(venue_name, date_str):
    """Weather isn't wired to a live feed yet -- deterministic per venue/date so it's stable on reruns."""
    dome_parks = {
        "Rogers Centre", "Tropicana Field", "Minute Maid Park", "Chase Field",
        "American Family Field", "loanDepot park", "T-Mobile Park", "Globe Life Field",
    }
    if venue_name in dome_parks:
        return {"temp": 72, "condition": "Roof Closed", "icon": "🏟️", "wind": "Climate controlled"}
    rnd = random.Random(abs(hash(f"{venue_name}-{date_str}")) % 100000)
    temp = rnd.randint(65, 92)
    condition = rnd.choice(["Clear", "Partly Cloudy", "Overcast", "Humid"])
    icon = {"Clear": "☀️", "Partly Cloudy": "⛅", "Overcast": "☁️", "Humid": "🌤️"}[condition]
    wind_speed = rnd.randint(2, 14)
    wind_dir = rnd.choice(
        ["out to RF", "out to CF", "out to LF", "in from RF", "in from CF", "in from LF", "calm", "cross LF-RF"]
    )
    return {"temp": temp, "condition": condition, "icon": icon, "wind": f"{wind_speed} mph {wind_dir}"}


# ----------------------------------------------------------------------
# Baseball Savant leaderboards (via the free, keyless pybaseball wrapper)
# ----------------------------------------------------------------------
@st.cache_data(ttl=21600, show_spinner=False)
def fetch_savant_batter_stats(year):
    if not PYBASEBALL_OK:
        return None
    try:
        ev = pyb.statcast_batter_exitvelo_barrels(year, minBBE=1)
        xs = pyb.statcast_batter_expected_stats(year, minPA=1)
        return {"exitvelo": ev, "expected": xs}
    except Exception:
        return None


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_savant_pitcher_stats(year):
    if not PYBASEBALL_OK:
        return None
    try:
        ev = pyb.statcast_pitcher_exitvelo_barrels(year, minBBE=1)
        xs = pyb.statcast_pitcher_expected_stats(year, minPA=1)
        return {"exitvelo": ev, "expected": xs}
    except Exception:
        return None


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_fangraphs_pitching(year):
    """CSW%, SwStr%, Barrel%, HardHit% -- free FanGraphs season leaderboard via pybaseball."""
    if not PYBASEBALL_OK:
        return None
    try:
        return pyb.pitching_stats(year, qual=0)
    except Exception:
        return None


def build_batter_lookup(savant_data):
    """{mlbam_player_id: {...real Savant stats...}}"""
    lookup = {}
    if not savant_data:
        return lookup
    ev, xs = savant_data.get("exitvelo"), savant_data.get("expected")

    id_col_ev = _find_col(ev, ["player_id"])
    if ev is not None and id_col_ev:
        brl_col = _find_col(ev, ["brl_percent", "barrel_batted_rate"])
        hh_col = _find_col(ev, ["hard_hit_percent", "ev95percent"])
        ss_col = _find_col(ev, ["anglesweetspotpercent", "sweet_spot_percent"])
        bbe_col = _find_col(ev, ["attempts", "bbe"])
        for _, row in ev.iterrows():
            pid = row.get(id_col_ev)
            if pd.isna(pid):
                continue
            pid = int(pid)
            d = lookup.setdefault(pid, {})
            if brl_col:
                d["barrel_pct"] = _pct_scale(row.get(brl_col))
            if hh_col:
                d["hard_hit_pct"] = _pct_scale(row.get(hh_col))
            if ss_col:
                d["sweet_spot_pct"] = _pct_scale(row.get(ss_col))
            if bbe_col:
                d["bbe"] = int(_safe_float(row.get(bbe_col)))

    id_col_xs = _find_col(xs, ["player_id"])
    if xs is not None and id_col_xs:
        ba_col = _find_col(xs, ["est_ba", "xba"])
        slg_col = _find_col(xs, ["est_slg", "xslg"])
        woba_col = _find_col(xs, ["est_woba", "xwoba"])
        actual_woba_col = _find_col(xs, ["woba"])
        pa_col = _find_col(xs, ["pa"])
        for _, row in xs.iterrows():
            pid = row.get(id_col_xs)
            if pd.isna(pid):
                continue
            pid = int(pid)
            d = lookup.setdefault(pid, {})
            xba = _safe_float(row.get(ba_col)) if ba_col else None
            xslg = _safe_float(row.get(slg_col)) if slg_col else None
            if xba is not None and xslg is not None:
                d["iso"] = round(max(xslg - xba, 0), 3)
            if woba_col:
                d["xwoba"] = _safe_float(row.get(woba_col))
            if actual_woba_col:
                d["xwobacon"] = _safe_float(row.get(actual_woba_col))
            if pa_col:
                d["pa"] = int(_safe_float(row.get(pa_col)))
    return lookup


def build_pitcher_lookup(savant_data, fangraphs_df):
    """{mlbam_player_id: {...real Savant + FanGraphs pitcher stats...}}"""
    lookup = {}
    if savant_data:
        ev, xs = savant_data.get("exitvelo"), savant_data.get("expected")
        id_col_ev = _find_col(ev, ["player_id"])
        if ev is not None and id_col_ev:
            brl_col = _find_col(ev, ["brl_percent", "barrel_batted_rate"])
            hh_col = _find_col(ev, ["hard_hit_percent", "ev95percent"])
            for _, row in ev.iterrows():
                pid = row.get(id_col_ev)
                if pd.isna(pid):
                    continue
                pid = int(pid)
                d = lookup.setdefault(pid, {})
                if brl_col:
                    d["barrel_bip_pct"] = _pct_scale(row.get(brl_col))
                if hh_col:
                    d["hard_hit_pct"] = _pct_scale(row.get(hh_col))
        id_col_xs = _find_col(xs, ["player_id"])
        if xs is not None and id_col_xs:
            woba_col = _find_col(xs, ["est_woba", "xwoba"])
            for _, row in xs.iterrows():
                pid = row.get(id_col_xs)
                if pd.isna(pid):
                    continue
                pid = int(pid)
                d = lookup.setdefault(pid, {})
                if woba_col:
                    d["xwoba_allowed"] = _safe_float(row.get(woba_col))

    # FanGraphs match-by-name for CSW% / SwStr% (not in the Savant leaderboards above)
    by_name = {}
    if fangraphs_df is not None and not fangraphs_df.empty:
        name_col = _find_col(fangraphs_df, ["Name"])
        csw_col = _find_col(fangraphs_df, ["CSW%"])
        swstr_col = _find_col(fangraphs_df, ["SwStr%"])
        if name_col:
            for _, row in fangraphs_df.iterrows():
                nm = str(row.get(name_col, "")).strip().lower()
                if not nm:
                    continue
                entry = {}
                if csw_col:
                    entry["csw_pct"] = _pct_scale(row.get(csw_col))
                if swstr_col:
                    entry["swstr_pct"] = _pct_scale(row.get(swstr_col))
                by_name[nm] = entry
    return lookup, by_name


# ----------------------------------------------------------------------
# Demo fallback data (used only if live fetching is off or fails)
# ----------------------------------------------------------------------
DEMO_GAME_SLATE = [
    {"away": "CWS", "home": "BAL", "time": "06:35 PM ET", "park": "Oriole Park at Camden Yards", "tag": "Sneaky Value Spot"},
    {"away": "TEX", "home": "CLE", "time": "06:40 PM ET", "park": "Progressive Field", "tag": None},
    {"away": "PHI", "home": "PIT", "time": "06:40 PM ET", "park": "Citizens Bank Park", "tag": None},
    {"away": "DET", "home": "NYY", "time": "07:05 PM ET", "park": "Yankee Stadium", "tag": "Power Alert"},
]
DEMO_PITCHERS = {
    "CWS-BAL": {"home": ("Trey Gibson", "R"), "away": ("Erick Fedde", "R")},
    "TEX-CLE": {"home": ("Marco Alvez", "L"), "away": ("Jonah Pruett", "R")},
    "PHI-PIT": {"home": ("Dylan Souza", "R"), "away": ("Paul Skenes", "R")},
    "DET-NYY": {"home": ("Casey Whitfield", "R"), "away": ("Reese Calder", "L")},
}
DEMO_ROSTERS = {
    "CWS": [("Miguel Vargas", True), ("Andrew Benintendi", False), ("Colson Montgomery", True),
            ("Randal Grichuk", False), ("Sam Antonacci", False), ("Tristan Peters", False)],
    "BAL": [("Pete Alonso", True), ("Gunnar Henderson", True), ("Adley Rutschman", False),
            ("Colton Cowser", False), ("Ryan Mountcastle", False), ("Jordan Westburg", False)],
}


def demo_roster(team_code):
    if team_code in DEMO_ROSTERS:
        return DEMO_ROSTERS[team_code]
    random.seed(hash(team_code) % 5000)
    first = ["Jake", "Marcus", "Diego", "Cole", "Ryan", "Trevor"]
    last = ["Turner", "Reyes", "Hoskins", "Bell", "Cruz", "Nunez"]
    names = random.sample([f"{f} {l}" for f in first for l in last], 6)
    return [(n, i < 2) for i, n in enumerate(names)]


DEMO_BULLPEN = {
    "CWS": [("Steven Ridings", "R"), ("Fraser Ellard", "R"), ("Justin Anderson", "L"), ("Brandon Eisert", "L")],
    "BAL": [("Felix Bautista", "R"), ("Yennier Cano", "R"), ("Keegan Akin", "L"), ("Seranthony Dominguez", "R")],
}


def demo_bullpen(team_code):
    if team_code in DEMO_BULLPEN:
        return [{"id": None, "name": n, "hand": h} for n, h in DEMO_BULLPEN[team_code]]
    random.seed(hash(team_code) % 7000)
    first = ["Trevor", "Kyle", "Nick", "Sam", "Blake", "Chris"]
    last = ["Holt", "Ferris", "Danner", "Wick", "Ozuna", "Reyna"]
    names = random.sample([f"{f} {l}" for f in first for l in last], 4)
    hands = random.choices(["R", "L"], weights=[70, 30], k=4)
    return [{"id": None, "name": n, "hand": h} for n, h in zip(names, hands)]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_pitching_staff(team_id):
    """All pitchers on the active roster (starters + relievers), with throwing hand."""
    try:
        r = requests.get(
            f"{MLB_API_BASE}/teams/{team_id}/roster",
            params={"rosterType": "active", "hydrate": "person(pitchHand)"},
            timeout=15,
        )
        r.raise_for_status()
        pitchers = []
        for entry in r.json().get("roster", []):
            pos = entry.get("position", {}).get("abbreviation", "")
            if pos != "P":
                continue
            person = entry.get("person", {})
            hand = (person.get("pitchHand", {}) or {}).get("code", "R")
            pitchers.append({"id": person.get("id"), "name": person.get("fullName"), "hand": hand})
        return pitchers
    except Exception:
        return None


# ----------------------------------------------------------------------
# Sidebar / data source controls
# ----------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🚚 LOAD THE TRUCK")
    st.link_button("💬 Join the FREE Discord", "https://discord.gg/nCfsd4cxBB", width='stretch')

    st.markdown("---")
    st.markdown("### 📡 Data source")
    use_live = st.checkbox("Use live Baseball Savant / MLB Stats API data", value=True)
    slate_date = st.date_input("Slate date", value=dt.date.today())
    st.caption(
        "Live mode pulls today's real schedule + rosters from the free MLB Stats API, "
        "and real season Statcast leaderboards (barrel%, xwOBA, hard-hit%, etc.) from "
        "Baseball Savant via the free pybaseball library. First load can take 10-30s."
    )

    st.markdown("---")
    with st.expander("📖 How To Use", expanded=False):
        st.markdown("**Hitter stats**")
        howto_hitter_rows = [
            ("Higher CEIL", "good"),
            ("Higher MatchupScore", "good"),
            ("Higher ZONE", "good"),
            ("Higher FORM", "good"),
            ("Higher KHR", "good"),
            ("Higher ISO", "good"),
            ("Higher xwOBA", "good"),
            ("Higher xwOBAC", "good"),
            ("Lower SwStr%", "good"),
            ("Higher PBRL%", "good"),
            ("Higher BRL%", "good"),
            ("Higher SwSp%", "good"),
            ("Higher FB%", "good"),
            ("Higher HR_FB%", "good"),
        ]
        for label, verdict in howto_hitter_rows:
            st.markdown(
                f'<div class="howto-row">{label} = <span class="howto-good">{verdict}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div class="howto-row">Very high HH% = <span class="howto-good">good</span> '
            f'<span class="howto-caveat">*can sometimes be ignored depending on BRL%</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="howto-row">Trend arrows (FORM only): '
            '↑ = trending up/hot &middot; → = flat/stable &middot; ↓ = trending down/cold</div>',
            unsafe_allow_html=True,
        )

        st.markdown("**Pitcher stats (Top Slate Pitchers)**")
        howto_pitcher_rows = [
            ("Higher Pitcher Score", "good"),
            ("Higher Strikeout Score", "good"),
            ("Lower xwOBA allowed", "good"),
            ("Higher CSW%", "good"),
            ("Higher SwStr%", "good"),
            ("Lower Ball%", "good"),
            ("Lower Pulled Barrel%", "good"),
            ("Lower Barrel BIP%", "good"),
            ("Lower Hard Hit%", "good"),
        ]
        for label, verdict in howto_pitcher_rows:
            st.markdown(
                f'<div class="howto-row">{label} = <span class="howto-good">{verdict}</span></div>',
                unsafe_allow_html=True,
            )

        st.markdown("---")
        st.caption(
            "🟢 Real Baseball Savant / FanGraphs data: ISO, xwOBA, xwOBAC, BRL%, "
            "SwSp%, HH%, CEIL, and (for pitchers) xwOBA allowed, Barrel BIP%, "
            "Hard Hit%, CSW%, SwStr%.\n\n"
            "🟡 Estimated/proprietary composites (not published by Savant): MatchupScore, "
            "ZONE, FORM, KHR, PBRL%, FB%, LA, HR_FB%, Pitcher Score, "
            "Strikeout Score, Ball%."
        )

# ----------------------------------------------------------------------
# Build TEAMS (live MLB Stats API, falls back to a hardcoded list)
# ----------------------------------------------------------------------
data_status_notes = []

if use_live:
    live_teams = fetch_all_teams()
    if live_teams:
        TEAMS = live_teams
    else:
        TEAMS = FALLBACK_TEAMS
        data_status_notes.append("Could not reach MLB Stats API for team list -- using built-in team data.")
else:
    TEAMS = FALLBACK_TEAMS

TEAMS_BY_ID = {v["id"]: k for k, v in TEAMS.items()}

# ----------------------------------------------------------------------
# Build GAME_SLATE + pitcher info: live schedule, or demo fallback
# ----------------------------------------------------------------------
GAME_SLATE = []
PITCHERS = {}
live_mode_active = False

if use_live:
    date_str = slate_date.strftime("%Y-%m-%d")
    schedule = fetch_schedule_for_date(date_str)
    # If no games today (off day / all-star break), look ahead up to 5 days
    lookahead = 0
    probe_date = slate_date
    while use_live and (not schedule) and lookahead < 5:
        lookahead += 1
        probe_date = slate_date + dt.timedelta(days=lookahead)
        schedule = fetch_schedule_for_date(probe_date.strftime("%Y-%m-%d"))
    if schedule:
        if lookahead > 0:
            data_status_notes.append(
                f"No games on {slate_date.strftime('%b %-d')} -- showing the next scheduled "
                f"slate ({probe_date.strftime('%b %-d, %Y')})."
            )
        for idx, g in enumerate(schedule):
            away_abbr = g["away_abbr"] or TEAMS_BY_ID.get(g["away_id"])
            home_abbr = g["home_abbr"] or TEAMS_BY_ID.get(g["home_id"])
            if not away_abbr or not home_abbr:
                continue
            # Include the real MLB gamePk so doubleheaders (same two teams, same
            # day) get distinct codes instead of colliding on "AWAY-HOME".
            unique_part = g.get("game_pk") or idx
            code = f"{away_abbr}-{home_abbr}-{unique_part}"

            def _pitcher_tuple(p):
                if not p:
                    return {"id": None, "name": "TBD", "hand": "R"}
                return {
                    "id": p.get("id"),
                    "name": p.get("fullName", "TBD"),
                    "hand": (p.get("pitchHand", {}) or {}).get("code", "R"),
                }

            weather = mock_weather(g["venue"], date_str)
            GAME_SLATE.append(
                {
                    "code": code,
                    "away": away_abbr, "home": home_abbr,
                    "away_id": g["away_id"], "home_id": g["home_id"],
                    "time": format_game_time(g["game_dt_utc"]),
                    "park": g["venue"], "tag": None,
                    "weather": weather,
                }
            )
            PITCHERS[code] = {"home": _pitcher_tuple(g["home_pitcher"]), "away": _pitcher_tuple(g["away_pitcher"])}
        live_mode_active = True
    else:
        data_status_notes.append("Could not reach the live MLB schedule -- showing demo data instead.")

if not GAME_SLATE:
    for idx, g in enumerate(DEMO_GAME_SLATE):
        gg = dict(g)
        gg["away_id"] = TEAMS.get(gg["away"], FALLBACK_TEAMS.get(gg["away"], {})).get("id")
        gg["home_id"] = TEAMS.get(gg["home"], FALLBACK_TEAMS.get(gg["home"], {})).get("id")
        gg["weather"] = mock_weather(gg["park"], str(slate_date))
        demo_lookup_code = f"{gg['away']}-{gg['home']}"
        gg["code"] = f"{demo_lookup_code}-{idx}"
        GAME_SLATE.append(gg)
        if demo_lookup_code in DEMO_PITCHERS:
            PITCHERS[gg["code"]] = {
                side: {"id": None, "name": name, "hand": hand}
                for side, (name, hand) in DEMO_PITCHERS[demo_lookup_code].items()
            }
    live_mode_active = False

default_game = params.get("game", GAME_SLATE[0]["code"])
game_codes = [g["code"] for g in GAME_SLATE]
if default_game not in game_codes:
    default_game = game_codes[0]

# Season for Savant/FanGraphs leaderboards -- matches the slate's year
STATS_YEAR = int(str(slate_date)[:4])

# ----------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------
st.markdown('<div class="brand">🚚 LOAD THE TRUCK</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="accent-bar">'
    '<div class="eyebrow">MLB Home Run Intelligence</div>'
    '<div class="page-title">By-Game Matchup Board</div>'
    '<div class="page-subtitle">Choose a game, compare both lineups, and drill into the best HR reads.</div>'
    '</div>',
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------
# Game Slate
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Game Slate</div>', unsafe_allow_html=True)

if data_status_notes:
    for note in data_status_notes:
        st.info(note)

if len(game_codes) > 1:
    _game_by_code = {g["code"]: g for g in GAME_SLATE}

    def _format_game_option(c):
        gi = _game_by_code.get(c)
        if not gi:
            return c
        return f"{gi['away']} @ {gi['home']} ({gi['time']})" if gi.get("time") else f"{gi['away']} @ {gi['home']}"

    picked = st.selectbox(
        "Choose a game",
        options=game_codes,
        index=game_codes.index(default_game),
        format_func=_format_game_option,
        label_visibility="collapsed",
    )
    if picked != default_game:
        st.query_params["game"] = picked
        st.rerun()

VISIBLE_SLATE_CARDS = 8
visible_games = GAME_SLATE[:VISIBLE_SLATE_CARDS]
slate_cols = st.columns(len(visible_games) + 1)
with slate_cols[0]:
    st.markdown(
        f'<div class="slate-card"><div style="font-weight:800; color:{text};">Slate</div>'
        f'<div class="slate-time">{len(GAME_SLATE)} games</div>'
        f'<div class="slate-park">scroll to choose</div></div>',
        unsafe_allow_html=True,
    )

for i, g in enumerate(visible_games):
    code = g["code"]
    with slate_cols[i + 1]:
        away_logo = team_logo(g["away"], size=20)
        home_logo = team_logo(g["home"], size=20)
        css_class = "slate-card-active" if code == default_game else "slate-card"
        w = g["weather"]
        st.markdown(
            f'<div class="{css_class}">'
            f'<div style="font-weight:800; color:{text};">{away_logo} {g["away"]} @ {home_logo} {g["home"]}</div>'
            f'<div class="slate-time">{g["time"]}</div>'
            f'<div class="slate-park">{g["park"]}</div>'
            f'<div class="slate-time">{w["icon"]} {w["temp"]}°F</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("Select", key=f"select_{code}", width='stretch'):
            st.query_params["game"] = code
            st.rerun()

game = default_game
game_info = next(g for g in GAME_SLATE if g["code"] == game)
away, home = game_info["away"], game_info["home"]
game_pitchers = PITCHERS.get(
    game, {"home": {"id": None, "name": "TBD", "hand": "R"}, "away": {"id": None, "name": "TBD", "hand": "R"}}
)
home_pitcher = game_pitchers["home"]
away_pitcher = game_pitchers["away"]
home_pitcher_name, home_pitcher_hand, home_pitcher_id = home_pitcher["name"], home_pitcher["hand"], home_pitcher["id"]
away_pitcher_name, away_pitcher_hand, away_pitcher_id = away_pitcher["name"], away_pitcher["hand"], away_pitcher["id"]
hand_label = {"R": "RHP", "L": "LHP"}

# ----------------------------------------------------------------------
# Matchup banner
# ----------------------------------------------------------------------
tag_html = f'<span class="tag-pill">🟠 {game_info["tag"]}</span>' if game_info["tag"] else ""
w = game_info["weather"]
weather_html = (
    f'<span class="tag-pill" style="background-color:transparent; color:{subtext}; '
    f'border:1px solid {border}; font-weight:600;">'
    f'{w["icon"]} {w["temp"]}°F · {w["condition"]} · {w["wind"]}</span>'
)

b1, b2, b3 = st.columns([1, 6, 1])
with b1:
    st.markdown(
        f'<div style="text-align:center;">{team_logo(away, size=56)}</div>',
        unsafe_allow_html=True,
    )
with b2:
    meta_extra = f' · {tag_html}' if tag_html else ''
    st.markdown(
        f'<div class="matchup-banner"><div>'
        f'<div class="matchup-title">{away} @ {home}</div>'
        f'<div class="matchup-meta">{game_info["time"]} · {game_info["park"]}{meta_extra}</div>'
        f'<div class="matchup-meta" style="margin-top:6px;">{weather_html}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )
with b3:
    st.markdown(
        f'<div style="text-align:center;">{team_logo(home, size=56)}</div>',
        unsafe_allow_html=True,
    )

# ----------------------------------------------------------------------
# Fetch rosters + Savant/FanGraphs stat lookups for the selected game
# ----------------------------------------------------------------------
stats_notes = []

if live_mode_active:
    away_roster_live = fetch_active_hitters(TEAMS.get(away, {}).get("id"))
    home_roster_live = fetch_active_hitters(TEAMS.get(home, {}).get("id"))
else:
    away_roster_live = None
    home_roster_live = None

if away_roster_live:
    away_roster_raw = away_roster_live
else:
    away_roster_raw = [{"id": None, "name": n} for n, _ in demo_roster(away)]
    if live_mode_active:
        stats_notes.append(f"Could not fetch {away}'s live roster -- showing demo hitters for that team.")

if home_roster_live:
    home_roster_raw = home_roster_live
else:
    home_roster_raw = [{"id": None, "name": n} for n, _ in demo_roster(home)]
    if live_mode_active:
        stats_notes.append(f"Could not fetch {home}'s live roster -- showing demo hitters for that team.")

batter_lookup, pitcher_lookup, pitcher_by_name = {}, {}, {}
if live_mode_active:
    if not PYBASEBALL_OK:
        stats_notes.append("The pybaseball package isn't installed -- run `pip install pybaseball` for real Savant stats.")
    else:
        savant_batters = fetch_savant_batter_stats(STATS_YEAR)
        if not savant_batters:
            stats_notes.append("Could not reach Baseball Savant batter leaderboards -- stats below are estimated.")
        batter_lookup = build_batter_lookup(savant_batters)

        savant_pitchers = fetch_savant_pitcher_stats(STATS_YEAR)
        fangraphs_pitchers = fetch_fangraphs_pitching(STATS_YEAR)
        if not savant_pitchers and fangraphs_pitchers is None:
            stats_notes.append("Could not reach Baseball Savant / FanGraphs pitcher leaderboards -- stats below are estimated.")
        pitcher_lookup, pitcher_by_name = build_pitcher_lookup(savant_pitchers, fangraphs_pitchers)

if stats_notes:
    for note in stats_notes:
        st.warning(note)

# League-average fallbacks used when a specific player has no qualifying Savant sample yet
LEAGUE_AVG_B = {
    "barrel_pct": 8.0, "hard_hit_pct": 39.0, "sweet_spot_pct": 33.0,
    "iso": 0.150, "xwoba": 0.320, "xwobacon": 0.360, "bbe": 120, "pa": 200,
}
LEAGUE_AVG_P = {
    "xwoba_allowed": 0.315, "barrel_bip_pct": 7.0, "hard_hit_pct": 38.0,
    "csw_pct": 28.0, "swstr_pct": 11.0,
}


def batter_stats_for(pid, name):
    """Real Savant stats where available; house-made composites derived from them otherwise."""
    d = batter_lookup.get(pid, {}) if pid else {}
    barrel_pct = d.get("barrel_pct", LEAGUE_AVG_B["barrel_pct"])
    hard_hit_pct = d.get("hard_hit_pct", LEAGUE_AVG_B["hard_hit_pct"])
    sweet_spot_pct = d.get("sweet_spot_pct", LEAGUE_AVG_B["sweet_spot_pct"])
    iso = d.get("iso", LEAGUE_AVG_B["iso"])
    xwoba = d.get("xwoba", LEAGUE_AVG_B["xwoba"])
    xwobacon = d.get("xwobacon", LEAGUE_AVG_B["xwobacon"])
    bbe = d.get("bbe", LEAGUE_AVG_B["bbe"])
    pa = d.get("pa", LEAGUE_AVG_B["pa"])
    has_real = bool(d)

    # CEIL: 0-100 score (not the old 20-80 scouting scale). MLB-average barrel
    # rate (~8%) lands near 50; this is used for the "CEIL" column and as an
    # input to other composites below.
    barrel_score = round(max(0.0, min(100.0, (barrel_pct / 16) * 100)), 3)

    rnd = random.Random((pid or abs(hash(name))) % 1_000_000)
    matchup_score = round(min(max(35 + (xwoba - 0.280) * 250 + rnd.uniform(-6, 6), 20), 85), 1)
    zonefit = round(max(0.03, min(0.14, 0.06 + (barrel_pct / 100) + rnd.uniform(-0.01, 0.01))), 3)
    hr_form_pct = int(min(max((barrel_score / 100) * 70 + rnd.randint(-8, 12), 5), 95))
    khr = round(max(0.0, (barrel_score / 100) * 22 + iso * 40 + rnd.uniform(-2, 2)), 1)
    khr_score = round(max(0.0, min(100.0, (khr / 45) * 100)), 3)
    pulled_brl_pct = round(barrel_pct * 0.62, 1)
    fb_pct = round(max(15, min(50, 33 + (barrel_pct - 8) * 0.8 + rnd.uniform(-4, 4))), 1)
    la = round(max(2, min(22, 11 + (fb_pct - 33) * 0.2 + rnd.uniform(-2, 2))), 1)
    hr_fb_pct = round(max(2, min(35, (barrel_pct * 1.3) + rnd.uniform(-3, 3))), 1)
    swstr_pct = round(max(4, min(20, 11 + (barrel_pct - 8) * 0.15 + rnd.uniform(-2, 2))), 1)

    return {
        "barrel_score": barrel_score, "matchup_score": matchup_score, "zonefit": zonefit,
        "hr_form_pct": hr_form_pct, "khr": khr, "khr_score": khr_score,
        "iso": iso, "xwoba": xwoba, "xwobacon": xwobacon,
        "swstr_pct": swstr_pct, "pulled_brl_pct": pulled_brl_pct, "brl_bip_pct": barrel_pct,
        "sweet_spot_pct": sweet_spot_pct, "fb_pct": fb_pct, "hh_pct": hard_hit_pct, "la": la,
        "hr_fb_pct": hr_fb_pct, "bbe": bbe, "pa": pa, "has_real": has_real,
    }


def pitcher_stats_for(pid, name):
    d = dict(pitcher_lookup.get(pid, {})) if pid else {}
    extra = pitcher_by_name.get((name or "").strip().lower(), {})
    for k, v in extra.items():
        d.setdefault(k, v)
    xwoba_allowed = d.get("xwoba_allowed", LEAGUE_AVG_P["xwoba_allowed"])
    barrel_bip_pct = d.get("barrel_bip_pct", LEAGUE_AVG_P["barrel_bip_pct"])
    hard_hit_pct = d.get("hard_hit_pct", LEAGUE_AVG_P["hard_hit_pct"])
    csw_pct = d.get("csw_pct", LEAGUE_AVG_P["csw_pct"])
    swstr_pct = d.get("swstr_pct", LEAGUE_AVG_P["swstr_pct"])
    has_real = bool(d)

    rnd = random.Random((pid or abs(hash(name))) % 1_000_000)
    pitcher_score = round(
        min(max(35 + (0.320 - xwoba_allowed) * 300 + (csw_pct - 27) * 0.8 + rnd.uniform(-3, 3), 20), 75), 3
    )
    k_score = round(min(max(30 + (swstr_pct - 10) * 2.2 + rnd.uniform(-3, 3), 20), 70), 3)
    ball_pct = round(max(28, min(42, 36 - (csw_pct - 27) * 0.3 + rnd.uniform(-2, 2))), 1)
    pulled_brl_pct = round(max(1, min(7, barrel_bip_pct * 0.5 + rnd.uniform(-1, 1))), 1)
    fb_pct = round(max(15, min(45, 30 + rnd.uniform(-6, 6))), 1)

    return {
        "pitcher_score": pitcher_score, "k_score": k_score, "xwoba_allowed": xwoba_allowed,
        "csw_pct": csw_pct, "swstr_pct": swstr_pct, "ball_pct": ball_pct,
        "pulled_brl_pct": pulled_brl_pct, "barrel_bip_pct": barrel_bip_pct,
        "fb_pct": fb_pct, "hard_hit_pct": hard_hit_pct, "has_real": has_real,
    }


def prop_line_for(proj):
    """Round a projection to a sportsbook-style half-line and note the model's lean."""
    base = round(proj * 2) / 2
    if abs(base - proj) < 1e-9:
        base -= 0.5  # nudge off an exact number so it isn't a push
    lean = "↑" if proj > base else "↓"
    return base, lean


def pitcher_props(pid, name, is_bullpen=False):
    """Model-projected prop lines for this game, derived from real per-9 rate estimates.
    NOT sportsbook odds -- there's no free, keyless odds API, so these are our own
    projections rounded to a sportsbook-style half-line with a directional lean."""
    s = pitcher_stats_for(pid, name)
    ip = 1.0 if is_bullpen else 5.3

    k_per9 = max(4.0, min(14.0, s["swstr_pct"] * 0.78))
    bb_per9 = max(1.2, min(6.0, 3.1 + (s["ball_pct"] - 36) * 0.18))
    era_est = max(1.80, min(7.50, 2.6 + (s["xwoba_allowed"] - 0.300) * 22))
    hits_per9 = max(4.5, min(12.5, 7.6 + (s["xwoba_allowed"] - 0.300) * 26))

    proj_k = round(k_per9 / 9 * ip, 1)
    proj_bb = round(bb_per9 / 9 * ip, 1)
    proj_er = round(era_est / 9 * ip, 1)
    proj_h = round(hits_per9 / 9 * ip, 1)
    proj_outs = round(ip * 3)

    k_line, k_lean = prop_line_for(proj_k)
    bb_line, bb_lean = prop_line_for(proj_bb)
    er_line, er_lean = prop_line_for(proj_er)
    h_line, h_lean = prop_line_for(proj_h)

    return {
        "ip": ip,
        "proj_k": proj_k, "k_line": f"{k_line}{k_lean}",
        "proj_bb": proj_bb, "bb_line": f"{bb_line}{bb_lean}",
        "proj_er": proj_er, "er_line": f"{er_line}{er_lean}",
        "proj_h": proj_h, "h_line": f"{h_line}{h_lean}",
        "proj_outs": proj_outs,
    }


def moneyline_from_prob(p):
    """Fair (no-vig) American moneyline odds from a win probability."""
    p = max(0.02, min(0.98, p))
    if p >= 0.5:
        return f"-{round(100 * p / (1 - p))}"
    return f"+{round(100 * (1 - p) / p)}"


def team_offense_xwoba(roster_raw):
    vals = [batter_stats_for(p["id"], p["name"])["xwoba"] for p in roster_raw]
    return sum(vals) / len(vals) if vals else LEAGUE_AVG_B["xwoba"]


def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ----------------------------------------------------------------------
# Model fallback numbers (used whenever ESPN doesn't have a real line
# for this game/date -- e.g. games far in the future, or ESPN's odds
# feed briefly unavailable)
# ----------------------------------------------------------------------
away_off_xwoba = team_offense_xwoba(away_roster_raw)
home_off_xwoba = team_offense_xwoba(home_roster_raw)
home_pitch_xwoba = pitcher_stats_for(home_pitcher_id, home_pitcher_name)["xwoba_allowed"]
away_pitch_xwoba = pitcher_stats_for(away_pitcher_id, away_pitcher_name)["xwoba_allowed"]

home_edge = (home_off_xwoba - away_pitch_xwoba) - (away_off_xwoba - home_pitch_xwoba) + 0.010
model_home_win_prob = 1 / (1 + math.exp(-home_edge * 40))
model_away_win_prob = 1 - model_home_win_prob


def est_team_runs(off_xwoba, opp_pitch_xwoba):
    factor = ((off_xwoba + opp_pitch_xwoba) / 2) / 0.320
    return max(1.5, min(9.0, 4.3 * factor))


model_away_runs = est_team_runs(away_off_xwoba, home_pitch_xwoba)
model_home_runs = est_team_runs(home_off_xwoba, away_pitch_xwoba)
model_total_runs = round(model_away_runs + model_home_runs, 1)

RUN_STD_DEV = 4.3  # typical MLB game run-differential std dev, for the run-line model
model_home_margin = model_home_runs - model_away_runs
if model_home_win_prob >= 0.5:
    model_fav, model_fav_line = home, -1.5
    model_dog, model_dog_line = away, 1.5
    model_fav_cover_prob = 1 - normal_cdf((1.5 - model_home_margin) / RUN_STD_DEV)
else:
    model_fav, model_fav_line = away, -1.5
    model_dog, model_dog_line = home, 1.5
    model_fav_cover_prob = 1 - normal_cdf((1.5 + model_home_margin) / RUN_STD_DEV)
model_dog_cover_prob = 1 - model_fav_cover_prob

# ----------------------------------------------------------------------
# Try real sportsbook odds via ESPN first (free, keyless, DraftKings/consensus)
# ----------------------------------------------------------------------
real_odds = None
if live_mode_active:
    espn_date = slate_date.strftime("%Y%m%d")
    espn_scoreboard = fetch_espn_scoreboard(espn_date)
    espn_event = find_espn_event(espn_scoreboard, away, home)
    real_odds = extract_real_odds(espn_event)

using_real_odds = real_odds is not None and (
    real_odds.get("home_moneyline") is not None or real_odds.get("total") is not None
)

# ----------------------------------------------------------------------
# Moneyline
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Moneyline</div>', unsafe_allow_html=True)

if using_real_odds and real_odds.get("home_moneyline") is not None:
    st.markdown(
        f'<div class="page-subtitle" style="margin-bottom:14px;">'
        f'🟢 Real sportsbook odds via ESPN ({real_odds["provider"]}).</div>',
        unsafe_allow_html=True,
    )
    away_ml_display = american_odds_str(real_odds["away_moneyline"]) if real_odds.get("away_moneyline") is not None else "--"
    home_ml_display = american_odds_str(real_odds["home_moneyline"])
    away_meta = "Live sportsbook line"
    home_meta = "Live sportsbook line"
else:
    st.markdown(
        '<div class="page-subtitle" style="margin-bottom:14px;">'
        '🟡 No live line found for this game -- showing a model estimate instead '
        '(real xwOBA-based win probability, converted to fair odds).</div>',
        unsafe_allow_html=True,
    )
    away_ml_display = moneyline_from_prob(model_away_win_prob)
    home_ml_display = moneyline_from_prob(model_home_win_prob)
    away_meta = f"Model win probability: {model_away_win_prob * 100:.1f}%"
    home_meta = f"Model win probability: {model_home_win_prob * 100:.1f}%"

ml_col1, ml_col2 = st.columns(2)
with ml_col1:
    st.markdown(
        f'<div class="read-card">'
        f'<div class="read-score">{away_ml_display}</div>'
        f'<div class="read-name">{team_logo(away, size=18)} {away}</div>'
        f'<div class="read-meta">{away_meta}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
with ml_col2:
    st.markdown(
        f'<div class="read-card">'
        f'<div class="read-score">{home_ml_display}</div>'
        f'<div class="read-name">{team_logo(home, size=18)} {home}</div>'
        f'<div class="read-meta">{home_meta}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
if not using_real_odds:
    st.caption(
        "Model estimate only: compares each lineup's real average xwOBA against the opposing starter's "
        "real xwOBA allowed, with a small home-field adjustment. No bullpen, bench, or in-game context factored in."
    )

# ----------------------------------------------------------------------
# Total Runs (Over/Under)
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Total Runs</div>', unsafe_allow_html=True)

if using_real_odds and real_odds.get("total") is not None:
    st.markdown(
        f'<div class="page-subtitle" style="margin-bottom:14px;">'
        f'🟢 Real sportsbook total via ESPN ({real_odds["provider"]}).</div>',
        unsafe_allow_html=True,
    )
    total_line = real_odds["total"]
    over_display = american_odds_str(real_odds["over_price"]) if real_odds.get("over_price") is not None else "-110"
    under_display = american_odds_str(real_odds["under_price"]) if real_odds.get("under_price") is not None else "-110"
    total_meta = "Live sportsbook line"
else:
    st.markdown(
        '<div class="page-subtitle" style="margin-bottom:14px;">'
        '🟡 No live total found -- showing a model estimate instead '
        '(projected combined runs from each lineup\'s real offense vs. the opposing starter).</div>',
        unsafe_allow_html=True,
    )
    total_line = round(model_total_runs * 2) / 2
    total_meta = f"Model projected total: {model_total_runs}"
    over_display, under_display = "--", "--"

tr_col1, tr_col2 = st.columns(2)
with tr_col1:
    st.markdown(
        f'<div class="read-card"><div class="read-score">O {total_line}</div>'
        f'<div class="read-name">Over</div><div class="read-meta">{over_display} &middot; {total_meta}</div></div>',
        unsafe_allow_html=True,
    )
with tr_col2:
    st.markdown(
        f'<div class="read-card"><div class="read-score">U {total_line}</div>'
        f'<div class="read-name">Under</div><div class="read-meta">{under_display} &middot; {total_meta}</div></div>',
        unsafe_allow_html=True,
    )

# ----------------------------------------------------------------------
# Spread (Run Line)
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Spread</div>', unsafe_allow_html=True)

if using_real_odds and real_odds.get("home_spread") is not None:
    st.markdown(
        f'<div class="page-subtitle" style="margin-bottom:14px;">'
        f'🟢 Real sportsbook spread via ESPN ({real_odds["provider"]}).</div>',
        unsafe_allow_html=True,
    )
    home_spread_val = real_odds["home_spread"]
    away_spread_val = real_odds["away_spread"]
    home_spread_display = f"{home_spread_val:+g}"
    away_spread_display = f"{away_spread_val:+g}"
    spread_meta = "Live sportsbook line"
else:
    st.markdown(
        '<div class="page-subtitle" style="margin-bottom:14px;">'
        '🟡 No live spread found -- showing a model run line instead '
        '(standard \u00b11.5, priced from a normal approximation of projected run differential).</div>',
        unsafe_allow_html=True,
    )
    home_spread_display = f"{model_fav_line:+g}" if model_fav == home else f"{model_dog_line:+g}"
    away_spread_display = f"{model_fav_line:+g}" if model_fav == away else f"{model_dog_line:+g}"
    spread_meta = None

sp_col1, sp_col2 = st.columns(2)
has_real_spread = using_real_odds and real_odds.get("home_spread") is not None
with sp_col1:
    if has_real_spread:
        away_price = american_odds_str(real_odds.get("away_spread_price", -110))
        away_sp_meta = spread_meta
    else:
        away_price = moneyline_from_prob(model_fav_cover_prob if model_fav == away else model_dog_cover_prob)
        away_sp_meta = f"Model cover probability: {(model_fav_cover_prob if model_fav == away else model_dog_cover_prob) * 100:.1f}%"
    st.markdown(
        f'<div class="read-card"><div class="read-score">{away_spread_display}</div>'
        f'<div class="read-name">{team_logo(away, size=18)} {away}</div>'
        f'<div class="read-meta">{away_price} &middot; {away_sp_meta}</div></div>',
        unsafe_allow_html=True,
    )
with sp_col2:
    if has_real_spread:
        home_price = american_odds_str(real_odds.get("home_spread_price", -110))
        home_sp_meta = spread_meta
    else:
        home_price = moneyline_from_prob(model_fav_cover_prob if model_fav == home else model_dog_cover_prob)
        home_sp_meta = f"Model cover probability: {(model_fav_cover_prob if model_fav == home else model_dog_cover_prob) * 100:.1f}%"
    st.markdown(
        f'<div class="read-card"><div class="read-score">{home_spread_display}</div>'
        f'<div class="read-name">{team_logo(home, size=18)} {home}</div>'
        f'<div class="read-meta">{home_price} &middot; {home_sp_meta}</div></div>',
        unsafe_allow_html=True,
    )
st.caption(
    "Moneyline, Total Runs, and Spread try real sportsbook lines first (via ESPN's public odds feed, "
    "sourced from DraftKings/consensus books) and only fall back to a model estimate when no live line "
    "is posted yet for this game. Pitcher Props and Batter Props below are always model projections -- "
    "there's no free, keyless source for individual player prop odds."
)

# ----------------------------------------------------------------------
# Pitcher Props (model projections, not sportsbook odds)
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Pitcher Props</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="page-subtitle" style="margin-bottom:4px;">'
    'Model-projected lines for tonight\'s starters, derived from real Statcast/FanGraphs rates '
    '-- not actual sportsbook odds.</div>',
    unsafe_allow_html=True,
)

props_rows = []
for team_code, p_info in ((away, away_pitcher), (home, home_pitcher)):
    if p_info["name"] == "TBD":
        continue
    pr = pitcher_props(p_info["id"], p_info["name"], is_bullpen=False)
    props_rows.append(
        {
            "Logo": f"https://www.mlbstatic.com/team-logos/{TEAMS.get(team_code, {}).get('id', '')}.svg",
            "Pitcher": p_info["name"],
            "Throws": hand_label.get(p_info["hand"], p_info["hand"]),
            "Proj IP": pr["ip"],
            "Proj K": pr["proj_k"], "K Line": pr["k_line"],
            "Proj BB": pr["proj_bb"], "BB Line": pr["bb_line"],
            "Proj ER": pr["proj_er"], "ER Line": pr["er_line"],
            "Proj H": pr["proj_h"], "H Line": pr["h_line"],
            "Proj Outs": pr["proj_outs"],
        }
    )

if props_rows:
    df_props = pd.DataFrame(props_rows)
    st.dataframe(
        df_props, width='stretch', height=110, hide_index=True,
        column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")},
    )
    st.caption(
        "↑ = model projects the Over on that line, ↓ = model projects the Under. "
        "Proj IP assumes a standard ~5.1-inning start; adjust mentally for known short leashes or bullpen games."
    )
else:
    st.caption("No probable starters posted yet for this game.")

# ----------------------------------------------------------------------
# Top Reads In This Game
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Top Reads In This Game</div>', unsafe_allow_html=True)


def top_hitters(roster_raw, n=2):
    scored = [(p, batter_stats_for(p["id"], p["name"])) for p in roster_raw]
    scored.sort(key=lambda t: t[1]["xwoba"], reverse=True)
    return scored[:n]


pool = (
    [(p, s, home, away) for p, s in top_hitters(home_roster_raw, 2)]
    + [(p, s, away, home) for p, s in top_hitters(away_roster_raw, 2)]
)

read_cols = st.columns(4)
for col, (player, stats, team, opp) in zip(read_cols, pool):
    with col:
        star = stats["has_real"] and (stats["brl_bip_pct"] >= 12 or stats["xwoba"] >= 0.360)
        badge = '<div class="barrel-badge">★ Barrel Signal</div>' if star else ""
        logo = team_logo(team, size=18)
        st.markdown(
            f'<div class="read-card"><div class="read-score">{stats["barrel_score"]}</div>'
            f'<div class="read-name">{logo} {player["name"]}</div>{badge}'
            f'<div class="read-meta">{team} vs {opp}</div></div>',
            unsafe_allow_html=True,
        )
        m1, m2, m3 = st.columns(3)
        for c, label, val in zip(
            [m1, m2, m3], ["MATCHUP", "ZONEFIT", "HR FORM"],
            [stats["matchup_score"], stats["zonefit"], f'{stats["hr_form_pct"]}%'],
        ):
            with c:
                st.markdown(
                    f'<div class="metric-card"><div class="metric-label">{label}</div>'
                    f'<div class="metric-value">{val}</div></div>',
                    unsafe_allow_html=True,
                )
        m4, m5, m6 = st.columns(3)
        for c, label, val in zip(
            [m4, m5, m6], ["PULLEDBRL", "BRL/BIP", "ISO"],
            [f'{stats["pulled_brl_pct"]}%', f'{stats["brl_bip_pct"]}%', stats["iso"]],
        ):
            with c:
                st.markdown(
                    f'<div class="metric-card"><div class="metric-label">{label}</div>'
                    f'<div class="metric-value">{val}</div></div>',
                    unsafe_allow_html=True,
                )

# ----------------------------------------------------------------------
# Batter Props (model projections, not sportsbook odds)
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Batter Props</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="page-subtitle" style="margin-bottom:4px;">'
    'Model-projected lines for tonight\'s top reads, derived from real Statcast rates '
    '-- not actual sportsbook odds.</div>',
    unsafe_allow_html=True,
)


def batter_props(stats):
    """Model-projected Hits / Total Bases / Strikeouts lines, plus an Anytime HR%
    (Poisson estimate from the season-pace kHR figure). NOT sportsbook odds."""
    pa = 4.3  # typical PA per game
    ab = pa * 0.87  # roughly excludes BB/HBP/SF

    avg_est = max(0.180, min(0.340, 0.230 + (stats["xwoba"] - 0.320) * 0.35))
    slg_est = avg_est + stats["iso"]
    k_rate_pct = max(12.0, min(38.0, stats["swstr_pct"] * 2.0))

    proj_hits = round(avg_est * ab, 1)
    proj_tb = round(slg_est * ab, 1)
    proj_k = round(k_rate_pct / 100 * pa, 1)

    hr_per_game = stats["khr"] / 162
    anytime_hr_pct = round((1 - math.exp(-hr_per_game)) * 100, 1)

    h_line, h_lean = prop_line_for(proj_hits)
    tb_line, tb_lean = prop_line_for(proj_tb)
    k_line, k_lean = prop_line_for(proj_k)

    return {
        "proj_hits": proj_hits, "h_line": f"{h_line}{h_lean}",
        "proj_tb": proj_tb, "tb_line": f"{tb_line}{tb_lean}",
        "proj_k": proj_k, "k_line": f"{k_line}{k_lean}",
        "anytime_hr_pct": anytime_hr_pct,
    }


batter_props_rows = []
for player, stats, team, opp in pool:
    bp = batter_props(stats)
    batter_props_rows.append(
        {
            "Logo": f"https://www.mlbstatic.com/team-logos/{TEAMS.get(team, {}).get('id', '')}.svg",
            "Player": player["name"],
            "Opp": opp,
            "Hits Line": bp["h_line"],
            "Total Bases Line": bp["tb_line"],
            "Strikeouts Line": bp["k_line"],
            "Anytime HR %": bp["anytime_hr_pct"],
        }
    )

if batter_props_rows:
    df_batter_props = pd.DataFrame(batter_props_rows)
    st.dataframe(
        df_batter_props, width='stretch', height=180, hide_index=True,
        column_config={
            "Logo": st.column_config.ImageColumn("Logo", width="small"),
            "Anytime HR %": st.column_config.NumberColumn("Anytime HR %", format="%.1f%%"),
        },
    )
    st.caption(
        "↑ = model projects the Over on that line, ↓ = model projects the Under. "
        "Anytime HR % is a Poisson estimate from each player's projected season home run pace, not a sportsbook price."
    )
else:
    st.caption("No batters available to project yet.")

# ----------------------------------------------------------------------
# Lineup Boards
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Lineup Boards</div>', unsafe_allow_html=True)


def build_lineup_df(roster_raw, team_code):
    rows = []
    for p in roster_raw:
        s = batter_stats_for(p["id"], p["name"])
        star = s["has_real"] and (s["brl_bip_pct"] >= 12 or s["xwoba"] >= 0.360)
        rows.append(
            {
                "Player": f"★ {p['name']}" if star else p["name"],
                "Logo": f"https://www.mlbstatic.com/team-logos/{TEAMS.get(team_code, {}).get('id', '')}.svg",
                "CEIL": s["barrel_score"],
                "MatchupScore": s["matchup_score"],
                "ZONE": s["zonefit"],
                "FORM": f'{s["hr_form_pct"]}% {"↑" if s["hr_form_pct"] >= 55 else ("→" if s["hr_form_pct"] >= 40 else "↓")}',
                "KHR": s["khr_score"],
                "PIT": int(round(s["pa"] * 3.9)),
                "BIP": s["bbe"],
                "ISO": s["iso"],
                "xwOBA": s["xwoba"],
                "xwOBAC": s["xwobacon"],
                "SwStr%": s["swstr_pct"],
                "PBRL%": s["pulled_brl_pct"],
                "BRL%": s["brl_bip_pct"],
                "SwSp%": s["sweet_spot_pct"],
                "FB%": s["fb_pct"],
                "HH%": s["hh_pct"],
                "LA": s["la"],
                "HR_FB%": s["hr_fb_pct"],
            }
        )
    return pd.DataFrame(rows)


numeric_cols = [
    "CEIL", "MatchupScore", "ZONE", "ISO", "xwOBA", "xwOBAC",
    "SwStr%", "PBRL%", "BRL%", "SwSp%", "FB%", "HH%", "LA", "HR_FB%", "KHR",
]

fmt = {
    "CEIL": "{:.3f}", "MatchupScore": "{:.1f}",
    "ZONE": "{:.3f}", "ISO": "{:.3f}", "xwOBA": "{:.3f}", "xwOBAC": "{:.3f}",
    "SwStr%": "{:.1f}%", "PBRL%": "{:.1f}%", "BRL%": "{:.1f}%", "SwSp%": "{:.1f}%",
    "FB%": "{:.1f}%", "HH%": "{:.1f}%", "LA": "{:.1f}", "HR_FB%": "{:.1f}", "KHR": "{:.3f}",
    "PIT": "{:,}", "BIP": "{:,}",
}

logo_col_config = {"Logo": st.column_config.ImageColumn("Logo", width="small")}

# Away hitters vs the home team's starter
st.markdown(
    f'<div class="section-subtitle">{away} hitters vs {home_pitcher_name} '
    f'({hand_label.get(home_pitcher_hand, home_pitcher_hand)})</div>',
    unsafe_allow_html=True,
)
df_away = build_lineup_df(away_roster_raw, away)
styled_away = df_away.style.background_gradient(subset=numeric_cols, cmap="RdYlGn", axis=0).format(fmt)
st.dataframe(
    styled_away, width='stretch', height=430, hide_index=True,
    column_config=logo_col_config,
)

# Home hitters vs the away team's starter
st.markdown(
    f'<div class="section-subtitle">{home} hitters vs {away_pitcher_name} '
    f'({hand_label.get(away_pitcher_hand, away_pitcher_hand)})</div>',
    unsafe_allow_html=True,
)
df_home = build_lineup_df(home_roster_raw, home)
styled_home = df_home.style.background_gradient(subset=numeric_cols, cmap="RdYlGn", axis=0).format(fmt)
st.dataframe(
    styled_home, width='stretch', height=430, hide_index=True,
    column_config=logo_col_config,
)

# ----------------------------------------------------------------------
# Bullpen Report
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Bullpen Report</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="page-subtitle" style="margin-bottom:14px;">Relief arms available for {away} and {home}</div>',
    unsafe_allow_html=True,
)

pitcher_table_cols = [
    "Pitcher Score", "Strikeout Score", "xwOBA", "CSW %", "SwStr %",
    "Ball %", "Barrel BIP %", "Hard Hit %",
]
pitcher_table_fmt = {
    "Pitcher Score": "{:.3f}", "Strikeout Score": "{:.3f}", "xwOBA": "{:.3f}",
    "CSW %": "{:.1f}", "SwStr %": "{:.1f}", "Ball %": "{:.1f}",
    "Barrel BIP %": "{:.1f}", "Hard Hit %": "{:.1f}",
}


def build_bullpen_df(team_code, team_id, exclude_pid):
    if live_mode_active:
        staff = fetch_pitching_staff(team_id)
    else:
        staff = None
    if not staff:
        staff = demo_bullpen(team_code)
        if live_mode_active:
            stats_notes.append(f"Could not fetch {team_code}'s live pitching staff -- showing demo relievers.")

    rows = []
    for p in staff:
        if exclude_pid and p["id"] == exclude_pid:
            continue  # already shown above as the probable starter
        s = pitcher_stats_for(p["id"], p["name"])
        rows.append(
            {
                "Logo": f"https://www.mlbstatic.com/team-logos/{TEAMS.get(team_code, {}).get('id', '')}.svg",
                "Pitcher Name": p["name"],
                "P Throws": p.get("hand", "R"),
                "Pitcher Score": s["pitcher_score"],
                "Strikeout Score": s["k_score"],
                "xwOBA": s["xwoba_allowed"],
                "CSW %": s["csw_pct"],
                "SwStr %": s["swstr_pct"],
                "Ball %": s["ball_pct"],
                "Barrel BIP %": s["barrel_bip_pct"],
                "Hard Hit %": s["hard_hit_pct"],
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("Pitcher Score", ascending=False).reset_index(drop=True)


bp_col1, bp_col2 = st.columns(2)
with bp_col1:
    st.markdown(f'<div class="section-subtitle">{away} bullpen</div>', unsafe_allow_html=True)
    df_bp_away = build_bullpen_df(away, TEAMS.get(away, {}).get("id"), away_pitcher_id)
    if not df_bp_away.empty:
        styled_bp_away = (
            df_bp_away.style
            .background_gradient(subset=pitcher_table_cols, cmap="RdYlGn", axis=0)
            .format(pitcher_table_fmt)
        )
        st.dataframe(
            styled_bp_away, width='stretch', height=300, hide_index=True,
            column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")},
        )
    else:
        st.caption("No bullpen data available.")

with bp_col2:
    st.markdown(f'<div class="section-subtitle">{home} bullpen</div>', unsafe_allow_html=True)
    df_bp_home = build_bullpen_df(home, TEAMS.get(home, {}).get("id"), home_pitcher_id)
    if not df_bp_home.empty:
        styled_bp_home = (
            df_bp_home.style
            .background_gradient(subset=pitcher_table_cols, cmap="RdYlGn", axis=0)
            .format(pitcher_table_fmt)
        )
        st.dataframe(
            styled_bp_home, width='stretch', height=300, hide_index=True,
            column_config={"Logo": st.column_config.ImageColumn("Logo", width="small")},
        )
    else:
        st.caption("No bullpen data available.")

if any("pitching staff" in n for n in stats_notes):
    for note in stats_notes:
        if "pitching staff" in note:
            st.warning(note)

# ----------------------------------------------------------------------
# Top Slate Pitchers
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Top Slate Pitchers</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="page-subtitle" style="margin-bottom:14px;">Top projected arms across the slate</div>',
    unsafe_allow_html=True,
)

pitcher_rows = []
for g in GAME_SLATE:
    code = g["code"]
    matchup_pitchers = PITCHERS.get(code)
    if not matchup_pitchers:
        continue
    for side, opp_side in (("home", "away"), ("away", "home")):
        p = matchup_pitchers[side]
        if p["name"] == "TBD":
            continue
        s = pitcher_stats_for(p["id"], p["name"])
        pitching_team = g[side]
        opposing_team = g[opp_side]
        pitcher_rows.append(
            {
                "Team": f"https://www.mlbstatic.com/team-logos/{TEAMS.get(pitching_team, {}).get('id', '')}.svg",
                "Pitcher Name": p["name"],
                "P Throws": p["hand"],
                "Pitcher Score": s["pitcher_score"],
                "Strikeout Score": s["k_score"],
                "xwOBA": s["xwoba_allowed"],
                "CSW %": s["csw_pct"],
                "SwStr %": s["swstr_pct"],
                "Ball %": s["ball_pct"],
                "Pulled Barrel %": s["pulled_brl_pct"],
                "Barrel BIP %": s["barrel_bip_pct"],
                "FB %": s["fb_pct"],
                "Hard Hit %": s["hard_hit_pct"],
                "Oppo": opposing_team,
            }
        )

if pitcher_rows:
    df_pitchers = pd.DataFrame(pitcher_rows).sort_values("Pitcher Score", ascending=False).reset_index(drop=True)

    pitcher_numeric_cols = [
        "Pitcher Score", "Strikeout Score", "xwOBA", "CSW %", "SwStr %",
        "Ball %", "Pulled Barrel %", "Barrel BIP %", "FB %", "Hard Hit %",
    ]
    pitcher_fmt = {
        "Pitcher Score": "{:.3f}", "Strikeout Score": "{:.3f}", "xwOBA": "{:.3f}",
        "CSW %": "{:.1f}", "SwStr %": "{:.1f}", "Ball %": "{:.1f}",
        "Pulled Barrel %": "{:.1f}", "Barrel BIP %": "{:.1f}", "FB %": "{:.1f}", "Hard Hit %": "{:.1f}",
    }

    styled_pitchers = (
        df_pitchers.style
        .background_gradient(subset=pitcher_numeric_cols, cmap="RdYlGn", axis=0)
        .format(pitcher_fmt)
    )
    st.dataframe(
        styled_pitchers, width='stretch', height=460, hide_index=True,
        column_config={"Team": st.column_config.ImageColumn("Team", width="small")},
    )
else:
    st.caption("No probable pitchers are posted for this slate yet.")

# ----------------------------------------------------------------------
# Top Slate Hitters
# ----------------------------------------------------------------------
st.markdown('<div class="section-title">Top Slate Hitters</div>', unsafe_allow_html=True)

hitters_header_col1, hitters_header_col2 = st.columns([3, 1])
with hitters_header_col1:
    st.markdown(
        '<div class="page-subtitle" style="margin-top:6px;">Best bats across the full slate</div>',
        unsafe_allow_html=True,
    )
with hitters_header_col2:
    hitters_sort_label = st.segmented_control(
        "Sort by", ["Matchup", "kHR"], default="Matchup", label_visibility="collapsed",
    ) or "Matchup"


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_team_roster_live(team_id):
    return fetch_active_hitters(team_id)


def get_team_roster(team_code):
    """Same live-with-demo-fallback pattern used for the selected game, generalized to any team."""
    live_roster = _cached_team_roster_live(TEAMS.get(team_code, {}).get("id")) if live_mode_active else None
    if live_roster:
        return live_roster
    return [{"id": None, "name": n} for n, _ in demo_roster(team_code)]


slate_hitter_rows = []
for g in GAME_SLATE:
    game_label = f"{g['away']} @ {g['home']}"
    for team_code, opp_code in ((g["away"], g["home"]), (g["home"], g["away"])):
        roster = get_team_roster(team_code)
        for p in roster:
            s = batter_stats_for(p["id"], p["name"])
            likely_score = round(
                0.5 * s["matchup_score"] + 0.3 * s["barrel_score"] * 0.45 + 0.2 * s["khr_score"] * 0.45, 1
            )
            slate_hitter_rows.append(
                {
                    "Hitter Name": p["name"],
                    "Matchup": s["matchup_score"],
                    "Ceiling": s["barrel_score"],
                    "Zone Fit": s["zonefit"],
                    "kHR": s["khr_score"],
                    "HR Form": f'{s["hr_form_pct"]}% {"↑" if s["hr_form_pct"] >= 55 else ("→" if s["hr_form_pct"] >= 40 else "↓")}',
                    "PIT": int(round(s["pa"] * 3.9)),
                    "BIP": s["bbe"],
                    "ISO": s["iso"],
                    "xWOBA": s["xwoba"],
                    "xWOBAC": s["xwobacon"],
                    "SwStr%": s["swstr_pct"],
                    "PullBRL%": s["pulled_brl_pct"],
                    "Brl/BIP%": s["brl_bip_pct"],
                    "FB%": s["fb_pct"],
                    "HH%": s["hh_pct"],
                    "LA": s["la"],
                    "Likely": likely_score,
                    "Game": game_label,
                    "Oppo": f"https://www.mlbstatic.com/team-logos/{TEAMS.get(opp_code, {}).get('id', '')}.svg",
                }
            )

if slate_hitter_rows:
    sort_col = "Matchup" if hitters_sort_label == "Matchup" else "kHR"
    df_slate_hitters = (
        pd.DataFrame(slate_hitter_rows).sort_values(sort_col, ascending=False).reset_index(drop=True)
    )

    hitters_numeric_cols = [
        "Matchup", "Ceiling", "Zone Fit", "kHR", "ISO", "xWOBA", "xWOBAC",
        "SwStr%", "PullBRL%", "Brl/BIP%", "FB%", "HH%", "LA", "Likely",
    ]
    hitters_fmt = {
        "Matchup": "{:.3f}", "Ceiling": "{:.3f}", "Zone Fit": "{:.3f}", "kHR": "{:.3f}",
        "ISO": "{:.3f}", "xWOBA": "{:.3f}", "xWOBAC": "{:.3f}",
        "SwStr%": "{:.1f}", "PullBRL%": "{:.1f}", "Brl/BIP%": "{:.1f}",
        "FB%": "{:.1f}", "HH%": "{:.1f}", "LA": "{:.1f}", "Likely": "{:.1f}",
        "PIT": "{:,}", "BIP": "{:,}",
    }

    styled_slate_hitters = (
        df_slate_hitters.style
        .background_gradient(subset=hitters_numeric_cols, cmap="RdYlGn", axis=0)
        .format(hitters_fmt)
    )
    st.caption(f"{len(df_slate_hitters):,} rows")
    st.dataframe(
        styled_slate_hitters, width='stretch', height=480, hide_index=True,
        column_config={"Oppo": st.column_config.ImageColumn("Oppo", width="small")},
    )
    st.caption(
        "\"Likely\" is a house-made composite blending Matchup, Ceiling, and kHR into one ranking "
        "number -- not a published Savant stat. Everything else follows the same real-vs-estimated "
        "split noted in How To Use."
    )
else:
    st.caption("No hitters available to rank for this slate yet.")

if live_mode_active:
    st.caption(
        f"🚚 LOAD THE TRUCK — live mode: schedule, rosters, and probable pitchers from the free "
        f"MLB Stats API; ISO, xwOBA, xwOBAC, BRL%, SwSp%, HH%, and CEIL from free "
        f"Baseball Savant / FanGraphs leaderboards ({STATS_YEAR} season) via the open-source pybaseball "
        f"library, matched by MLB player ID. MatchupScore, ZONE, FORM, KHR, PBRL%, FB%, LA, "
        f"HR_FB%, Pitcher Score, Strikeout Score, and Ball% are house-made composites derived from those "
        f"real inputs, not published Savant fields. Weather is still simulated."
    )
else:
    st.caption(
        "🚚 LOAD THE TRUCK — showing demo data. Turn on \"Use live Baseball Savant / MLB Stats API data\" "
        "in the sidebar for real rosters, pitchers, and Statcast stats."
    )
