import streamlit as st
import time
import threading
from datetime import date, timedelta, datetime
from agent_tools import (
    run_flight_agent,
    search_cash_flights,
    search_award_availability,
    get_points_valuations,
    compare_and_recommend,
    resolve_airport,
)
from history import log_search, get_history

st.set_page_config(page_title="Flight + Points Finder", page_icon="✈️", layout="wide")

st.markdown("""
<style>
.ai-box {
    border-left: 4px solid #4da3ff;
    padding: 1rem 1.25rem;
    border-radius: 0 10px 10px 0;
    background: rgba(77,163,255,0.07);
    margin-bottom: 1.25rem;
}
.ai-label {
    font-size: 11px;
    color: #4da3ff;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    margin-bottom: 6px;
}
.card {
    border: 1px solid rgba(128,128,128,0.2);
    border-radius: 10px;
    padding: 0.85rem 1rem;
    margin-bottom: 8px;
}
.card-best { border: 2px solid #28a745 !important; }
.badge { font-size: 10px; padding: 2px 8px; border-radius: 20px; margin-left: 6px; font-weight: 500; }
.badge-great { background:#d4edda; color:#155724; }
.badge-good  { background:#cce5ff; color:#004085; }
.badge-poor  { background:#f8d7da; color:#721c24; }
.badge-ref   { background:#e2e3e5; color:#383d41; }
</style>
""", unsafe_allow_html=True)

def parse_points_to_programs(points_text: str) -> list:
    """
    Parse free text points input into a list of reachable airline programs.
    e.g. "100,000 Amex MR and 50,000 Aeroplan" -> ["aeroplan", "united", "delta", ...]
    """
    if not points_text:
        return []

    from transfer_partners import TRANSFER_PARTNERS, REVERSE_INDEX

    # Map common user terms to card currency keys
    currency_aliases = {
        "amex": "amex_mr", "amex mr": "amex_mr", "membership rewards": "amex_mr",
        "chase": "chase_ur", "chase ur": "chase_ur", "ultimate rewards": "chase_ur",
        "capital one": "capital_one", "venture": "capital_one",
        "citi": "citi_ty", "thankyou": "citi_ty", "citi ty": "citi_ty",
        "bilt": "bilt",
        "wells fargo": "wells_fargo", "wells": "wells_fargo",
        # Airline programs map to themselves
        "aeroplan": "aeroplan", "air canada": "aeroplan",
        "united": "united", "mileageplus": "united",
        "delta": "delta", "skymiles": "delta",
        "alaska": "alaska", "mileage plan": "alaska",
        "american": "american", "aadvantage": "american",
        "flyingblue": "flyingblue", "flying blue": "flyingblue",
        "air france": "flyingblue", "klm": "flyingblue",
        "singapore": "singapore", "krisflyer": "singapore",
        "virgin atlantic": "virginatlantic", "virgin": "virginatlantic",
        "turkish": "turkish", "miles and smiles": "turkish",
        "etihad": "etihad", "etihad guest": "etihad",
        "emirates": "emirates", "skywards": "emirates",
        "qatar": "qatar", "avios": "qatar",
        "qantas": "qantas",
        "avianca": "avianca", "lifemiles": "avianca",
        "british": "british", "british airways": "british",
        "finnair": "finnair",
    }

    text_lower = points_text.lower()
    reachable_programs = set()

    for alias, currency in currency_aliases.items():
        if alias in text_lower:
            # Check if it's a card currency with transfer partners
            if currency in TRANSFER_PARTNERS:
                for airline in TRANSFER_PARTNERS[currency].get("airlines", []):
                    reachable_programs.add(airline["program"])
            # Check if it's a direct airline program
            elif currency in REVERSE_INDEX or currency in [
                "aeroplan","united","delta","alaska","american","flyingblue",
                "singapore","virginatlantic","turkish","etihad","emirates",
                "qatar","qantas","avianca","british","finnair"
            ]:
                reachable_programs.add(currency)

    return list(reachable_programs)

