"""
dashQ - a queue management system (Python + Streamlit + SQLite)

Four screens, each can be opened on a different device:
  - Join      (/)          : customers scan a QR code, join with their phone number, watch their live position
  - Staff     (/staff)     : counter staff call the next person, mark no-shows, add walk-ins, set priority
  - Display   (/display)   : a "Now serving" board for a TV / projector in the waiting area
  - Analytics (/analytics) : today's numbers plus busy hours and wait times from past days

Run:  streamlit run app.py
"""
import random
from html import escape
from io import BytesIO
from urllib.parse import urlsplit

import pandas as pd
import qrcode
import streamlit as st

from queue_core import CANCELLED, DONE, NO_SHOW, SERVING, WAITING, QueueSystem, clean_phone

st.set_page_config(page_title="dashQ", page_icon="static/icon.png", layout="wide")


@st.cache_resource
def get_system() -> QueueSystem:
    """One shared queue for every visitor, stored in dashq.db."""
    return QueueSystem("dashq.db")


qs = get_system()

# =============================================================================
# Styling
# =============================================================================
CSS = """
<style>
:root {
  --ink: #1F1F1F; --muted: #6B6B76; --line: #E8E8EE; --card: #FFFFFF;
  --navy: #2E3060; --navy-2: #1E1F3B; --indigo: #3B3FB6;
}

/* soft orange + periwinkle glow behind the top of every page */
.stApp::before {
  content: ""; position: fixed; left: 50%; top: -42vh; transform: translateX(-50%);
  width: 130vw; height: 85vh; pointer-events: none; z-index: 0; opacity: var(--glow, 1);
  background:
    radial-gradient(closest-side at 50% 58%, rgba(249,115,22,.85), rgba(251,146,60,.35) 45%, transparent 75%),
    radial-gradient(closest-side at 22% 62%, rgba(129,140,248,.55), transparent 80%),
    radial-gradient(closest-side at 78% 62%, rgba(129,140,248,.55), transparent 80%);
  filter: blur(36px);
}
[data-testid="stMain"], [data-testid="stHeader"] { position: relative; z-index: 1; }
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1180px; padding-top: 3.5rem; padding-bottom: 4rem; }

/* typography */
.display { font-family: "Newsreader", serif; font-weight: 400; letter-spacing: -0.025em;
           color: var(--ink); line-height: 1.04; margin: 0; }
.display.xl { font-size: clamp(2.6rem, 6vw, 4.4rem); }
.display.lg { font-size: clamp(2.1rem, 4.2vw, 3.2rem); }
.display.md { font-size: clamp(1.6rem, 2.6vw, 2.1rem); }
.lead { color: #3F3F46; font-size: 1.15rem; line-height: 1.6; margin: .9rem auto 0 !important; max-width: 34rem; }
.hero:not(.center) .lead { margin-left: 0 !important; }
.eyebrow { font-size: .74rem; font-weight: 500; letter-spacing: .18em; text-transform: uppercase;
           color: var(--muted); margin-bottom: .55rem; }
.muted { color: var(--muted); }
.hero { margin: 1.5rem 0 2.4rem; }
.hero.center { text-align: center; }
.tagline { display: inline-block; color: var(--indigo); font-weight: 500; padding: .5rem 1.4rem; margin-bottom: 1.4rem;
           border-top: 1px solid; border-bottom: 1px solid;
           border-image: linear-gradient(90deg, transparent, rgba(59,63,182,.35), transparent) 1; }
.section-title { margin: 2.6rem 0 1rem; }

/* cards: any container whose key starts with "card" */
[class*="st-key-card"] { background: var(--card); border: 1px solid var(--line); border-radius: 18px;
                         padding: 1.4rem 1.5rem; box-shadow: 0 1px 2px rgba(16,16,24,.03); }

/* buttons */
[data-testid^="stBaseButton-primary"] { background: linear-gradient(180deg, var(--navy), var(--navy-2));
  border: none; color: #fff; font-weight: 500; box-shadow: 0 6px 16px -8px rgba(30,31,59,.6); }
[data-testid^="stBaseButton-primary"]:hover { filter: brightness(1.18); color: #fff; }
[data-testid^="stBaseButton-secondary"], [data-testid="stPopoverButton"] { background: #fff; font-weight: 500; }

/* tabs as a segmented pill */
.stTabs [role="tablist"] { gap: .25rem; background: #F1F1F4; padding: 4px; border-radius: 999px;
                           width: fit-content; border: none; box-shadow: none; margin-bottom: .6rem; }
.stTabs [data-testid="stTab"] { border-radius: 999px; padding: .4rem 1.1rem; height: auto; color: var(--muted); }
.stTabs [data-testid="stTab"][aria-selected="true"] { background: #fff; color: var(--ink);
                                                       box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.stTabs [data-testid="stTab"] p { font-size: .92rem; font-weight: 500; }
.stTabs .react-aria-SelectionIndicator { display: none; }

/* status chips */
.chip { display: inline-flex; align-items: center; gap: .4rem; padding: .28rem .8rem; border-radius: 999px;
        font-size: .82rem; font-weight: 500; border: 1px solid; white-space: nowrap; }
.chip.blue   { background: #EEF0FF; color: #3B3FB6; border-color: #D9DCFB; }
.chip.green  { background: #EAF7EE; color: #1F7A45; border-color: #CDEBD7; }
.chip.amber  { background: #FFF6E5; color: #A35F00; border-color: #F6DFB0; }
.chip.pink   { background: #FDEEF4; color: #BE2F6B; border-color: #F6CFE0; }
.chip.orange { background: #FFF1E7; color: #C2410C; border-color: #FBD3BC; }
.chip.gray   { background: #F4F4F6; color: #52525B; border-color: #E4E4EA; }

/* stat strip */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); background: var(--card);
         border: 1px solid var(--line); border-radius: 18px; overflow: hidden; }
.stat { padding: 1.1rem 1.4rem; box-shadow: -1px 0 0 var(--line); }
.stat-label { font-size: .82rem; color: var(--muted); }
.stat-value { font-family: "Newsreader", serif; font-size: 2.1rem; letter-spacing: -.02em; line-height: 1.2; color: var(--ink); }

/* label / value rows */
.kv { display: flex; justify-content: space-between; align-items: baseline; padding: .8rem 0; border-bottom: 1px solid var(--line); }
.kv:last-child { border-bottom: none; }
.kv .k { color: var(--muted); }
.kv .v { font-family: "Newsreader", serif; font-size: 1.5rem; color: var(--ink); }

/* token numbers */
.token { font-family: "Newsreader", serif; font-size: 5.5rem; line-height: 1; letter-spacing: -.04em; color: var(--ink); }
.counter-token { font-family: "Newsreader", serif; font-size: 3.4rem; line-height: 1.05; letter-spacing: -.03em; color: var(--ink); }
.idle { color: #C4C4CC; }

/* waiting list rows */
.row-pos { font-family: "Newsreader", serif; font-size: 1.5rem; color: var(--muted); }
.row-main { line-height: 1.45; }
.row-main b { font-weight: 600; }

/* sparkle bullets */
.spark { color: #22A06B; margin-right: .5rem; }
.insight { display: flex; gap: .8rem; align-items: flex-start; }

/* display board */
.board { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }
.board-card { border-radius: 22px; padding: 1.6rem 1.8rem; color: #fff; min-height: 220px;
              display: flex; flex-direction: column; justify-content: space-between;
              box-shadow: 0 18px 40px -24px rgba(30,31,59,.55); }
.board-card .label { font-size: .8rem; letter-spacing: .2em; text-transform: uppercase; opacity: .9; font-weight: 500; }
.board-card .num { font-family: "Newsreader", serif; font-size: clamp(4.5rem, 8vw, 7rem); line-height: 1; letter-spacing: -.04em; }
.board-card .who { font-size: .95rem; opacity: .9; }
.g0 { background: linear-gradient(135deg, #A78BFA, #6366F1 70%); }
.g1 { background: linear-gradient(135deg, #FDBA74, #EA580C 75%); }
.g2 { background: linear-gradient(135deg, #86EFAC, #16A34A 75%); }
.g3 { background: linear-gradient(135deg, #93C5FD, #2563EB 75%); }
.g4 { background: linear-gradient(135deg, #F9A8D4, #DB2777 75%); }
.g5 { background: linear-gradient(135deg, #5EEAD4, #0D9488 75%); }
.next-chip { display: inline-block; font-family: "Newsreader", serif; font-size: 2rem; padding: .25rem 1.1rem;
             margin: .3rem .4rem 0 0; border-radius: 999px; background: #fff; border: 1px solid var(--line); }

.st-key-card-insight, .st-key-card-empty { margin-top: .75rem; }
.footer { text-align: center; color: #A1A1AA; font-size: .85rem; margin-top: 4rem; }
[data-testid="stImageCaption"] { color: var(--muted); }
</style>
"""


