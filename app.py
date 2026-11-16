"""
dashQ - a queue management system (Python + Streamlit + SQLite)

Four screens, each can be opened on a different device:
  - Join      : customers scan a QR code, join with their phone number, watch their live position
  - Staff     : counter staff call the next person, mark no-shows, add walk-ins, set priority
  - Display   : a "Now serving" board for a TV / projector in the waiting area
  - Analytics : today's numbers plus busy hours and wait times from past days

Run:  streamlit run app.py
Open a screen directly with  ?view=join | staff | display | analytics
"""
from io import BytesIO
from urllib.parse import urlsplit

import pandas as pd
import qrcode
import streamlit as st

from queue_core import CANCELLED, DONE, NO_SHOW, SERVING, WAITING, QueueSystem, clean_phone

st.set_page_config(page_title="dashQ", page_icon="🕒", layout="wide")


@st.cache_resource
def get_system() -> QueueSystem:
    """One shared queue for every visitor, stored in dashq.db."""
    return QueueSystem("dashq.db")


qs = get_system()


def fmt_wait(minutes: int) -> str:
    return "You're next" if minutes <= 0 else f"~{minutes} min"


def qr_png(url: str) -> bytes:
    buf = BytesIO()
    qrcode.make(url, box_size=8, border=2).save(buf, format="PNG")
    return buf.getvalue()


def join_url() -> str:
    """
    Link the QR code opens. Uses the address set in Settings if there is one,
    otherwise the address this page was opened at (works on Streamlit Cloud).
    """
    base = qs.get_setting("public_url").strip()
    if not base:
        url = st.context.url or "http://localhost:8501"
        base = urlsplit(url)._replace(path="", query="", fragment="").geturl()
    return base.rstrip("/") + "/?view=join"


# =============================================================================
# Join (customer) screen
# =============================================================================
def join_view():
    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.title(f"🕒 {qs.business_name}")
        st.caption("Join the queue from your phone. No app, no sign-up.")

        ticket = qs.get(st.session_state.get("ticket_id"))
        if ticket is not None and ticket.status in (WAITING, SERVING):
            live_ticket_panel()
            return

        if ticket is not None:
            msg = {
                DONE: ("success", "You've been served. Thank you!"),
                CANCELLED: ("info", "You left the queue."),
                NO_SHOW: ("warning", "You missed your turn. Join again if you still need to be seen."),
            }.get(ticket.status)
            if msg:
                getattr(st, msg[0])(msg[1])

        tab_join, tab_find = st.tabs(["Join the queue", "Already joined?"])
        with tab_join:
            with st.form("join"):
                phone = st.text_input("Mobile number", placeholder="98765 43210")
                name = st.text_input("Name (optional)")
                if st.form_submit_button("Get my token", type="primary", width="stretch"):
                    t, err = qs.join(phone, name)
                    if err:
                        st.error(err)
                    else:
                        st.session_state.ticket_id = t.id
                        st.rerun()
        with tab_find:
            with st.form("find"):
                phone = st.text_input("Mobile number you joined with")
                if st.form_submit_button("Find my token", width="stretch"):
                    t = qs.find_active(clean_phone(phone) or "")
                    if t:
                        st.session_state.ticket_id = t.id
                        st.rerun()
                    else:
                        st.error("No active token found for that number.")


