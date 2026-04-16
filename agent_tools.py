"""
Flight + Points Comparison Agent — Tool Schema
================================================
This file defines the four tools the LLM agent (Claude/GPT-4o) can call.
Each tool has:
  - A Python function (the actual logic that runs)
  - A JSON schema (what you pass to the LLM so it knows how/when to call it)

HOW IT WORKS
------------
1. User sends a natural language query, e.g.:
      "Fly from Boston to Tokyo in late March. I have 80k Aeroplan points."
2. You pass that query + these tool schemas to Claude/GPT-4o.
3. The LLM decides which tools to call and in what order.
4. You execute the tool calls and return results to the LLM.
5. The LLM synthesizes a final recommendation.

DEPENDENCIES
------------
    pip install anthropic httpx redis python-dotenv

ENVIRONMENT VARIABLES (.env)
-----------------------------
    ANTHROPIC_API_KEY=...
    AMADEUS_API_KEY=...
    AMADEUS_API_SECRET=...
    SERPAPI_KEY=...
    SEATS_AERO_KEY=...
    REDIS_URL=redis://localhost:6379
"""

import os
import json
import hashlib
import httpx
import redis
from datetime import datetime, timedelta
from dotenv import load_dotenv
import anthropic

load_dotenv()

CITY_TO_AIRPORT = {
    "NYC": "JFK,EWR,LGA",
    "LON": "LHR,LGW,STN",
    "PAR": "CDG,ORY",
    "TYO": "NRT,HND",
    "CHI": "ORD,MDW",
    "WAS": "IAD,DCA,BWI",
    "MIL": "MXP,LIN",
    "SFO": "SFO,OAK,SJC",
    "LAX": "LAX,BUR,LGB,ONT",
    "BOS": "BOS",
    "MIA": "MIA,FLL",
    "ATH": "ATH",
    "DXB": "DXB",
    "SIN": "SIN",
    "HKG": "HKG",
    "SYD": "SYD",
    "YYZ": "YYZ",
    "YVR": "YVR",
}

def resolve_airport(code):
    return CITY_TO_AIRPORT.get(code.upper(), code.upper())


# ─────────────────────────────────────────────
# Redis cache (optional but strongly recommended)
# ─────────────────────────────────────────────
try:
    cache = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
    cache.ping()
    CACHE_AVAILABLE = True
except Exception:
    CACHE_AVAILABLE = False
    print("Warning: Redis not available. Caching disabled.")

CACHE_TTL_SECONDS = 900  # 15 minutes


def _cache_get(key: str):
    if not CACHE_AVAILABLE:
        return None
    val = cache.get(key)
    return json.loads(val) if val else None


def _cache_set(key: str, value: dict):
    if CACHE_AVAILABLE:
        cache.setex(key, CACHE_TTL_SECONDS, json.dumps(value))


def _cache_key(*args) -> str:
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════
# TOOL 1 — search_cash_flights
# Searches live cash prices using Serpapi (Google Flights scraper)
# Fallback: Amadeus Flight Offers Search API
# ═══════════════════════════════════════════════════════════════════