def style(glow: float = 1.0):
    st.markdown(CSS + f"<style>.stApp{{--glow:{glow}}}</style>", unsafe_allow_html=True)


def html(s: str):
    st.markdown(s, unsafe_allow_html=True)


def hero(eyebrow: str, title: str, lead: str = "", center: bool = True, size: str = "xl", tagline: bool = True):
    cls = "hero center" if center else "hero"
    top = f'<div class="tagline">{eyebrow}</div>' if tagline else f'<div class="eyebrow">{eyebrow}</div>'
    sub = f'<p class="lead">{lead}</p>' if lead else ""
    html(f'<div class="{cls}">{top}<div class="display {size}">{title}</div>{sub}</div>')


def section(eyebrow: str, title: str):
    html(f'<div class="section-title"><div class="eyebrow">{eyebrow}</div><div class="display md">{title}</div></div>')


def stats(items):
    cells = "".join(f'<div class="stat"><div class="stat-label">{k}</div><div class="stat-value">{v}</div></div>'
                    for k, v in items)
    html(f'<div class="stats">{cells}</div>')


def chip(text: str, color: str = "gray") -> str:
    return f'<span class="chip {color}">{text}</span>'


def footer():
    html('<div class="footer">dashQ · a Python mini-project</div>')


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
    return base.rstrip("/") + "/"