TOOL_LABELS = {
    "search_cash_flights":       "🔎 Searching cash prices",
    "search_award_availability": "🏆 Checking award availability",
    "get_points_valuations":     "💰 Getting points valuations",
    "compare_and_recommend":     "📊 Ranking all options",
    "get_transfer_partners":     "💳 Finding transfer partners",
}

# Programs that require manual login before search results appear
REQUIRES_LOGIN = {
    "delta", "virginatlantic", "british", "singapore",
    "etihad", "emirates", "turkish", "qatar", "aeromexico", "finnair"
}

# Concise per-program instructions for replicating the search manually
MANUAL_INSTRUCTIONS = {
    "delta":          f"1. Log in to Delta.com → 2. Click 'Shop with Miles' → 3. Enter {{origin}}→{{destination}}, {{date}}, Economy → 4. Hit Search",
    "virginatlantic": f"1. Log in to FlyWith.VirginAtlantic.com → 2. Click 'Book a flight with points' → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "british":        f"1. Log in to BritishAirways.com → 2. Click 'Spend Avios' → Flights → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "singapore":      f"1. Log in to SingaporeAir.com → 2. Click 'KrisFlyer' → Redeem Miles → Flights → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "etihad":         f"1. Log in to Etihad.com → 2. Click 'Pay with miles' on flight search → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "emirates":       f"1. Log in to Emirates.com → 2. Click 'Use Miles' toggle on search → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "turkish":        f"1. Log in to TurkishAirlines.com → 2. Go to Miles&Smiles → Award Tickets → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "qatar":          f"1. Log in to QatarAirways.com → 2. Click 'Privilege Club' → Redeem Avios → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "aeromexico":     f"1. Log in to ClubPremier.com → 2. Click 'Redeem' → Flights → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
    "finnair":        f"1. Log in to Finnair.com → 2. Click 'Finnair Plus' → Spend Points → Flights → 3. Enter {{origin}}→{{destination}}, {{date}} → 4. Search",
}

EXAMPLE_QUERIES = [
    "Fly me from BOS to LHR in June. I have 80k Aeroplan and 45k Chase UR.",
    "Best way to get to Tokyo from JFK in August with 60k United miles?",
    "Compare cash vs points for NYC to Paris next month.",
    "Is it worth using 50k Amex MR for business class to Dubai?",
]

ALL_PROGRAMS = ["aeroplan","united","delta","alaska","american",
                "flyingblue","singapore","virginatlantic","turkish",
                "emirates","qatar","british","etihad","qantas",
                "avianca","aeromexico","finnair"]

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conv_history" not in st.session_state:
    st.session_state.conv_history = []


def animate_steps():
    ph = st.empty()
    cols = ph.columns(5)
    placeholders = {}
    for i, (k, label) in enumerate(TOOL_LABELS.items()):
        placeholders[k] = cols[i].empty()
        placeholders[k].markdown(f"⬜ {label}")
    return ph, placeholders


def run_agent_animated(query, conversation_history=None):
    result_holder = {}
    def run():
        res, hist = run_flight_agent(query, conversation_history=conversation_history)
        result_holder["result"] = res
        result_holder["history"] = hist
    t = threading.Thread(target=run)
    t.start()
    step_ph = st.empty()
    with step_ph.container():
        cols = st.columns(5)
        for i, (k, label) in enumerate(TOOL_LABELS.items()):
            p = cols[i].empty()
            p.markdown(f"⟳ {label}...")
            time.sleep(1.5)
            p.markdown(f"✅ {label}")
    t.join()
    step_ph.empty()
    return result_holder.get("result",""), result_holder.get("history", conversation_history or [])