def search_cash_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = None,
    adults: int = 1,
    cabin_class: str = "economy",
    flex_days: int = 0,
) -> dict:
    """
    Returns the cheapest cash fares for a given route and date.

    Args:
        origin:         IATA airport code, e.g. "BOS"
        destination:    IATA airport code, e.g. "NRT"
        departure_date: ISO date string "YYYY-MM-DD"
        return_date:    ISO date string for round trips (optional)
        adults:         Number of passengers
        cabin_class:    "economy" | "premium_economy" | "business" | "first"
        flex_days:      Expand search ±N days around departure_date (0 = exact)

    Returns a dict with keys:
        {
          "origin": "BOS",
          "destination": "NRT",
          "results": [
            {
              "price_usd": 842,
              "airline": "Japan Airlines",
              "departure_datetime": "2025-03-22T11:00:00",
              "arrival_datetime": "2025-03-23T14:30:00",
              "stops": 0,
              "cabin": "economy",
              "booking_url": "https://..."
            },
            ...
          ]
        }
    """
    origin = resolve_airport(origin)
    destination = resolve_airport(destination)
    cache_key = _cache_key("cash", origin, destination, departure_date,
                           return_date, adults, cabin_class, flex_days)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    results = []

    # ── Build flex date list ──────────────────────────────────────
    base_date = datetime.strptime(departure_date, "%Y-%m-%d")
    dates_to_search = [
        (base_date + timedelta(days=d)).strftime("%Y-%m-%d")
        for d in range(-flex_days, flex_days + 1)
    ]

    serpapi_key = os.getenv("SERPAPI_KEY")
    if not serpapi_key:
        # ── Fallback: Amadeus ────────────────────────────────────
        results = _search_amadeus(origin, destination, departure_date,
                                  return_date, adults, cabin_class)
    else:
        # ── Primary: Serpapi Google Flights ──────────────────────
        for date in dates_to_search:
            params = {
                "engine": "google_flights",
                "departure_id": origin,
                "arrival_id": destination,
                "outbound_date": date,
                "currency": "USD",
                "hl": "en",
                "api_key": serpapi_key,
            }
            if return_date:
                params["return_date"] = return_date
                params["type"] = "1"  # round trip
            else:
                params["type"] = "2"  # one way

            cabin_map = {
                "economy": "1", "premium_economy": "2",
                "business": "3", "first": "4"
            }
            params["travel_class"] = cabin_map.get(cabin_class, "1")

            resp = httpx.get("https://serpapi.com/search", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            for flight in data.get("best_flights", []) + data.get("other_flights", []):
                itinerary = flight.get("flights", [{}])
                first_leg = itinerary[0] if itinerary else {}
                last_leg = itinerary[-1] if itinerary else {}
                results.append({
                    "price_usd": flight.get("price"),
                    "airline": first_leg.get("airline", "Unknown"),
                    "departure_datetime": first_leg.get("departure_airport", {}).get("time"),
                    "arrival_datetime": last_leg.get("arrival_airport", {}).get("time"),
                    "stops": len(itinerary) - 1,
                    "cabin": cabin_class,
                    "booking_url": f"https://www.google.com/travel/flights",
                    "date_searched": date,
                })

    # Sort by price ascending, deduplicate
    results = sorted(
        [r for r in results if r.get("price_usd")],
        key=lambda x: x["price_usd"]
    )

    output = {"origin": origin, "destination": destination, "results": results[:10]}
    _cache_set(cache_key, output)
    return output


def _search_amadeus(origin, destination, departure_date, return_date,
                    adults, cabin_class):
    """Amadeus fallback — requires AMADEUS_API_KEY + AMADEUS_API_SECRET"""
    key = os.getenv("AMADEUS_API_KEY")
    secret = os.getenv("AMADEUS_API_SECRET")
    if not key or not secret:
        return [{"error": "No cash flight API key configured. Set SERPAPI_KEY or AMADEUS_API_KEY."}]

    # Get OAuth token
    token_resp = httpx.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={"grant_type": "client_credentials",
              "client_id": key, "client_secret": secret}
    )
    token = token_resp.json().get("access_token")

    cabin_map = {"economy": "ECONOMY", "premium_economy": "PREMIUM_ECONOMY",
                 "business": "BUSINESS", "first": "FIRST"}

    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": departure_date,
        "adults": adults,
        "travelClass": cabin_map.get(cabin_class, "ECONOMY"),
        "currencyCode": "USD",
        "max": 10,
    }
    if return_date:
        params["returnDate"] = return_date

    resp = httpx.get(
        "https://test.api.amadeus.com/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=15
    )
    resp.raise_for_status()
    offers = resp.json().get("data", [])

    results = []
    for offer in offers:
        price = float(offer["price"]["grandTotal"])
        itinerary = offer["itineraries"][0]["segments"]
        results.append({
            "price_usd": round(price, 2),
            "airline": itinerary[0].get("carrierCode", "Unknown"),
            "departure_datetime": itinerary[0]["departure"]["at"],
            "arrival_datetime": itinerary[-1]["arrival"]["at"],
            "stops": len(itinerary) - 1,
            "cabin": cabin_class,
            "booking_url": "https://www.amadeus.com",
        })
    return results


# ═══════════════════════════════════════════════════════════════════
# TOOL 2 — search_award_availability
# Fetches live award space via seats.aero API
# Fallback path described for direct scraping
# ═══════════════════════════════════════════════════════════════════

