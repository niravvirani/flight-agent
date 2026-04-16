import streamlit as st
from agent_tools import run_flight_agent

st.set_page_config(page_title="Flight + Points Finder", page_icon="✈️", layout="centered")

st.title("✈️ Flight + Points Finder")
st.caption("Compare cash prices vs loyalty points — powered by AI")

with st.form("search_form"):
    col1, col2 = st.columns(2)
    with col1:
        origin = st.text_input("From (airport code)", value="BOS", max_chars=3).upper()
        departure_date = st.date_input("Departure date")
    with col2:
        destination = st.text_input("To (airport code)", value="NRT", max_chars=3).upper()
        cabin = st.selectbox("Cabin", ["economy", "business", "first"])

    points = st.text_input(
        "Your points balances (optional)",
        placeholder="e.g. 80,000 Aeroplan and 45,000 United miles"
    )

    submitted = st.form_submit_button("Search", use_container_width=True)

if submitted:
    query = (
        f"I want to fly one way from {origin} to {destination} on {departure_date}. "
        f"Cabin: {cabin}. "
        + (f"I have {points}. " if points else "")
        + "Should I use points or pay cash? Give me the best option with CPP and a booking link."
    )

    st.divider()
    st.subheader("Agent working...")

    status_box = st.empty()
    steps_done = []

    tool_labels = {
        "search_cash_flights":       "Searching cash prices...",
        "search_award_availability": "Checking award availability...",
        "get_points_valuations":     "Getting points valuations...",
        "compare_and_recommend":     "Ranking and comparing options...",
    }

    def update_status(tool_name):
        steps_done.append(tool_labels.get(tool_name, tool_name))
        status_box.markdown("\n".join(f"- {s}" for s in steps_done))

    with st.spinner("Running..."):
        result = run_flight_agent(query, status_callback=update_status)

    st.divider()
    st.subheader("Recommendation")
    st.markdown(result)
