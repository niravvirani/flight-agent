import streamlit as st
import time
import threading
import queue
from agent_tools import run_flight_agent
from history import log_search, get_history

st.set_page_config(page_title="Flight + Points Finder", page_icon="✈️", layout="wide")

st.markdown("""
<style>
.result-box {
    border-left: 4px solid #4da3ff;
    padding: 1.2rem 1.5rem;
    border-radius: 0 8px 8px 0;
    margin-top: 1rem;
}
</style>
""", unsafe_allow_html=True)

TOOL_LABELS = {
    "search_cash_flights":       "🔎 Searching cash prices",
    "search_award_availability": "🏆 Checking award availability",
    "get_points_valuations":     "💰 Getting points valuations",
    "compare_and_recommend":     "📊 Ranking all options",
    "get_transfer_partners":     "💳 Finding transfer partners",
}

tab1, tab2 = st.tabs(["✈️ Search", "🕓 History"])

with tab1:
    st.title("✈️ Flight + Points Finder")
    st.caption("Compare cash prices vs loyalty points — powered by AI")

    with st.form("search_form"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            origin = st.text_input("From", value="BOS", max_chars=3).upper()
        with col2:
            destination = st.text_input("To", value="NRT", max_chars=3).upper()
        with col3:
            departure_date = st.date_input("Date")
        with col4:
            cabin = st.selectbox("Cabin", ["economy", "business", "first"])
        points = st.text_input("Your points balances", placeholder="e.g. 80,000 Aeroplan and 45,000 United miles")
        submitted = st.form_submit_button("🔍 Search", use_container_width=True)

    if submitted:
        query = (
            f"I want to fly one way from {origin} to {destination} on {departure_date}. "
            f"Cabin: {cabin}. "
            + (f"I have {points}. " if points else "")
            + "Should I use points or pay cash? Give me the best option with CPP, transfer partner options, and a booking link."
        )

        st.divider()
        col_steps, col_result = st.columns([1, 2])

        with col_steps:
            st.markdown("**Agent steps**")
            placeholders = {k: st.empty() for k in TOOL_LABELS}
            for k, label in TOOL_LABELS.items():
                placeholders[k].markdown(f"⬜ {label}")

        with col_result:
            st.markdown("**Recommendation**")
            result_placeholder = st.empty()
            result_placeholder.markdown("_Waiting for agent..._")

        # Simulate steps while agent runs in background
        import threading
        result_holder = {}

        def run():
            result_holder["result"] = run_flight_agent(query)

        t = threading.Thread(target=run)
        t.start()

        # Animate steps while waiting
        for i, (k, label) in enumerate(TOOL_LABELS.items()):
            placeholders[k].markdown(f"⟳ {label}...")
            time.sleep(1.5)
            placeholders[k].markdown(f"✅ {label}")

        # Wait for agent to finish
        t.join()
        result = result_holder.get("result", "No result returned.")

        if result:
            result_placeholder.markdown(result)
            log_search(origin, destination, departure_date, cabin, points, result)

with tab2:
    st.markdown("### 🕓 Search History")
    st.caption("Your last 50 searches")
    history = get_history()
    if not history:
        st.info("No searches yet.")
    else:
        for row in history:
            timestamp, orig, dest, dep_date, cab, pts_input, rec = row
            friendly_time = timestamp[:16].replace("T", " at ")
            with st.expander(f"✈️ {orig} → {dest}  |  {dep_date}  |  {friendly_time}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**Cabin:** {cab}")
                    st.markdown(f"**Points:** {pts_input or 'None'}")
                with c2:
                    st.markdown(f"**Searched:** {friendly_time}")
                st.markdown("**Result:**")
                st.markdown(rec)