def search_award_availability(
    origin: str,
    destination: str,
    departure_date: str,
    programs: list = None,
    cabin_class: str = "economy",
    flex_days: int = 3,
) -> dict:
    """
    Returns live award availability and point costs.

    Args:
        origin:         IATA code, e.g. "BOS"
        destination:    IATA code, e.g. "NRT"
        departure_date: ISO date "YYYY-MM-DD"
        programs:       List of loyalty programs to check.
                        Options: ["aeroplan", "united", "delta", "alaska",
                                  "american", "flying_blue", "avianca"]
                        Default: all programs
        cabin_class:    "economy" | "business" | "first"
        flex_days:      Search ±N days around departure_date

    Returns:
        {
          "origin": "BOS",
          "destination": "NRT",
          "results": [
            {
              "program": "aeroplan",
              "points_required": 75000,
              "taxes_usd": 5.60,
              "cabin": "business",
              "airline": "Air Canada",
              "availability": "available",
              "seats_remaining": 2,
              "departure_date": "2025-03-22",
              "booking_url": "https://www.aircanada.com/aeroplan/..."
            },
            ...
          ]
        }
    """
    if programs is None:
        programs = ["aeroplan", "united", "delta", "alaska", "american"]

    origin = resolve_airport(origin)
    destination = resolve_airport(destination)
    cache_key = _cache_key("award", origin, destination, departure_date,
                           *sorted(programs), cabin_class, flex_days)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    seats_aero_key = os.getenv("SEATS_AERO_KEY")
    results = []

    if seats_aero_key:
        results = _search_seats_aero(
            origin, destination, departure_date,
            programs, cabin_class, flex_days, seats_aero_key
        )
    else:
        # ── No API key: return instructional placeholder ──────────
        results = [{
            "error": "seats.aero API key not configured.",
            "setup": "Get a key at https://seats.aero — ~$10/month.",
            "alternative": "For scraping fallback, see scraper_fallback.py"
        }]

    output = {"origin": origin, "destination": destination, "results": results}
    _cache_set(cache_key, output)
    return output