def queue_summary() -> str:
    n = len(qs.waiting())
    if n == 0:
        return chip("● No queue right now", "green")
    wait = round((n // qs.counters + 1) * qs.avg_service_minutes())
    return chip(f"● {n} waiting · about {wait} min", "blue")


# =============================================================================
# Join (customer) screen
# =============================================================================
def join_page():
    style(glow=1)
    hero(escape(qs.business_name), "Skip the line,<br>not your turn.",
         "Join the queue from your phone. No app, no sign-up. We'll text you when it's almost your turn.")

    _, mid, _ = st.columns([1, 1.5, 1])
    with mid:
        ticket = qs.get(st.session_state.get("ticket_id"))
        if ticket is not None and ticket.status in (WAITING, SERVING):
            live_ticket_panel()
            footer()
            return

        if ticket is not None:
            msg = {
                DONE: chip("✓ You've been served. Thank you!", "green"),
                CANCELLED: chip("You left the queue", "gray"),
                NO_SHOW: chip("You missed your turn. Join again if you still need to be seen.", "amber"),
            }.get(ticket.status)
            if msg:
                html(f'<div style="text-align:center;margin-bottom:1rem">{msg}</div>')

        html(f'<div style="text-align:center;margin-bottom:1rem">{queue_summary()}</div>')
        with st.container(key="card-join"):
            tab_join, tab_find = st.tabs(["Join the queue", "Already joined?"])
            with tab_join:
                with st.form("join", border=False):
                    phone = st.text_input("Mobile number", placeholder="98765 43210")
                    name = st.text_input("Name (optional)", placeholder="So staff can greet you")
                    if st.form_submit_button("Get my token", type="primary", width="stretch"):
                        t, err = qs.join(phone, name)
                        if err:
                            st.error(err)
                        else:
                            st.session_state.ticket_id = t.id
                            st.rerun()
            with tab_find:
                with st.form("find", border=False):
                    phone = st.text_input("Mobile number you joined with", placeholder="98765 43210")
                    if st.form_submit_button("Find my token", width="stretch"):
                        t = qs.find_active(clean_phone(phone) or "")
                        if t:
                            st.session_state.ticket_id = t.id
                            st.rerun()
                        else:
                            st.error("No active token found for that number.")
    footer()


@st.fragment(run_every=5)
def live_ticket_panel():
    qs.process_reminders()
    t = qs.get(st.session_state.get("ticket_id"))
    if t is None or t.status not in (WAITING, SERVING):
        st.rerun(scope="app")
        return

    with st.container(key="card-ticket"):
        if t.status == SERVING:
            html(f'<div style="text-align:center"><div class="eyebrow">Your token</div>'
                 f'<div class="token">#{t.token}</div>'
                 f'<div style="margin:1rem 0 .4rem">{chip("✓ It is your turn", "green")}</div>'
                 f'<div class="display md">Please go to counter {t.counter}</div></div>')
            if not st.session_state.get(f"cheered{t.id}"):
                st.balloons()
                st.session_state[f"cheered{t.id}"] = True
            return

        pos = qs.position(t)
        wait = qs.wait_minutes(t)
        near = wait <= qs.reminder_lead
        status = chip("⏰ Head back now", "amber") if near else chip("✓ You can step away", "blue")
        prio = " " + chip("★ Priority", "pink") if t.priority else ""
        html(f'<div style="text-align:center"><div class="eyebrow">Your token</div>'
             f'<div class="token">#{t.token}</div>'
             f'<div style="margin:.9rem 0 .6rem">{status}{prio}</div></div>')
        html(f'<div class="kv"><span class="k">People ahead of you</span><span class="v">{pos - 1}</span></div>'
             f'<div class="kv"><span class="k">Estimated wait</span><span class="v">{fmt_wait(wait)}</span></div>'
             f'<div class="kv"><span class="k">Counters open</span><span class="v">{qs.counters}</span></div>')
        st.progress(1 / pos)
        if near:
            html('<p class="muted" style="margin:.2rem 0 1rem">Your turn is coming up. Please make your way back.</p>')
        else:
            html(f'<p class="muted" style="margin:.2rem 0 1rem">We\'ll text +91 {t.phone} when you\'re about '
                 f'{qs.reminder_lead} minutes away. This page updates by itself.</p>')
        if st.button("Leave the queue", width="stretch"):
            qs.cancel(t.id)
            st.rerun(scope="app")


# =============================================================================
# Staff screen
# =============================================================================
def staff_page():
    style(glow=0.45)
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        hero("Staff dashboard", escape(qs.business_name), center=False, size="lg", tagline=False)
    with right:
        b1, b2 = st.columns(2)
        with b1.popover("⚙ Settings", width="stretch"):
            settings_panel()
        with b2.popover("✦ Demo tools", width="stretch"):
            demo_panel()
    staff_panel()
    footer()


@st.fragment(run_every=5)
def staff_panel():
    qs.process_reminders()
    waiting = qs.waiting()
    new_wait = round((len(waiting) // qs.counters + 1) * qs.avg_service_minutes()) if waiting else 0
    stats([
        ("Waiting", len(waiting)),
        ("Being served", len(qs.serving())),
        ("Avg. service time", f"{qs.avg_service_minutes():.1f} min"),
        ("Wait for a new person", f"{new_wait} min"),
    ])

    section("Counters", "Who's at the desk")
    cols = st.columns(qs.counters)
    for i, col in enumerate(cols, start=1):
        with col, st.container(key=f"card-counter-{i}"):
            current = qs.serving_at(i)
            html(f'<div class="eyebrow">Counter {i}</div>')
            if current:
                star = " " + chip("★ Priority", "pink") if current.priority else ""
                html(f'<div class="counter-token">#{current.token}</div>'
                     f'<div class="muted" style="margin:.2rem 0 1rem">{escape(current.name)} · +91 {current.phone} · '
                     f'called {current.called_at:%H:%M}{star}</div>')
            else:
                html('<div class="counter-token idle">—</div>'
                     '<div class="muted" style="margin:.2rem 0 1rem">Free</div>')
            if st.button("Call next", key=f"next{i}", type="primary", width="stretch"):
                if not qs.call_next(i):
                    qs.complete(i)
                    st.toast("Nobody is waiting.")
                st.rerun(scope="app")
            c1, c2, c3 = st.columns(3)
            if c1.button("Done", key=f"done{i}", disabled=current is None, width="stretch"):
                qs.complete(i)
                st.rerun(scope="app")
            if c2.button("Recall", key=f"recall{i}", disabled=current is None, width="stretch"):
                qs.recall(i)
                st.toast(f"Recalled token #{current.token}")
            if c3.button("No-show", key=f"ns{i}", disabled=current is None, width="stretch"):
                qs.no_show(i)
                st.rerun(scope="app")

    section("Queue", "Waiting line")
    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(key="card-line"):
        if not waiting:
            html('<p class="muted" style="margin:0">No one is waiting.</p>')
        for pos, t in enumerate(waiting, start=1):
            c1, c2, c3, c4, c5 = st.columns([0.6, 4, 2.2, 0.8, 0.8], vertical_alignment="center")
            c1.markdown(f'<span class="row-pos">{pos}</span>', unsafe_allow_html=True)
            star = " " + chip("★ Priority", "pink") if t.priority else ""
            c2.markdown(f'<div class="row-main"><b>#{t.token}</b> {escape(t.name)}{star}<br>'
                        f'<span class="muted">+91 {t.phone}</span></div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="row-main">{fmt_wait(qs.wait_minutes(t))}<br>'
                        f'<span class="muted">joined {t.joined_at:%H:%M}</span></div>', unsafe_allow_html=True)
            if c4.button("★", key=f"p{t.id}", help="Toggle priority (elderly, emergency, ...)"):
                qs.toggle_priority(t.id)
                st.rerun(scope="app")
            if c5.button("✕", key=f"x{t.id}", help="Remove from queue"):
                qs.cancel(t.id)
                st.rerun(scope="app")

    with right:
        with st.container(key="card-walkin"):
            html('<div class="eyebrow">Add a walk-in</div>')
            with st.form("walkin", clear_on_submit=True, border=False):
                phone = st.text_input("Mobile number")
                name = st.text_input("Name (optional)")
                prio = st.checkbox("Priority (elderly, emergency)")
                if st.form_submit_button("Add to queue", type="primary", width="stretch"):
                    t, err = qs.join(phone, name, prio)
                    if err:
                        st.error(err)
                    else:
                        st.toast(f"Added token #{t.token}")
                        st.rerun(scope="app")

        with st.container(key="card-outbox"):
            html('<div class="eyebrow">SMS outbox · simulated</div>')
            msgs = qs.outbox(8)
            if not msgs:
                html('<p class="muted" style="margin:0">No messages yet.</p>')
            for m in msgs:
                html(f'<div class="kv" style="display:block;padding:.6rem 0">'
                     f'<div class="muted" style="font-size:.8rem">{m["sent_at"][11:16]} → +91 {m["phone"]}</div>'
                     f'<div style="font-size:.92rem">{escape(m["body"])}</div></div>')


def settings_panel():
    name = st.text_input("Business name", qs.business_name)
    counters = st.number_input("Number of counters", 1, 6, qs.counters)
    avg = st.number_input("Starting avg. minutes per person", 1, 120, int(qs.get_setting("avg_minutes")),
                          help="Used until 3 people have been served; after that dashQ learns the real speed.")
    lead = st.number_input("Text customers this many minutes before their turn", 1, 60, qs.reminder_lead)
    url = st.text_input("Public app address (for the QR code)", qs.get_setting("public_url"),
                        placeholder="blank = this page's address",
                        help="Leave blank on Streamlit Cloud. When running on your laptop, use its "
                             "Wi-Fi IP, e.g. http://192.168.1.5:8501, so phones can join.")
    if st.button("Save settings", type="primary", width="stretch"):
        for k, v in [("business_name", name), ("counters", counters), ("avg_minutes", avg),
                     ("reminder_lead", lead), ("public_url", url)]:
            qs.set_setting(k, v)
        st.rerun()


def demo_panel():
    if st.button("Load 2 weeks of sample history", width="stretch"):
        n = qs.seed_demo_history()
        st.toast(f"Added {n} past visits")
    if st.button("Add 5 random people to the queue", width="stretch"):
        names = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Sara", "Vihaan", "Anaya", "Ishaan", "Zoya"]
        for _ in range(5):
            qs.join(f"9{random.randint(100000000, 999999999)}", random.choice(names))
        st.rerun()
    if st.button("Clear everything", width="stretch"):
        qs.reset_all()
        st.session_state.pop("ticket_id", None)
        st.rerun()


# =============================================================================
# Display board (TV in the waiting area)
# =============================================================================
def display_page():
    style(glow=0.9)
    hero("Now serving", escape(qs.business_name), size="lg")
    board, qr = st.columns([3, 1], gap="large")
    with board:
        display_board()
    with qr, st.container(key="card-qr"):
        html('<div class="eyebrow" style="text-align:center">Scan to join</div>')
        st.image(qr_png(join_url()), width="stretch")
        html('<p class="muted" style="text-align:center;margin:0;font-size:.9rem">No app needed. '
             'We\'ll text you when it\'s your turn.</p>')


@st.fragment(run_every=3)
def display_board():
    serving = {t.counter: t for t in qs.serving()}
    cards = ""
    for i in range(1, qs.counters + 1):
        t = serving.get(i)
        num = f"#{t.token}" if t else "—"
        who = "Please come to the counter" if t else "Free"
        cards += (f'<div class="board-card g{(i - 1) % 6}"><div class="label">Counter {i}</div>'
                  f'<div class="num">{num}</div><div class="who">{who}</div></div>')
    html(f'<div class="board">{cards}</div>')

    upcoming = qs.waiting()[:6]
    html('<div class="eyebrow" style="margin-top:2rem">Up next</div>')
    if upcoming:
        html("".join(f'<span class="next-chip">#{t.token}</span>' for t in upcoming))
    else:
        html('<span class="next-chip idle">No one waiting</span>')


# =============================================================================
# Analytics
# =============================================================================
def analytics_page():
    style(glow=0.45)
    hero("Analytics", "How the queue is doing", center=False, size="lg", tagline=False)
    s = qs.today_stats()
    stats([
        ("Joined today", s["joined"]),
        ("Served", s["served"]),
        ("Still waiting", s["waiting"]),
        ("No-shows", s["no_shows"]),
        ("Avg. wait", f"{s['avg_wait']:.1f} min" if s["avg_wait"] is not None else "—"),
        ("Avg. service", f"{s['avg_service']:.1f} min" if s["avg_service"] is not None else "—"),
    ])

    hist = qs.history()
    if len(hist) < 5:
        with st.container(key="card-empty"):
            html('<div class="insight"><span class="spark">✦</span><div>Not enough data yet. Serve some people, '
                 'or open <b>Staff → Demo tools → Load 2 weeks of sample history</b>.</div></div>')
        footer()
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
    per_hour = past.groupby("hour").size() / days

    peak = per_hour.idxmax()
    with st.container(key="card-insight"):
        html(f'<div class="insight"><span class="spark">✦</span><div><b>Busiest hour: {peak}:00–{peak + 1}:00</b>'
             f'<br><span class="muted">About {per_hour.max():.1f} people arrive in that hour. Opening an extra '
             f'counter then would cut waiting times the most.</span></div></div>')

    section("Patterns", "When people come, and how long they wait")
    left, right = st.columns(2, gap="medium")
    with left, st.container(key="card-chart-hour"):
        html('<div class="eyebrow">Arrivals per hour</div>')
        st.bar_chart(per_hour.rename("people / hour"), color="#6C72E8", height=280)
    with right, st.container(key="card-chart-wait"):
        html('<div class="eyebrow">Average wait by hour (min)</div>')
        st.line_chart(past.groupby("hour")["wait_min"].mean().rename("avg wait (min)"), color="#F97316", height=280)

    left, right = st.columns(2, gap="medium")
    with left, st.container(key="card-chart-day"):
        html('<div class="eyebrow">Visitors per day</div>')
        per_day = df.groupby("date").size()
        per_day.index = [d.strftime("%d %b") for d in per_day.index]
        st.bar_chart(per_day.rename("visitors"), sort=False, color="#6C72E8", height=280)
    with right, st.container(key="card-chart-weekday"):
        html('<div class="eyebrow">Average visitors by weekday</div>')
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        per_wd = past.groupby("weekday")["date"].agg(lambda d: len(d) / d.nunique()).reindex(order).dropna()
        per_wd.index = [d[:3] for d in per_wd.index]
        st.bar_chart(per_wd.rename("avg visitors"), sort=False, color="#22A06B", height=280)

    with st.expander("Raw data"):
        st.dataframe(df[["token", "name", "status", "counter", "joined_at", "called_at", "finished_at"]]
                     .sort_values("joined_at", ascending=False), width="stretch")
        st.download_button("Download CSV", df.to_csv(index=False), "dashq_history.csv", "text/csv")
    footer()


# =============================================================================
# Navigation
# =============================================================================
PAGES = {
    "join": st.Page(join_page, title="Join", default=True),
    "staff": st.Page(staff_page, title="Staff", url_path="staff"),
    "display": st.Page(display_page, title="Display", url_path="display"),
    "analytics": st.Page(analytics_page, title="Analytics", url_path="analytics"),
}
st.logo("static/logo.png", size="large")
nav = st.navigation(list(PAGES.values()), position="top")

# old links like /?view=staff still work
old = st.query_params.get("view")
if old in PAGES:
    del st.query_params["view"]
    st.switch_page(PAGES[old])

nav.run()