@st.fragment(run_every=5)
def live_ticket_panel():
    qs.process_reminders()
    t = qs.get(st.session_state.get("ticket_id"))
    if t is None or t.status not in (WAITING, SERVING):
        st.rerun(scope="app")
        return

    st.markdown(f"<div class='token'>#{t.token}</div><div class='token-label'>your token</div>",
                unsafe_allow_html=True)

    if t.status == SERVING:
        st.success(f"🎉 It's your turn! Please go to **counter {t.counter}**.")
        st.balloons() if not st.session_state.get(f"cheered{t.id}") else None
        st.session_state[f"cheered{t.id}"] = True
        return

    pos = qs.position(t)
    wait = qs.wait_minutes(t)
    c1, c2 = st.columns(2)
    c1.metric("People ahead of you", pos - 1)
    c2.metric("Estimated wait", fmt_wait(wait))
    st.progress(1 / pos, text=f"Position {pos} in line")

    if wait <= qs.reminder_lead:
        st.warning("⏰ Your turn is coming up. Please head back now!")
    else:
        st.info(f"You can step away. We'll text +91 {t.phone} when you're "
                f"about {qs.reminder_lead} minutes from your turn.")
    if t.priority:
        st.caption("⭐ You've been given priority.")

    if st.button("Leave the queue"):
        qs.cancel(t.id)
        st.rerun(scope="app")


# =============================================================================
# Staff screen
# =============================================================================
def staff_view():
    st.title("🗂️ Staff dashboard")
    staff_panel()
    with st.sidebar:
        st.divider()
        settings_panel()


@st.fragment(run_every=5)
def staff_panel():
    qs.process_reminders()
    waiting = qs.waiting()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Waiting", len(waiting))
    m2.metric("Being served", len(qs.serving()))
    m3.metric("Avg. service time", f"{qs.avg_service_minutes():.1f} min")
    m4.metric("Wait for a new person",
              f"{round((len(waiting) // qs.counters + 1) * qs.avg_service_minutes())} min"
              if waiting else "0 min")

    # One card per counter
    st.subheader("Counters")
    cols = st.columns(qs.counters)
    for i, col in enumerate(cols, start=1):
        with col.container(border=True):
            current = qs.serving_at(i)
            st.markdown(f"**Counter {i}**")
            if current:
                star = "⭐ " if current.priority else ""
                st.markdown(f"<div class='counter-token'>#{current.token}</div>", unsafe_allow_html=True)
                st.caption(f"{star}{current.name} · +91 {current.phone} · "
                           f"called {current.called_at:%H:%M}")
            else:
                st.markdown("<div class='counter-token idle'>—</div>", unsafe_allow_html=True)
                st.caption("Free")
            if st.button("▶ Next", key=f"next{i}", type="primary", width="stretch"):
                if not qs.call_next(i):
                    qs.complete(i)
                    st.toast("Nobody is waiting.")
                st.rerun(scope="app")
            b1, b2, b3 = st.columns(3)
            if b1.button("✔ Done", key=f"done{i}", disabled=current is None, width="stretch"):
                qs.complete(i)
                st.rerun(scope="app")
            if b2.button("🔔 Recall", key=f"recall{i}", disabled=current is None, width="stretch"):
                qs.recall(i)
                st.toast(f"Recalled token #{current.token}")
            if b3.button("🚫 No-show", key=f"ns{i}", disabled=current is None, width="stretch"):
                qs.no_show(i)
                st.rerun(scope="app")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Waiting line")
        if not waiting:
            st.write("No one is waiting.")
        for pos, t in enumerate(waiting, start=1):
            c1, c2, c3, c4, c5 = st.columns([1, 4, 2, 1, 1])
            c1.markdown(f"**{pos}**")
            star = "⭐ " if t.priority else ""
            c2.markdown(f"{star}**#{t.token}** {t.name}  \n+91 {t.phone}")
            c3.markdown(f"{fmt_wait(qs.wait_minutes(t))}  \njoined {t.joined_at:%H:%M}")
            if c4.button("⭐", key=f"p{t.id}", help="Toggle priority (elderly, emergency, ...)"):
                qs.toggle_priority(t.id)
                st.rerun(scope="app")
            if c5.button("✖", key=f"x{t.id}", help="Remove from queue"):
                qs.cancel(t.id)
                st.rerun(scope="app")

    with right:
        with st.container(border=True):
            st.markdown("**Add a walk-in**")
            with st.form("walkin", clear_on_submit=True):
                phone = st.text_input("Mobile number")
                name = st.text_input("Name (optional)")
                prio = st.checkbox("Priority")
                if st.form_submit_button("Add to queue", width="stretch"):
                    t, err = qs.join(phone, name, prio)
                    if err:
                        st.error(err)
                    else:
                        st.toast(f"Added token #{t.token}")
                        st.rerun(scope="app")

        with st.expander("📱 SMS outbox (simulated)", expanded=True):
            msgs = qs.outbox(15)
            if not msgs:
                st.write("No messages yet.")
            for m in msgs:
                st.caption(f"{m['sent_at'][11:]} → +91 {m['phone']}")
                st.text(m["body"])