def _search_seats_aero(origin, destination, departure_date, programs,
                       cabin_class, flex_days, api_key):
    cabin_map = {
        "economy": "Y",
        "premium_economy": "W",
        "business": "J",
        "first": "F"
    }
    prefix = cabin_map.get(cabin_class, "Y")

    base_date = datetime.strptime(departure_date, "%Y-%m-%d")
    start_date = (base_date - timedelta(days=flex_days)).strftime("%Y-%m-%d")
    end_date = (base_date + timedelta(days=flex_days)).strftime("%Y-%m-%d")

    all_programs = ["aeroplan", "alaska", "american", "delta", "united",
                    "flyingblue", "lufthansa", "singapore", "qantas",
                    "virginatlantic", "turkish", "etihad", "emirates"]

    results = []
    for program in all_programs:
        try:
            resp = httpx.get(
                "https://seats.aero/partnerapi/search",
                headers={"Partner-Authorization": api_key},
                params={
                    "origin_airport": origin,
                    "destination_airport": destination,
                    "start_date": start_date,
                    "end_date": end_date,
                    "cabin": cabin_class,
                    "source": program,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                continue
            for avail in resp.json().get("data", []):
                if not avail.get(f"{prefix}Available"):
                    continue
                pts = avail.get(f"{prefix}MileageCost")
                if not pts or int(pts) == 0:
                    continue
                results.append({
                    "program": program,
                    "points_required": int(pts),
                    "taxes_usd": round(avail.get(f"{prefix}TotalTaxes", 0) / 100, 2),
                    "cabin": cabin_class,
                    "airline": avail.get(f"{prefix}Airlines", "Unknown"),
                    "availability": "available",
                    "seats_remaining": avail.get(f"{prefix}RemainingSeats", 0),
                    "departure_date": avail.get("Date"),
                    "booking_url": _build_booking_url(
                        program, origin, destination, avail.get("Date", departure_date)
                    ),
                })
        except Exception:
            continue

    return sorted(results, key=lambda x: x["points_required"])

def _build_booking_url(program: str, origin: str, destination: str, date: str) -> str:
    """Build a deep link directly to the airline's award booking flow."""
    urls = {
        "aeroplan":    f"https://www.aircanada.com/aeroplan/redeem/availability?org0={origin}&dest0={destination}&departureDate0={date}&lang=en-CA",
        "united":      f"https://www.united.com/en/us/flights/search/results?f={origin}&t={destination}&d={date}&tt=1&sc=7",
        "delta":       f"https://www.delta.com/us/en/flight-search/search-results?cacheKeySuffix=0&origin={origin}&destination={destination}&departureDate={date}&paxCount=1&cabinClass=coach&tripType=ONE_WAY",
        "alaska":      f"https://www.alaskaair.com/booking/choose-flights/1/{origin}/{destination}/{date}/1/0/0/ASA/Lowest",
        "american":    f"https://www.aa.com/booking/search?locale=en_US&pax=1&adult=1&type=OneWay&searchType=Award&cabin=&carriers=AA&outboundDeparts={date}&origin={origin}&destination={destination}",
    }
    return urls.get(program, f"https://awardhacker.com/?from={origin}&to={destination}&date={date}")


# ═══════════════════════════════════════════════════════════════════
# TOOL 3 — get_points_valuations
# Returns current CPP (cents per point) for each loyalty program
# ═══════════════════════════════════════════════════════════════════

def get_points_valuations(programs: list = None) -> dict:
    """
    Returns the current estimated value (cents per point) for each program.
    Values sourced from TPG / NerdWallet — update this dict monthly,
    or wire up a web scraper cron job to auto-refresh.

    Args:
        programs: List of program names to return (default: all)

    Returns:
        {
          "valuations": {
            "aeroplan":   { "cpp": 1.5, "source": "TPG", "updated": "2025-03" },
            "united":     { "cpp": 1.35, ... },
            ...
          },
          "note": "Values in US cents per point. Source: The Points Guy Mar 2025."
        }
    """
    # ── Static baseline (update monthly or wire to scraper) ───────
    # To auto-update: run a weekly cron that calls a small LLM prompt:
    # "Visit https://thepointsguy.com/points-miles-valuations/ and
    #  return the CPP for each program as JSON."
    VALUATIONS = {
        "aeroplan":       {"cpp": 1.50, "source": "TPG", "updated": "2025-03"},
        "united":         {"cpp": 1.35, "source": "TPG", "updated": "2025-03"},
        "delta":          {"cpp": 1.20, "source": "TPG", "updated": "2025-03"},
        "alaska":         {"cpp": 1.40, "source": "TPG", "updated": "2025-03"},
        "american":       {"cpp": 1.65, "source": "TPG", "updated": "2025-03"},
        "flying_blue":    {"cpp": 1.30, "source": "TPG", "updated": "2025-03"},
        "chase_ur":       {"cpp": 2.00, "source": "TPG", "updated": "2025-03"},
        "amex_mr":        {"cpp": 2.00, "source": "TPG", "updated": "2025-03"},
        "citi_ty":        {"cpp": 1.70, "source": "TPG", "updated": "2025-03"},
        "capital_one":    {"cpp": 1.85, "source": "TPG", "updated": "2025-03"},
    }

    if programs:
        filtered = {k: v for k, v in VALUATIONS.items() if k in programs}
    else:
        filtered = VALUATIONS

    return {
        "valuations": filtered,
        "note": "Values in US cents per point. Source: The Points Guy."
    }


# ═══════════════════════════════════════════════════════════════════
# TOOL 4 — compare_and_recommend
# Synthesizes cash + award results into a ranked comparison with CPP
# ═══════════════════════════════════════════════════════════════════

def compare_and_recommend(
    cash_results: dict,
    award_results: dict,
    points_balance: dict = None,
) -> dict:
    """
    Computes cents-per-point for each award option,
    compares against the best cash price, and ranks all options.

    Args:
        cash_results:   Output of search_cash_flights(...)
        award_results:  Output of search_award_availability(...)
        points_balance: Optional dict of user's point balances, e.g.:
                        {"aeroplan": 80000, "united": 45000}

    Returns:
        {
          "best_cash": { "price_usd": 842, "airline": "JAL", ... },
          "ranked_options": [
            {
              "type": "award",
              "program": "aeroplan",
              "points_required": 75000,
              "taxes_usd": 5.60,
              "cpp_achieved": 1.78,
              "cpp_baseline": 1.50,
              "vs_baseline": "+19%",
              "cash_equivalent_usd": 842,
              "verdict": "Great deal — 19% above baseline value",
              "affordable": true,   // only if points_balance provided
              "booking_url": "https://..."
            },
            {
              "type": "cash",
              "price_usd": 842,
              "airline": "JAL",
              "verdict": "Reference cash price",
              ...
            }
          ],
          "summary": "Your Aeroplan points are worth 1.78¢ each on this route — 19% above TPG baseline. Recommend redeeming if you have 75k+ Aeroplan."
        }
    """
    valuations = get_points_valuations()["valuations"]

    best_cash = cash_results.get("results", [{}])[0]
    best_cash_price = best_cash.get("price_usd", 0)

    ranked = []

    # ── Score each award option ──────────────────────────────────
    for award in award_results.get("results", []):
        program = award.get("program")
        pts = award.get("points_required", 0)
        taxes = award.get("taxes_usd", 0)

        if not pts or not best_cash_price:
            continue

        # CPP = (cash price - taxes saved) / points
        # i.e. how many cents of value you're extracting per point
        cpp_achieved = round((best_cash_price - taxes) / pts * 100, 2)
        baseline = valuations.get(program, {}).get("cpp", 1.0)
        delta_pct = round((cpp_achieved - baseline) / baseline * 100)

        if delta_pct >= 20:
            verdict = f"Excellent — {delta_pct}% above baseline"
        elif delta_pct >= 0:
            verdict = f"Good deal — {delta_pct}% above baseline"
        elif delta_pct >= -15:
            verdict = f"Fair — {abs(delta_pct)}% below baseline, still usable"
        else:
            verdict = f"Poor value — {abs(delta_pct)}% below baseline. Pay cash."

        affordable = None
        if points_balance:
            user_pts = points_balance.get(program, 0)
            affordable = user_pts >= pts

        ranked.append({
            "type": "award",
            "program": program,
            "points_required": pts,
            "taxes_usd": taxes,
            "cpp_achieved": cpp_achieved,
            "cpp_baseline": baseline,
            "vs_baseline": f"{'+' if delta_pct >= 0 else ''}{delta_pct}%",
            "cash_equivalent_usd": best_cash_price,
            "verdict": verdict,
            "affordable": affordable,
            "seats_remaining": award.get("seats_remaining"),
            "departure_date": award.get("departure_date"),
            "booking_url": award.get("booking_url"),
        })

    # Sort: best CPP first
    ranked.sort(key=lambda x: x["cpp_achieved"], reverse=True)

    # Append the best cash option at the end as a reference
    if best_cash_price:
        ranked.append({
            "type": "cash",
            "price_usd": best_cash_price,
            "airline": best_cash.get("airline"),
            "stops": best_cash.get("stops"),
            "cabin": best_cash.get("cabin"),
            "verdict": "Cash reference price",
            "booking_url": best_cash.get("booking_url"),
        })

    # ── Generate text summary ─────────────────────────────────────
    top = ranked[0] if ranked else None
    if top and top.get("type") == "award":
        summary = (
            f"Best option: {top['program'].title()} at {top['points_required']:,} pts + "
            f"${top['taxes_usd']:.2f} taxes. "
            f"That's {top['cpp_achieved']}¢/pt ({top['vs_baseline']} vs baseline). "
            f"{top['verdict']}."
        )
    elif best_cash_price:
        summary = f"No award deals beat the cash price of ${best_cash_price}. Recommend paying cash."
    else:
        summary = "Insufficient data to make a recommendation."

    return {
        "best_cash": best_cash,
        "ranked_options": ranked,
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════════
# TOOL SCHEMAS — pass these to the LLM (Claude or GPT-4o)
# These tell the model what each tool does and what arguments to pass
# ═══════════════════════════════════════════════════════════════════

TOOL_SCHEMAS_ANTHROPIC = [
    {
        "name": "search_cash_flights",
        "description": (
            "Search for live cash flight prices on a given route and date. "
            "Use this first to establish the cash price baseline for a route. "
            "Supports flexible date search (±N days)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin":         {"type": "string", "description": "IATA origin airport code, e.g. BOS"},
                "destination":    {"type": "string", "description": "IATA destination airport code, e.g. NRT"},
                "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                "return_date":    {"type": "string", "description": "Return date for round trips (optional)"},
                "adults":         {"type": "integer", "description": "Number of adult passengers", "default": 1},
                "cabin_class":    {"type": "string", "enum": ["economy", "premium_economy", "business", "first"], "default": "economy"},
                "flex_days":      {"type": "integer", "description": "Search ±N days around the departure date", "default": 0},
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
    {
        "name": "search_award_availability",
        "description": (
            "Search for live award (points) availability on a given route. "
            "Returns available award seats, point costs, and taxes for multiple loyalty programs. "
            "Use after search_cash_flights so you can compare against the cash baseline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin":         {"type": "string", "description": "IATA origin airport code"},
                "destination":    {"type": "string", "description": "IATA destination airport code"},
                "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                "programs":       {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Loyalty programs to check. Options: aeroplan, united, delta, alaska, american, flying_blue. Omit to check all."
                },
                "cabin_class":    {"type": "string", "enum": ["economy", "business", "first"], "default": "economy"},
                "flex_days":      {"type": "integer", "description": "Search ±N days", "default": 3},
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
    {
        "name": "get_points_valuations",
        "description": (
            "Returns the current estimated value (in cents per point) for each loyalty program. "
            "Use this to contextualize whether a redemption is above or below baseline value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "programs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Loyalty programs to return valuations for. Omit for all."
                }
            },
        },
    },
    {
        "name": "compare_and_recommend",
        "description": (
            "Computes cents-per-point for each award option, ranks all options, "
            "and generates a recommendation. Call this after getting both cash and award results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cash_results":   {"type": "object", "description": "Output from search_cash_flights"},
                "award_results":  {"type": "object", "description": "Output from search_award_availability"},
                "points_balance": {
                    "type": "object",
                    "description": "User's point balances by program, e.g. {\"aeroplan\": 80000}. Optional."
                },
            },
            "required": ["cash_results", "award_results"],
        },
    },
]

# GPT-4o uses a slightly different schema format
TOOL_SCHEMAS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": s["name"],
            "description": s["description"],
            "parameters": s["input_schema"],
        }
    }
    for s in TOOL_SCHEMAS_ANTHROPIC
]


