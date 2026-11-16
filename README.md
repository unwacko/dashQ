# dashQ

A queue management system written in Python. Instead of standing in a line, people scan a QR code, join the queue from their phone, and get a message when their turn is close. Staff call people from a dashboard, a screen in the waiting room shows who is being served, and an analytics page shows when the place gets busy.

This is our first-year engineering project.

---

## The problem

Anyone who has waited at a clinic, the college admin office, a bank or a ration shop knows the drill. You take a token or stand in line, and then you can't leave, because you have no idea when you'll be called. Lines get crowded, people argue about who came first, and elderly people stand for a long time.

We wanted something that:

- lets people wait **anywhere** instead of in the line itself
- tells them **how long** they'll roughly have to wait
- **reminds** them when it's time to come back
- is easy for staff to use, with nothing for customers to install

---

## Try it

You need Python 3.10 or newer.

```bash
git clone https://github.com/unwacko/dashQ.git
cd dashQ
pip install -r requirements.txt
streamlit run app.py
```

It opens in your browser at `http://localhost:8501`. Use the sidebar to switch between the four screens:

| Screen | Link | Used by |
|---|---|---|
| Join | `/?view=join` | Customers, on their phone |
| Staff | `/?view=staff` | People at the counters |
| Display | `/?view=display` | A TV or projector in the waiting area |
| Analytics | `/?view=analytics` | The manager |

Tip: open the Staff screen in one browser window and the Join screen in another (or on your phone). Press "Next" on the Staff screen and watch the customer's screen update.

To fill the analytics page with data, go to **Staff → Demo tools → Load 2 weeks of sample history**. This is randomly generated sample data, not real visits.

---

## What we used

| Tool | What it's for | Why we picked it |
|---|---|---|
| **Python 3** | Everything | It's the language for our course, and the one we know best |
| **Streamlit** | The web interface | We could build web pages in pure Python, with no HTML/JavaScript to learn first |
| **SQLite** (`sqlite3`) | Storing tickets, messages and settings | It comes with Python, needs no server, and the whole database is one file |
| **pandas** | Analytics (grouping visits by hour and day) | It makes "average arrivals per hour" a one-line calculation |
| **qrcode** | Generating the "scan to join" QR code | Small library that does exactly one thing |
| **pytest** | Automated tests for the queue logic | Lets us change code without being scared of breaking it |
| **Git + GitHub** | Version control and sharing code within the team | |
| **Streamlit Community Cloud** | Hosting the app online for free | Deploys straight from GitHub in a couple of clicks |
| **Canva** | Our presentation slides | |

---

## How we built it, step by step

**1. Planning.** We picked a clinic waiting room as our example, since it's the case most people have experienced. We listed what the customer needs to see and what the staff need to do, and made our first slides in Canva.

**2. First prototype.** Our first version was a single file, `app.py`. The queue was just a Python list in memory, with one counter and a fixed "5 minutes per person" estimate. It worked, but everything disappeared whenever the app restarted.

**3. Separating logic from the interface.** We moved all the queue rules into their own file, `queue_core.py`, which has no Streamlit code at all. `app.py` only draws the screens. This made the logic much easier to test and reason about.

**4. Adding a database.** We replaced the in-memory list with SQLite. There are three tables: `tickets`, `messages` (the text message log) and `settings`. Now a restart doesn't lose the queue, and we keep the history we need for analytics.

**5. Ticket life cycle.** Every ticket goes through these states:

```
waiting ──"Next"──▶ serving ──"Done"──▶ done
   │                    └──"No-show"──▶ no_show
   └──"Leave queue"────────────────────▶ cancelled
```

The order is: **priority** tickets first (elderly, emergencies), then **first come, first served**.

**6. Multiple counters and a better wait estimate.** Real places usually have more than one desk, so we added counters. Each counter calls its own next person. For someone at position `p`, with `c` counters, `f` of them free, and an average service time `s`:

```
ahead = p - 1 - f
wait  = 0                       if ahead < 0     (a counter is free for them)
      = (ahead // c + 1) × s    otherwise
```

For example, with 2 counters both busy and 5 minutes per person, positions 1 and 2 wait about 5 minutes, and positions 3 and 4 wait about 10.

**7. Learning how fast the desk actually is.** A fixed "5 minutes" is a guess. Once 3 people have been served, the app switches to the real average service time of the last 10 people served, so the estimates improve as the day goes on.

**8. Reminders.** When someone gets within N minutes of their turn (10 by default), they get a reminder, exactly once. Right now these messages are saved to the database and shown in an outbox on the Staff screen instead of being sent as real SMS (see "Limitations").

**9. Display board and QR code.** We added a "Now serving" screen for a TV, with big token numbers per counter, the next few tokens, and a QR code that opens the Join page.

**10. Analytics.** Using pandas we added charts for busiest hours, average wait by hour, visitors per day and busiest weekdays, plus a CSV download. The page also points out the peak hour, which is when opening an extra counter helps the most.

**11. Testing.** We wrote 17 tests in `tests/test_queue_core.py` covering phone number validation, ordering, priority, no-shows, the wait formula with one and two counters, reminders and daily stats. The tests use a fake clock so we can "fast-forward" time instead of waiting.

**12. Putting it online.** We host it on Streamlit Community Cloud. While getting it ready, we realised the server runs on UTC, which would have shown every time 5½ hours off. We fixed it by always converting to India time (`Asia/Kolkata`). We also made the QR code use whatever address the app is opened at, so it works online and on a laptop without changing anything.

---

## Project structure

```
dashQ/
├── app.py                  # the four screens (Streamlit)
├── queue_core.py           # queue engine: tickets, ordering, wait times, reminders, stats
├── tests/
│   └── test_queue_core.py  # pytest tests for the engine
├── requirements.txt
└── README.md
```

## Running the tests

```bash
pytest
```

---

## Putting it online yourself

1. Push the code to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **Create app** → **Deploy a public app from GitHub**.
4. Choose the repository `unwacko/dashQ`, branch `main`, main file `app.py`.
5. Pick an address (for example `dashq.streamlit.app`) and click **Deploy**.

Things to know about the free tier: the app goes to sleep after a few days without visitors, and the database file is reset when it restarts. Before a demo, open the app a few minutes early to wake it up.

**Using it on a laptop with phones on the same Wi-Fi:** find your laptop's IP address (`ipconfig` on Windows, `ifconfig` on Mac/Linux) and open `http://<your-ip>:8501` on the phone. If the QR code shows the wrong address, set it under **Staff → Settings → Public app address**.

---

## Limitations

- **Text messages are simulated.** They go to the outbox on the Staff screen. Sending real SMS needs a paid provider such as Twilio or MSG91, plus Indian DLT registration. The only place that would change is the `notify()` function in `queue_core.py`.
- **One queue per app.** A hospital with several departments would need several queues.
- **No login for staff.** Anyone with the link can open the Staff screen.

## What we'd like to do next

- Real SMS, and an automated phone call when it's your turn
- "Call to join" by phone, for people without smartphones
- Predicting busy times from past data and suggesting when to open more counters
- Staff login

---

## Team

<!-- add your names, roll numbers and roles here -->

| Name | Role |
|---|---|
| | |
| | |
| | |