def settings_panel():
    st.markdown("### ⚙️ Settings")
    name = st.text_input("Business name", qs.business_name)
    counters = st.number_input("Number of counters", 1, 6, qs.counters)
    avg = st.number_input("Starting avg. minutes per person", 1, 120, int(qs.get_setting("avg_minutes")),
                          help="Used until 3 people have been served; after that dashQ learns the real speed.")
    lead = st.number_input("Text customers this many minutes before their turn", 1, 60, qs.reminder_lead)
    url = st.text_input("Public app address (for the QR code)", qs.get_setting("public_url"),
                        placeholder="blank = this page's address",
                        help="Leave blank on Streamlit Cloud. When running on your laptop, use its "
                             "Wi-Fi IP, e.g. http://192.168.1.5:8501, so phones can join.")
    if st.button("Save settings", width="stretch"):
        for k, v in [("business_name", name), ("counters", counters), ("avg_minutes", avg),
                     ("reminder_lead", lead), ("public_url", url)]:
            qs.set_setting(k, v)
        st.rerun()

    st.markdown("### 🧪 Demo tools")
    if st.button("Load 2 weeks of sample history", width="stretch"):
        n = qs.seed_demo_history()
        st.toast(f"Added {n} past visits")
    if st.button("Add 5 random people to the queue", width="stretch"):
        import random
        names = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Sara", "Vihaan", "Anaya", "Ishaan", "Zoya"]
        for _ in range(5):
            qs.join(f"9{random.randint(100000000, 999999999)}", random.choice(names))
        st.rerun()
    if st.button("⚠️ Clear everything", width="stretch"):
        qs.reset_all()
        st.session_state.pop("ticket_id", None)
        st.rerun()


# =============================================================================
# Display board (TV in the waiting area)
# =============================================================================
def display_view():
    st.markdown(f"<h1 style='text-align:center'>{qs.business_name}</h1>", unsafe_allow_html=True)
    board, qr = st.columns([3, 1])
    with board:
        display_board()
    with qr:
        st.image(qr_png(join_url()), caption="Scan to join the queue", width="stretch")


@st.fragment(run_every=3)
def display_board():
    serving = {t.counter: t for t in qs.serving()}
    cols = st.columns(qs.counters)
    for i, col in enumerate(cols, start=1):
        t = serving.get(i)
        col.markdown(
            f"<div class='board-card'><div class='board-label'>Counter {i}</div>"
            f"<div class='board-token'>{'#' + str(t.token) if t else '—'}</div></div>",
            unsafe_allow_html=True,
        )
    upcoming = qs.waiting()[:6]
    st.markdown("<div class='board-label' style='margin-top:1.5rem'>Up next</div>", unsafe_allow_html=True)
    if upcoming:
        st.markdown(" ".join(f"<span class='chip'>#{t.token}</span>" for t in upcoming),
                    unsafe_allow_html=True)
    else:
        st.markdown("<span class='chip idle'>No one waiting</span>", unsafe_allow_html=True)