# ═══════════════════════════════════════════════════════════════════
# TOOL DISPATCHER
# Maps tool names → Python functions so the agent loop can call them
# ═══════════════════════════════════════════════════════════════════

TOOL_REGISTRY = {
    "search_cash_flights":       search_cash_flights,
    "search_award_availability": search_award_availability,
    "get_points_valuations":     get_points_valuations,
    "compare_and_recommend":     compare_and_recommend,
}


def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Execute a tool by name with the given arguments.
    Called inside the agent loop when the LLM requests a tool.
    """
    fn = TOOL_REGISTRY.get(tool_name)
    if not fn:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return fn(**tool_input)
    except Exception as e:
        return {"error": str(e), "tool": tool_name, "input": tool_input}


# ═══════════════════════════════════════════════════════════════════
# AGENT LOOP (Claude)
# Runs the full multi-turn tool-use conversation
# ═══════════════════════════════════════════════════════════════════

def run_flight_agent(user_query: str, status_callback=None) -> str:
    """
    Main entry point. Pass a natural language query like:
      "Find me flights from Boston to Tokyo in late March.
       I have 80,000 Aeroplan and 45,000 United miles."

    Returns a final text recommendation.
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    system_prompt = """You are a flight booking expert that helps users find the best way
to book flights — either paying cash or using loyalty points.

When a user asks about flights:
1. Call search_cash_flights to get the cash price baseline.
2. Call search_award_availability to get award options (infer programs from any points mentioned).
3. Call get_points_valuations for context on whether redemptions are worth it.
4. Call compare_and_recommend to synthesize everything.
5. Present a clear, concise recommendation: best cash option, best award option, and your verdict.

Always state:
- The best cash price
- The best award option (program, points + taxes)
- The CPP achieved vs baseline
- Whether you recommend cash or points and why
- A direct booking link"""

    messages = [{"role": "user", "content": user_query}]

    # ── Agentic loop ─────────────────────────────────────────────
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=system_prompt,
            tools=TOOL_SCHEMAS_ANTHROPIC,
            messages=messages,
        )

        # Add assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        # If done, return the final text
        if response.stop_reason == "end_turn":
            return next(
                (block.text for block in response.content if hasattr(block, "text")),
                "No response generated."
            )

        # If tool use requested, execute all tool calls
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason
            break

    return "Agent loop ended unexpectedly."


# ═══════════════════════════════════════════════════════════════════
# QUICK TEST
# Run: python agent_tools.py
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    query = (
        "I want to fly from Boston (BOS) to Tokyo (NRT) around March 22, 2025. "
        "One way, economy class. I have 80,000 Aeroplan points and 45,000 United miles. "
        "Is it better to use points or just pay cash?"
    )
    print("Query:", query)
    print("\n" + "="*60 + "\n")
    result = run_flight_agent(query)
    print(result)