def render_result_cards(cash_results, award_results, compare_results, filters, valuations=None):
    if valuations is None:
        valuations = get_points_valuations()["valuations"]
    all_options = []

    # Add award options
    for a in award_results.get("results", []):
        if not a.get("points_required"):
            continue
        prog = a.get("program","")
        pts = a.get("points_required", 0)
        taxes = a.get("taxes_usd", 0)
        cpp = None
        best_cash_price = cash_results.get("results",[{}])[0].get("price_usd", 0) if cash_results.get("results") else 0
        if pts and best_cash_price:
            cpp = round((best_cash_price - taxes) / pts * 100, 2)
        baseline = get_points_valuations().get("valuations",{}).get(prog,{}).get("cpp", 1.0)
        delta_pct = round((cpp - baseline) / baseline * 100) if cpp else 0
        all_options.append({
            "type": "award",
            "program": prog,
            "points": pts,
            "taxes": taxes,
            "cpp": cpp,
            "baseline": baseline,
            "delta_pct": delta_pct,
            "stops": a.get("stops", 0),
            "airline": a.get("airline",""),
            "date": a.get("departure_date",""),
            "seats": a.get("seats_remaining"),
            "booking_url": a.get("booking_url",""),
            "cabin": a.get("cabin","economy"),
        })

    # Add cash options
    for c in cash_results.get("results", []):
        if not c.get("price_usd"):
            continue
        all_options.append({
            "type": "cash",
            "price": c.get("price_usd"),
            "airline": c.get("airline",""),
            "stops": c.get("stops", 0),
            "cabin": c.get("cabin","economy"),
            "date": c.get("date_searched",""),
            "booking_url": c.get("booking_url",""),
            "program": None,
            "cpp": None,
        })

    if not all_options:
        st.warning("No results found for this route and date. Try different dates or nearby airports.")
        return

    # Apply filters
    if not filters.get("show_award"):
        all_options = [o for o in all_options if o["type"] != "award"]
    if not filters.get("show_cash"):
        all_options = [o for o in all_options if o["type"] != "cash"]
    # Date filter
    selected_dates = filters.get("selected_dates", [])
    if selected_dates:
        all_options = [o for o in all_options
                      if o.get("date") in selected_dates or o.get("date_searched") in selected_dates]
    if not filters.get("show_nonstop"):
        all_options = [o for o in all_options if o.get("stops",0) != 0]
    if not filters.get("show_one_stop"):
        all_options = [o for o in all_options if o.get("stops",0) != 1]
    if filters.get("program_filter"):
        all_options = [o for o in all_options
                      if o["type"] == "cash" or o.get("program") in filters["program_filter"]]
    # For cash: filter by actual price. For awards: filter by constructed value (pts x CPP + taxes)
    max_p = filters.get("max_price", 99999)
    def within_price(o):
        if o["type"] == "cash":
            return o.get("price", 0) <= max_p
        else:
            cpp_v = valuations.get(o.get("program",""), {}).get("cpp", 1.20)
            val = round((o.get("points",0) * cpp_v / 100) + o.get("taxes",0), 2)
            return val <= max_p
    all_options = [o for o in all_options if within_price(o)]
    all_options = [o for o in all_options
                  if (o["type"] == "cash") or (o.get("points",0) <= filters.get("max_points", 9999999))]

    # Sort
    sort_col = filters.get("sort_col", "cpp")
    sort_asc = filters.get("sort_asc", False)

    def compute_value(o):
        """Unified value: cash price for cash, pts x CPP + taxes for awards."""
        if o.get("type") == "cash":
            return o.get("price") or 0
        cpp_v = valuations.get(o.get("program",""), {}).get("cpp", 1.20)
        return round((o.get("points",0) * cpp_v / 100) + o.get("taxes",0), 2)

    def sort_key(x):
        if sort_col == "cpp":
            if x.get("type") == "cash":
                return -9999
            return x.get("cpp") or 0
        elif sort_col == "points":
            if x.get("type") == "cash":
                return x.get("price") or 0
            return x.get("points") or 0
        elif sort_col == "value":
            return compute_value(x)
        elif sort_col == "stops":
            return x.get("stops") or 0
        elif sort_col == "program":
            return (x.get("program") or x.get("airline") or "").lower()
        return x.get("cpp") or 0

    reverse = not sort_asc if sort_col != "program" else sort_asc
    all_options.sort(key=sort_key, reverse=reverse)

    st.markdown(f"**{len(all_options)} options found**")

    for i, opt in enumerate(all_options):
        is_best = (i == 0)
        card_class = "card card-best" if is_best else "card"

        if opt["type"] == "award":
            cpp = opt.get("cpp")
            delta = opt.get("delta_pct", 0)
            if delta >= 15:
                badge = '<span class="badge badge-great">Great value</span>'
                cpp_color = "green"
            elif delta >= 0:
                badge = '<span class="badge badge-good">Good</span>'
                cpp_color = "blue"
            else:
                badge = '<span class="badge badge-poor">Poor value</span>'
                cpp_color = "red"
            if is_best:
                badge += ' <span class="badge badge-great">⭐ Best</span>'

            with st.container():
                st.markdown(f'<div class="{card_class}">', unsafe_allow_html=True)
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
                with c1:
                    st.markdown(f"**{opt['program'].title()}** — {opt['airline']} {badge}", unsafe_allow_html=True)
                    stops_label = "Nonstop" if opt.get("stops",0)==0 else f"{opt.get('stops')} stop(s)"
                    seats = f" · {opt['seats']} seats left" if opt.get("seats") else ""
                    st.caption(f"{opt.get('date','')} · {stops_label} · {opt['cabin'].title()}{seats}")
                with c2:
                    if cpp:
                        st.markdown(f"**:{cpp_color}[{cpp}¢/pt]**")
                        st.caption(f"baseline {opt['baseline']}¢")
                with c3:
                    st.markdown(f"**{opt['points']:,} pts**")
                    st.caption(f"+${opt['taxes']:.2f} fees")
                with c4:
                    # True dollar value of this redemption
                    basket_cpp = valuations.get(opt.get("program",""), {}).get("cpp", 1.20)
                    true_val = round((opt['points'] * basket_cpp / 100) + opt['taxes'], 2)
                    best_cash_p = cash_results.get("results",[{}])[0].get("price_usd", 0)
                    color = "green" if true_val < best_cash_p else "red"
                    st.markdown(f"**:{color}[${true_val:,.0f}]**")
                    st.caption("value (pts+taxes)")
                with c5:
                    prog = opt.get("program","")
                    origin_val = data.get("origin","") if "data" in dir() else ""
                    dest_val = data.get("destination","") if "data" in dir() else ""
                    date_val = data.get("departure_date","") if "data" in dir() else ""
                    if opt.get("booking_url"):
                        if prog in REQUIRES_LOGIN:
                            st.link_button("Book →", opt["booking_url"], use_container_width=True)
                            instructions = MANUAL_INSTRUCTIONS.get(prog,"")
                            if instructions:
                                instructions = instructions.format(
                                    origin=origin_val,
                                    destination=dest_val,
                                    date=date_val
                                )
                            st.caption(f"⚠️ Login required")
                            with st.expander("How to search", expanded=False):
                                st.markdown(instructions)
                        else:
                            st.link_button("Book →", opt["booking_url"], use_container_width=True)
                            st.caption("✅ Opens search directly")
                st.markdown('</div>', unsafe_allow_html=True)

        else:
            with st.container():
                st.markdown(f'<div class="{card_class}">', unsafe_allow_html=True)
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
                with c1:
                    ref_badge = '<span class="badge badge-ref">Cash</span>' + (' <span class="badge badge-great">⭐ Best</span>' if is_best else '')
                    st.markdown(f"**{opt['airline']}** {ref_badge}", unsafe_allow_html=True)
                    stops_label = "Nonstop" if opt.get("stops",0)==0 else f"{opt.get('stops')} stop(s)"
                    st.caption(f"{opt.get('date','')} · {stops_label} · {opt['cabin'].title()}")
                with c2:
                    st.markdown("**—**")
                    st.caption("cash price")
                with c3:
                    st.markdown(f"**${opt['price']:,.0f}**")
                with c4:
                    st.markdown(f"**${opt['price']:,.0f}**")
                    st.caption("value")
                with c5:
                    if opt.get("booking_url"):
                        st.link_button("🔍 See on Kayak →", opt["booking_url"], use_container_width=True)
                        st.caption("✅ Opens directly on Kayak")
                st.markdown('</div>', unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────
st.title("✈️ Flight + Points Finder")
col_t, col_tog = st.columns([3, 1])
with col_tog:
    if "mode_state" not in st.session_state:
        st.session_state.mode_state = "search"
    tog_label = "💬 Chat mode" if st.session_state.mode_state == "search" else "🔍 Search mode"
    if st.button(tog_label, key="mode_toggle_btn", use_container_width=True):
        st.session_state.mode_state = "chat" if st.session_state.mode_state == "search" else "search"
        st.rerun()
mode = "🔍 Search" if st.session_state.mode_state == "search" else "💬 Chat"
st.divider()

tab1, tab2, tab3 = st.tabs(["Main", "🕓 History", "📊 Valuations"])

with tab1:

    # ══ SEARCH MODE ═══════════════════════════════════════════════
    if mode == "🔍 Search":
        st.caption("Compare cash vs points — live results with filters.")

        with st.form("search_form"):
            c1, c2, c3, c4 = st.columns(4)
            with c1: origin = st.text_input("From", value="BOS", max_chars=3).upper()
            with c2: destination = st.text_input("To", value="LHR", max_chars=3).upper()
            with c3: departure_date = st.date_input("Date", value=datetime.now().date() + timedelta(days=1), min_value=datetime.now().date() + timedelta(days=1))
            with c4:
                _cabin_options = ["economy", "business", "first"]
                _saved = st.session_state.get("submitted_cabin", "economy")
                cabin = st.selectbox("Cabin", _cabin_options, index=_cabin_options.index(_saved) if _saved in _cabin_options else 0)
            points = st.text_input("Your points", placeholder="e.g. 80,000 Aeroplan and 45,000 Chase UR")
            submitted = st.form_submit_button("🔍 Search", use_container_width=True)

        if submitted or "search_data" in st.session_state:
            if submitted:
                st.session_state["submitted_cabin"] = st.session_state.get("cabin_select", "economy")
                if "search_data" in st.session_state:
                    del st.session_state["search_data"]
                if "sort_col" in st.session_state:
                    del st.session_state["sort_col"]
                # Parse points input to get reachable programs
                if points:
                    _reachable = parse_points_to_programs(points)
                    if _reachable:
                        st.session_state["auto_program_filter"] = _reachable
                    else:
                        st.session_state["auto_program_filter"] = None
                else:
                    st.session_state["auto_program_filter"] = None
                query = (
                    f"Fly one way from {origin} to {destination} on {departure_date}. "
                    f"Cabin: {cabin}. "
                    + (f"I have {points}. " if points else "")
                    + "Should I use points or pay cash? Give CPP, transfer partners, and booking links."
                )
                st.divider()
                st.markdown("**Agent working...**")

                with st.spinner(""):
                    # Run agent for AI text
                    ai_result_holder = {}
                    def run_ai(q=query):
                        res, hist = run_flight_agent(q, conversation_history=None)
                        ai_result_holder["result"] = res

                    ai_thread = threading.Thread(target=run_ai)
                    ai_thread.start()

                    # Animate steps
                    cols = st.columns(5)
                    for i, (k, label) in enumerate(TOOL_LABELS.items()):
                        p = cols[i].empty()
                        p.markdown(f"⟳ {label}...")
                        time.sleep(1.5)
                        p.markdown(f"✅ {label}")


                    st.session_state["submitted_cabin"] = cabin
                    cash_results = search_cash_flights(
                        origin, destination, str(departure_date),
                        cabin_class=cabin, flex_days=3
                    )
                    award_results = search_award_availability(
                        origin, destination, str(departure_date),
                        cabin_class=cabin, flex_days=3
                    )
                    compare_results = compare_and_recommend(cash_results, award_results)
                    ai_thread.join()

                st.session_state.search_data = {
                    "cash": cash_results,
                    "award": award_results,
                    "compare": compare_results,
                    "ai_result": ai_result_holder.get("result",""),
                    "origin": origin,
                    "destination": destination,
                    "departure_date": str(departure_date),
                    "cabin": st.session_state["submitted_cabin"],
                    "points": points,
                }
                log_search(origin, destination, str(departure_date), cabin, points,
                           ai_result_holder.get("result",""))

            # Render from session state
            data = st.session_state.search_data
            origin = data["origin"]
            destination = data["destination"]
            departure_date = data["departure_date"]
            cabin = data["cabin"]
            st.caption(f"DEBUG: stored cabin = {cabin}")

            st.divider()

            # Summary metrics
            cash_list = data["cash"].get("results",[])
            award_list = data["award"].get("results",[])
            best_cash = cash_list[0].get("price_usd") if cash_list else None
            # Best award = highest CPP from raw award list
            best_cash_price = cash_list[0].get("price_usd", 0) if cash_list else 0
            best_award = None
            best_cpp = 0
            for a in award_list:
                pts = a.get("points_required", 0)
                taxes = a.get("taxes_usd", 0)
                if pts and best_cash_price:
                    cpp = round((best_cash_price - taxes) / pts * 100, 2)
                    if cpp > best_cpp:
                        best_cpp = cpp
                        best_award = a

            m1,m2,m3,m4 = st.columns(4)
            with m1:
                st.metric("Best cash", f"${best_cash:,.0f}" if best_cash else "N/A")
            with m2:
                if best_award:
                    st.metric("Best award", f"{best_award.get('points_required',0):,} pts",
                              delta=f"{best_award.get('program','').title()}")
                else:
                    st.metric("Best award", "N/A")
            with m3:
                ranked = data["compare"].get("ranked_options",[])
                top_award = next((r for r in ranked if r.get("type")=="award"), None)
                if top_award:
                    st.metric("Best CPP", f"{top_award.get('cpp_achieved',0)}¢",
                              delta=top_award.get("vs_baseline",""))
                else:
                    st.metric("Best CPP", "N/A")
            with m4:
                total = len(cash_list) + len(award_list)
                st.metric("Options found", f"{total} ({len(award_list)} award · {len(cash_list)} cash)")

            st.divider()

            st.divider()

            # AI recommendation at top
            ai_text = data.get("ai_result", "")
            if ai_text and len(ai_text.strip()) > 10:
                st.markdown('<div class="ai-box"><div class="ai-label">✨ AI Recommendation</div>' +
                            ai_text + '</div>', unsafe_allow_html=True)
            else:
                st.info("⏳ AI recommendation is still loading — try your search again if this persists.")

            # Filters + cards
            filter_col, result_col = st.columns([1, 3])

            with filter_col:
                st.markdown("#### Filters")
                show_award = st.checkbox("Award options", value=True)
                show_cash = st.checkbox("Cash options", value=True)
                st.markdown("**Stops**")
                show_nonstop = st.checkbox("Nonstop", value=True)
                show_one_stop = st.checkbox("1 stop", value=True)
                st.markdown("**Programs**")
                _auto_filter = st.session_state.get("auto_program_filter")
                _default_programs = [p for p in (_auto_filter or ALL_PROGRAMS) if p in ALL_PROGRAMS]
                if _auto_filter:
                    st.caption(f"Showing {len(_default_programs)} programs reachable from your points")
                program_filter = st.multiselect(
                    "Programs",
                    ALL_PROGRAMS,
                    default=_default_programs,
                    label_visibility="collapsed",
                    key="program_filter"
                )
                st.markdown("**Max cash price**")
                max_price = st.slider("$", 0, 20000, 20000, 50, label_visibility="collapsed")
                st.markdown("**Max points**")
                max_points = st.slider("pts", 0, 500000, 500000, 5000, label_visibility="collapsed")
                st.markdown("**Sort by**")
                sort_by = st.radio("Sort", ["Best value","Lowest price","Fewest stops","Highest CPP"],
                                   label_visibility="collapsed")

                st.markdown("**Flex dates**")
                try:
                    base = datetime.strptime(str(departure_date), "%Y-%m-%d")
                    date_options = [(base + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(-3,4)]
                    date_labels = [(base + timedelta(days=d)).strftime("%b %d") for d in range(-3,4)]
                    selected_dates = []
                    for d_val, d_label in zip(date_options, date_labels):
                        checked = st.checkbox(d_label, value=(d_val == str(departure_date)), key=f"date_{d_val}")
                        if checked:
                            selected_dates.append(d_val)
                    if not selected_dates:
                        selected_dates = date_options  # if none selected, show all
                except:
                    selected_dates = []
                    st.caption("N/A")


            with result_col:
                st.markdown("#### All Results")

                # Clickable column sort headers
                if "sort_col" not in st.session_state:
                    st.session_state.sort_col = "cpp"
                    st.session_state.sort_asc = False
                    st.session_state.sort_initialized = True

                h1, h2, h3, h4, h5, h6 = st.columns([3, 1, 1, 1, 1, 1])
                def sort_btn(col, label, container):
                    arrow = ""
                    if st.session_state.sort_col == col:
                        arrow = " ↑" if st.session_state.sort_asc else " ↓"
                    if container.button(f"{label}{arrow}", key=f"sort_{col}", use_container_width=True):
                        if st.session_state.sort_col == col:
                            st.session_state.sort_asc = not st.session_state.sort_asc
                        else:
                            st.session_state.sort_col = col
                            st.session_state.sort_asc = False
                        st.rerun()

                sort_btn("program", "Airline / Program", h1)
                sort_btn("cpp", "CPP", h2)
                sort_btn("points", "Points", h3)
                sort_btn("value", "Value ($)", h4)
                sort_btn("stops", "Stops", h5)
                h6.write("")  # Book column — no sort
                st.divider()

                col_sort_map = {
                    "cpp":     "Highest CPP",
                    "points":  "Lowest price",
                    "price":   "Lowest price",
                    "stops":   "Fewest stops",
                    "program": "Best value",
                }
                resolved_sort = col_sort_map.get(st.session_state.sort_col, "Best value")

                render_result_cards(
                    data["cash"], data["award"], data["compare"],
                    valuations=get_points_valuations()["valuations"],
                    filters={
                        "show_award": show_award,
                        "show_cash": show_cash,
                        "show_nonstop": show_nonstop,
                        "show_one_stop": show_one_stop,
                        "program_filter": program_filter,
                        "max_price": max_price,
                        "max_points": max_points,
                        "sort_by": resolved_sort,
                        "sort_asc": st.session_state.sort_asc,
                        "sort_col": st.session_state.sort_col,
                        "selected_dates": selected_dates,
                    }
                )

    # ══ CHAT MODE ═════════════════════════════════════════════════
    else:
        st.caption("Ask anything — then follow up. Agent remembers context.")

        if not st.session_state.messages:
            st.markdown("**Try asking:**")
            cols = st.columns(2)
            for i, q in enumerate(EXAMPLE_QUERIES):
                with cols[i % 2]:
                    if st.button(q, key=f"ex_{i}", use_container_width=True):
                        st.session_state.pending_query = q
                        st.rerun()

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if "pending_query" in st.session_state:
            user_input = st.session_state.pending_query
            del st.session_state.pending_query
        else:
            user_input = st.chat_input("Ask about any flight — or follow up...")

        if user_input:
            with st.chat_message("user"):
                st.markdown(user_input)
            st.session_state.messages.append({"role": "user", "content": user_input})

            with st.chat_message("assistant"):
                result, updated_history = run_agent_animated(
                    user_input,
                    conversation_history=st.session_state.conv_history
                )
                st.session_state.conv_history = updated_history
                st.markdown('<div class="ai-box"><div class="ai-label">✨ AI Recommendation</div>' +
                            result + '</div>', unsafe_allow_html=True)

            st.session_state.messages.append({"role": "assistant", "content": result})
            log_search("chat","chat","chat","chat", user_input, result)

        if st.session_state.messages:
            if st.button("🗑️ Clear chat"):
                st.session_state.messages = []
                st.session_state.conv_history = []
                st.rerun()

with tab2:
    st.markdown("### 🕓 Search History")
    history = get_history()
    if not history:
        st.info("No searches yet.")
    else:
        for row in history:
            timestamp, orig, dest, dep_date, cab, pts_input, rec = row
            friendly_time = timestamp[:16].replace("T", " at ")
            with st.expander(f"💬 {pts_input[:60]}...  |  {friendly_time}"):
                st.markdown(f"**Asked:** {pts_input}")
                st.markdown(f"**When:** {friendly_time}")
                st.markdown("**Result:**")
                st.markdown(rec)

with tab3:
    st.markdown("### 📊 Points & Miles Valuations")
    st.caption("Basket average of TPG, NerdWallet, and Upgraded Points — used to calculate true redemption value.")

    import pandas as pd
    from datetime import datetime
    import json, os

    VAL_CACHE_FILE = "valuations_cache.json"

    def load_val_cache():
        if os.path.exists(VAL_CACHE_FILE):
            with open(VAL_CACHE_FILE, "r") as f:
                return json.load(f)
        return None

    def save_val_cache(data):
        with open(VAL_CACHE_FILE, "w") as f:
            json.dump(data, f)

    def build_val_table():
        raw = get_points_valuations()["valuations"]
        rows = []
        for prog, vals in raw.items():
            rows.append({
                "Program": prog.replace("_", " ").title(),
                "TPG (¢)": vals.get("tpg") or vals.get("cpp", "—"),
                "NerdWallet (¢)": vals.get("nerdwallet") or vals.get("cpp", "—"),
                "Upgraded Points (¢)": vals.get("upgraded") or vals.get("cpp", "—"),
                "Basket Avg (¢)": vals.get("cpp", "—"),
            })
        return pd.DataFrame(rows)

    cache = load_val_cache()

    col_ref, col_btn = st.columns([3, 1])
    with col_ref:
        if cache:
            st.caption(f"Last updated: {cache.get('updated_at', 'Unknown')}")
        else:
            st.caption("Last updated: using built-in April 2026 data")

    with col_btn:
        if st.button("🔄 Refresh valuations", use_container_width=True):
            with st.spinner("Updating..."):
                try:
                    import anthropic as _anthropic
                    from dotenv import load_dotenv
                    load_dotenv()
                    _client = _anthropic.Anthropic()
                    _resp = _client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=1500,
                        tools=[{
                            "type": "web_search_20250305",
                            "name": "web_search"
                        }],
                        messages=[{
                            "role": "user",
                            "content": (
                                "Search for the latest points and miles valuations from TPG (thepointsguy.com/loyalty-programs/monthly-valuations) "
                                "and NerdWallet (nerdwallet.com/travel/learn/airline-miles-and-hotel-points-valuations). "
                                "Return ONLY a JSON object with program names as keys and objects with tpg, nerdwallet, upgraded, cpp fields as values. "
                                "Programs: aeroplan, united, delta, alaska, american, flyingblue, singapore, virginatlantic, turkish, etihad, emirates, qatar, qantas, avianca, british. "
                                "No markdown, no explanation, just raw JSON."
                            )
                        }]
                    )
                    raw_text = next((b.text for b in _resp.content if hasattr(b, "text")), "")
                    raw_text = raw_text.strip().replace("```json","").replace("```","").strip()
                    new_vals = json.loads(raw_text)
                    cache_data = {
                        "updated_at": datetime.now().strftime("%B %d, %Y at %I:%M %p"),
                        "valuations": new_vals
                    }
                    save_val_cache(cache_data)
                    st.rerun()
                except Exception as e:
                    st.warning(f"Refresh failed — using built-in data. ({e})")

    # Show table
    if cache and "valuations" in cache:
        display_vals = cache["valuations"]
        rows = []
        for prog, vals in display_vals.items():
            rows.append({
                "Program": prog.replace("_"," ").title(),
                "TPG (¢)": vals.get("tpg","—"),
                "NerdWallet (¢)": vals.get("nerdwallet","—"),
                "Upgraded Points (¢)": vals.get("upgraded","—"),
                "Basket Avg (¢)": vals.get("cpp","—"),
            })
        df = pd.DataFrame(rows)
    else:
        df = build_val_table()

    st.dataframe(df, use_container_width=True, hide_index=True)


    st.markdown("---")
    st.markdown("**Sources**")
    st.markdown("- [The Points Guy - Monthly Valuations](https://thepointsguy.com/loyalty-programs/monthly-valuations/)")
    st.markdown("- [NerdWallet - Points and Miles Valuations](https://www.nerdwallet.com/travel/learn/airline-miles-and-hotel-points-valuations)")
    st.markdown("- [Upgraded Points - Points Valuations](https://upgradedpoints.com/travel/points-and-miles-valuations/)")
    st.caption("Basket average = simple mean of all three sources. Updated April 2026.")