# =============================================================================
# Analytics
# =============================================================================
def analytics_view():
    st.title("📊 Analytics")
    s = qs.today_stats()
    c = st.columns(6)
    c[0].metric("Joined today", s["joined"])
    c[1].metric("Served", s["served"])
    c[2].metric("Still waiting", s["waiting"])
    c[3].metric("No-shows", s["no_shows"])
    c[4].metric("Avg. wait", f"{s['avg_wait']:.1f} min" if s["avg_wait"] is not None else "—")
    c[5].metric("Avg. service", f"{s['avg_service']:.1f} min" if s["avg_service"] is not None else "—")

    hist = qs.history()
    if len(hist) < 5:
        st.info("Not enough data yet. Serve some people, or use **Staff → Demo tools → "
                "Load 2 weeks of sample history**.")
        return

    df = pd.DataFrame([t.__dict__ for t in hist])
    df["date"] = df["joined_at"].dt.date
    df["hour"] = df["joined_at"].dt.hour
    df["weekday"] = df["joined_at"].dt.day_name()
    df["wait_min"] = (df["called_at"] - df["joined_at"]).dt.total_seconds() / 60
    # averages use only finished days, so a half-finished "today" doesn't drag them down
    past = df[df["date"] < qs.clock().date()]
    if past.empty:
        past = df
    days = past["date"].nunique()

    left, right = st.columns(2)
    with left:
        st.subheader("When are we busiest?")
        st.caption("Average people arriving per hour of the day")
        per_hour = past.groupby("hour").size() / days
        st.bar_chart(per_hour.rename("people / hour"))
    with right:
        st.subheader("How long do people wait?")
        st.caption("Average minutes from joining to being called, by hour")
        st.line_chart(past.groupby("hour")["wait_min"].mean().rename("avg wait (min)"))

    left, right = st.columns(2)
    with left:
        st.subheader("Visitors per day")
        per_day = df.groupby("date").size()
        per_day.index = [d.strftime("%d %b") for d in per_day.index]
        st.bar_chart(per_day.rename("visitors"), sort=False)
    with right:
        st.subheader("Busiest days of the week")
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        per_wd = past.groupby("weekday")["date"].agg(lambda d: len(d) / d.nunique()).reindex(order).dropna()
        per_wd.index = [d[:3] for d in per_wd.index]
        st.bar_chart(per_wd.rename("avg visitors"), sort=False)

    peak = per_hour.idxmax()
    st.success(f"**Insight:** the busiest hour is **{peak}:00–{peak + 1}:00** with about "
               f"{per_hour.max():.1f} arrivals. Opening an extra counter then would cut waits the most.")

    with st.expander("Raw data"):
        st.dataframe(df[["token", "name", "status", "counter", "joined_at", "called_at", "finished_at"]]
                     .sort_values("joined_at", ascending=False), width="stretch")
        st.download_button("Download CSV", df.to_csv(index=False), "dashq_history.csv", "text/csv")


# =============================================================================
# Styling + router
# =============================================================================
st.markdown("""
<style>
.token {font-size: 5rem; font-weight: 800; text-align: center; line-height: 1;}
.token-label {text-align: center; opacity: .6; margin-bottom: 1rem;}
.counter-token {font-size: 3rem; font-weight: 800;}
.idle {opacity: .35;}
.board-card {border: 2px solid rgba(128,128,128,.35); border-radius: 16px; padding: 1.5rem; text-align: center;}
.board-label {font-size: 1.4rem; opacity: .7; text-transform: uppercase; letter-spacing: .08em;}
.board-token {font-size: 6rem; font-weight: 900; line-height: 1.1;}
.chip {display: inline-block; font-size: 2rem; font-weight: 700; padding: .3rem 1rem; margin: .3rem;
       border-radius: 999px; background: rgba(128,128,128,.18);}
</style>
""", unsafe_allow_html=True)

VIEWS = {"Join": join_view, "Staff": staff_view, "Display": display_view, "Analytics": analytics_view}
param = st.query_params.get("view", "join").capitalize()
default = list(VIEWS).index(param) if param in VIEWS else 0

with st.sidebar:
    st.markdown("## 🕒 dashQ")
    mode = st.radio("Screen", list(VIEWS), index=default)
    st.query_params["view"] = mode.lower()
    st.caption("Open each screen on a different device or browser tab to see it update live.")
    if mode == "Join":
        st.image(qr_png(join_url()), caption=join_url())

VIEWS[mode]()
