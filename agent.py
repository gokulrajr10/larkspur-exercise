"""Larkspur disruption agent. This is the file you build.

It runs right now, and it is wrong in four places. The trace shows each one
before the code does, so read the trace first:

    python3 run.py K7PQ2M --trace

Where you edit:   grep -n '✏' agent.py   (six marks, one per place)
Steps and gates:  https://anthropicpartnerbasecamp.bts.com/
"""
from __future__ import annotations
from typing import Any, Dict, List
from support import (MODEL, SYSTEM_PROMPT, call_local, execute_tool, mcp_client,
                     new_session, next_available_day, record_tool_result,
                     runtime_preamble)

MAX_TOOL_CALLS = 8  # Larkspur's own build capped the loop here; then a human takes over.

TONE_ADDENDUM = ""                       # ✏️ Build 4, step 4.1, intelligence goal
# ── Build 2, step 2.1: the functions behind departures_in_window and ground_stop_status.
# Both read Larkspur's own files (flights.csv, and "now" from bookings.json) and say
# so when the answer is not there. Neither one makes anything up.
import re
from datetime import datetime, timedelta
from support.mock_backend import load_bookings, load_flights

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_STOP_WINDOW = re.compile(r"ground stop\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*([A-Z]{3})?", re.I)


def _fixture_now() -> datetime:
    """The moment the sample data treats as 'now' (bookings.json, fixture_clock), Denver local."""
    return datetime.fromisoformat(load_bookings()["fixture_clock"])


def departures_in_window(airport: str, start_time: str = "00:00", end_time: str = "23:59",
                         date: str = "") -> Dict[str, Any]:
    airport = str(airport).strip().upper()
    date = date or _fixture_now().strftime("%Y-%m-%d")   # Larkspur's "today", not this laptop's
    if not _ISO_DATE.match(str(date)):
        return {"error": "date must be YYYY-MM-DD, got %s" % date}
    for t in (start_time, end_time):
        if not _HHMM.match(str(t)):
            return {"error": "start_time and end_time must be 24-hour HH:MM local, got %s" % t}

    rows = [r for r in load_flights() if r["origin"] == airport and r["date"] == date]
    if not rows:
        return {"airport": airport, "date": date, "departures": [],
                "note": "No Larkspur departures from %s on %s in flights.csv." % (airport, date)}

    now = _fixture_now()
    # The data's clock is Denver local, and flights.csv has no time zones, so
    # "already left" can only be judged honestly for Denver.
    judge_departed = airport == "DEN" and date == now.strftime("%Y-%m-%d")
    win_start = datetime.fromisoformat("%sT%s" % (date, start_time.zfill(5)))
    win_end = datetime.fromisoformat("%sT%s" % (date, end_time.zfill(5)))

    leaving, cancelled, already_left = [], [], []
    for r in rows:
        sched = datetime.fromisoformat("%sT%s" % (date, r["sched_dep_local"].zfill(5)))
        expected = sched + timedelta(minutes=r["delay_min"])
        if not (win_start <= expected <= win_end or win_start <= sched <= win_end):
            continue
        if r["status"] == "CANCELLED":
            cancelled.append({"flight_no": r["flight_no"], "dest": r["dest"],
                              "scheduled_dep": r["sched_dep_local"], "remarks": r["remarks"]})
            continue
        if not (win_start <= expected <= win_end):
            continue  # its real departure time now falls outside the window
        if judge_departed and expected < now.replace(tzinfo=None):
            already_left.append(r["flight_no"])
            continue
        leaving.append({
            "flight_no": r["flight_no"], "dest": r["dest"], "status": r["status"],
            "scheduled_dep": r["sched_dep_local"],
            "expected_dep": expected.strftime("%H:%M") + ("" if expected.date() == sched.date() else " (+1 day)"),
            "delay_min": r["delay_min"],
            "seats_left_economy": r["seats_left_Y"], "seats_left_business": r["seats_left_J"],
            "remarks": r["remarks"],
        })
    leaving.sort(key=lambda f: f["expected_dep"])
    return {
        "airport": airport, "date": date, "window_local": "%s-%s" % (start_time, end_time),
        "now": now.isoformat(),
        "still_leaving": leaving,
        "cancelled_in_window": cancelled,
        "already_departed": already_left,
        "note": ("Times are local to %s. expected_dep is the schedule plus the current delay "
                 "and can still move. Seat counts are what is open now, not a hold." % airport)
                + ("" if judge_departed else
                   " 'Now' is only known in Denver time, so flights here are not filtered "
                   "for having already left."),
    }


def ground_stop_status(airport: str, date: str = "") -> Dict[str, Any]:
    airport = str(airport).strip().upper()
    now = _fixture_now()
    date = date or now.strftime("%Y-%m-%d")
    if not _ISO_DATE.match(str(date)):
        return {"error": "date must be YYYY-MM-DD, got %s" % date}

    # The code must name the stop itself ("DEN ground stop", "DEN GDP then ground stop"),
    # not just appear in the same remark ("held at BNA (DEN ground stop)" is Denver's).
    mentions = re.compile(r"\b%s\s+(?:GDP\s+then\s+)?ground stop" % re.escape(airport), re.I)
    evidence = [r for r in load_flights()
                if r["date"] == date and r["remarks"] and mentions.search(r["remarks"])]
    if not evidence:
        return {"airport": airport, "date": date, "now": now.isoformat(), "ground_stop_reported": False,
                "note": "No ground stop at %s on %s is recorded in flights.csv. That is not a "
                        "confirmation that operations are normal; check each flight's status." % (airport, date)}

    window = None
    for r in evidence:
        m = _STOP_WINDOW.search(r["remarks"])
        if m:
            window = {"start": m.group(1), "end": m.group(2), "tz": m.group(3) or "local",
                      "source_flight": r["flight_no"]}
            break

    if window is None:
        state = "reported, but no start or end time is published in Larkspur's data"
    elif date != now.strftime("%Y-%m-%d") or airport != "DEN":
        state = "reported for %s-%s %s; whether it is still on cannot be judged from Denver's clock" % (
            window["start"], window["end"], window["tz"])
    else:
        t = now.strftime("%H:%M")
        if t < window["start"].zfill(5):
            state = "scheduled, not started yet"
        elif t <= window["end"].zfill(5):
            state = "active now, published end %s %s" % (window["end"], window["tz"])
        else:
            state = "ended at %s %s" % (window["end"], window["tz"])

    return {
        "airport": airport, "date": date, "now": now.isoformat(), "ground_stop_reported": True,
        "state": state, "window": window,
        "evidence": [{"flight_no": r["flight_no"], "route": "%s-%s" % (r["origin"], r["dest"]),
                      "status": r["status"], "remarks": r["remarks"]} for r in evidence],
        "note": "From flight remarks in OpsFeed (flights.csv). A published end time can be "
                "extended; flights held by the stop may leave later than the stop ends.",
    }


EXTRA_TOOLS: List[Dict[str, Any]] = [   # ✏️ Build 2, step 2.1: schemas for the tools you add
    {
        "name": "departures_in_window",
        "description": (
            "List every Larkspur flight leaving one airport within a stretch of local time, today "
            "unless told otherwise (it reads Larkspur's own 'today' itself; do not pass the date "
            "for today), with its expected departure (schedule plus current delay), status and open seats "
            "per cabin, plus which flights in that window are cancelled or have already left. Use it "
            "when the customer asks what is still leaving, what else goes out, or what flies from an "
            "airport this morning/afternoon/evening, without naming one flight. Turn their words "
            "into times yourself before calling (e.g. 'this afternoon' = 12:00-18:00). Different "
            "from get_flight_status (one named flight) and search_alternatives (rebooking options "
            "for the customer's own disrupted segment): this is the whole departure board, not a "
            "rebooking, and listing a flight here does not mean it is a valid option for them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport": {"type": "string", "description": "Three-letter IATA code of the departure airport, e.g. DEN"},
                "date": {"type": "string", "description": "Only for a day other than today: local date YYYY-MM-DD. Leave out for today."},
                "start_time": {"type": "string", "description": "Start of the window, 24-hour local HH:MM, e.g. 12:00"},
                "end_time": {"type": "string", "description": "End of the window, 24-hour local HH:MM, e.g. 18:00"},
            },
            "required": ["airport", "start_time", "end_time"],
        },
    },
    {
        "name": "ground_stop_status",
        "description": (
            "Say whether an airport is under a ground stop (no departures allowed, arrivals held), "
            "and if Larkspur's data publishes it, the stop's start and end time and whether it is "
            "active right now. It reads 'now' itself; never pass a time. Use it when the customer "
            "asks if an airport is stopped, closed or shut down, why nothing is moving, or when it "
            "will reopen. Different from get_flight_status, which is one flight's own delay: a "
            "ground stop is the airport-wide cause behind many delays. If it reports no stop, say "
            "that none is on record, not that the airport is operating normally."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "airport": {"type": "string", "description": "Three-letter IATA code of the airport, e.g. DEN"},
                "date": {"type": "string", "description": "Optional local date YYYY-MM-DD; leave out for today"},
            },
            "required": ["airport"],
        },
    },
]
LOCAL_TOOLS: Dict[str, Any] = {          # ✏️ Build 2, step 2.1: the functions behind them
    "departures_in_window": departures_in_window,
    "ground_stop_status": ground_stop_status,
}


def text_of(response) -> str:
    """Given. The last non-empty text block, never content[0]."""
    texts = [b.text for b in response.content if getattr(b, "type", None) == "text" and b.text]
    return texts[-1] if texts else ""


def tool_results(response) -> List[Dict[str, Any]]:
    """Given. Runs every tool_use block and packages the results the way the
    API expects them back. A tool can live in three places: the MCP server,
    LOCAL_TOOLS, or support/tools.py."""
    # three branches, no try/except in this file: mcp_client.call_remote() and
    # support.call_local() answer with an error dict instead of raising, and both
    # record what came back on the trace
    results = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if block.name in mcp_client.tool_names:
            output = mcp_client.call_remote(block.name, block.input)
        elif block.name in LOCAL_TOOLS:
            output = call_local(LOCAL_TOOLS[block.name], block.name, block.input)
        else:
            output = execute_tool(block.name, block.input)
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": str(output),
        })
    return results


def run_agent(pnr: str, last_name: str, message: str) -> str:            # ✏️ Build 1, step 1.2
    """Run the tool loop until Claude stops asking for tools. Return its final text."""
    client, tracer = new_session()
    tools = tool_list()
    messages = [
        {"role": "user", "content": f"PNR {pnr}, last name {last_name}. {message}"},
    ]

    response = client.messages.create(
        model=MODEL, max_tokens=4096, system=runtime_preamble() + SYSTEM_PROMPT + TONE_ADDENDUM,
        thinking={"type": "adaptive"}, tools=tools, messages=messages,
    )

    answer = ""
    turns = 1
    while response.stop_reason == "tool_use" and turns < MAX_TOOL_CALLS:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results(response)})
        response = client.messages.create(
            model=MODEL, max_tokens=4096, system=runtime_preamble() + SYSTEM_PROMPT + TONE_ADDENDUM,
            thinking={"type": "adaptive"}, tools=tools, messages=messages,
        )
        turns += 1

    answer = text_of(response)
    return answer


def tool_list() -> List[Dict[str, Any]]:                   # ✏️ Build 2, step 2.2
    """Given. Exactly what Claude is offered on every turn; run.py --show-tools
    prints this list."""
    return build_tools() + EXTRA_TOOLS + mcp_client.tools()


# ──────────────────────────────────────────────────────────────────────────────
# Below this line: what Claude is told about each tool. Step 1.3.
# The functions these describe are written and correct, in support/tools.py.
# ──────────────────────────────────────────────────────────────────────────────
def build_tools() -> List[Dict[str, Any]]:                 # ✏️ Build 1, step 1.3
    """Anthropic-shaped schemas: name, description, input_schema. What Claude is
    told about each of the nine tools, and all it is ever told."""
    return [
        {
            "name": "lookup_booking",
            "description": (
                "Retrieve a Larkspur reservation from Altura by confirmation code (PNR) "
                "and the passenger's last name. Both are required to prevent a lookup on "
                "a guessed PNR. Returns fare family, loyalty tier, the segment that needs "
                "attention, and any group/partner/minor/SSR flags relevant to scope."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}, "last_name": {"type": "string"}},
                "required": ["pnr", "last_name"],
            },
        },
        {
            "name": "get_flight_status",
            "description": (
                "Look up a Larkspur or Larkspur Link flight's current OpsFeed status for "
                "one local date: status, delay minutes, and cause. Use this before telling "
                "a customer anything about a flight's timing; never state it from memory."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "flight_no": {"type": "string"},
                    "date": {"type": "string", "description": "Local flight date in ISO format YYYY-MM-DD, e.g. 2025-05-08"},
                },
                "required": ["flight_no", "date"],
            },
        },
        {
            "name": "search_alternatives",
            "description": (
                "Find replacement flights for the customer's disrupted segment, looked up "
                "from the booking by PNR (origin, destination, date and cabin are not "
                "asked of you). Call it after lookup_booking has shown the segment is "
                "cancelled or badly delayed and the customer needs another flight. Returns "
                "ranked options, each with an option_id, flights, times, seats left and any "
                "delay. Pass an option_id to hold_seat or check_policy; never invent one."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}},
                "required": ["pnr"],
            },
        },
        {
            "name": "check_policy",
            "description": (
                "Resolve what Larkspur owes this customer for the disruption: rebooking "
                "waiver, refund path, meal/hotel/ground care, goodwill eligibility and cap, "
                "and any escalation triggers. cause_code, delay_minutes and status describe "
                "what get_flight_status told you; fare_family, loyalty_tier and whether this "
                "is overnight are looked up from the booking, not asked of you. Every "
                "response carries a policy_row_id. Cite it if you reference this decision "
                "again."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string"},
                    "cause_code": {"type": "string", "enum": ["WX", "ATC", "MX", "CREW", "SEC"]},
                    "delay_minutes": {"type": "integer"},
                    "status": {"type": "string", "enum": ["ON_TIME", "DELAYED", "CANCELLED", "DIVERTED"]},
                    "wait_minutes_for_alternative": {"type": "integer"},
                    "chosen_option_id": {"type": "string"},
                },
                "required": ["pnr", "cause_code", "delay_minutes", "status"],
            },
        },
        {
            "name": "hold_seat",
            "description": "Place a 15-minute hold on one alternative. Reversible. It simply expires.",
            "input_schema": {
                "type": "object",
                "properties": {"option_id": {"type": "string"}, "pnr": {"type": "string"}},
                "required": ["option_id", "pnr"],
            },
        },
        {
            "name": "confirm_rebooking",
            "description": (
                "Finalize a held seat. Irreversible. Requires a confirmation_token that "
                "only the customer's own Confirm-click can produce. You cannot supply it "
                "yourself, and 'the customer said yes' in chat does not substitute for it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"hold_id": {"type": "string"}, "confirmation_token": {"type": "string"}},
                "required": ["hold_id", "confirmation_token"],
            },
        },
        {
            "name": "issue_voucher",
            "description": (
                "Issue a meal, ground, hotel, or goodwill voucher. Auto-approves within the "
                "policy's threshold for that type; above it, returns a pending status for a "
                "human. It does not fail. Always pass the policy_row_id that made it eligible."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "voucher_type": {"type": "string", "enum": ["meal", "ground", "hotel", "goodwill"]},
                    "amount_usd": {"type": "number"},
                    "pnr": {"type": "string"},
                    "policy_row_id": {"type": "string"},
                },
                "required": ["voucher_type", "amount_usd", "pnr", "policy_row_id"],
            },
        },
        {
            "name": "escalate_to_human",
            "description": (
                "Hand this conversation to a human, with your reasoning attached. Use for "
                "groups, partner segments, unaccompanied minors, refunds, or anything else "
                "out of scope. This is the correct outcome for those cases, not a failure."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pnr": {"type": "string"}, "reason": {"type": "string"},
                    "summary_for_human": {"type": "string"}, "queue": {"type": "string"},
                },
                "required": ["pnr", "reason", "summary_for_human"],
            },
        },
        {
            "name": "send_confirmation",
            "description": "Send the customer a written confirmation of what was just done. Benign.",
            "input_schema": {
                "type": "object",
                "properties": {"pnr": {"type": "string"}, "message": {"type": "string"}},
                "required": ["pnr", "message"],
            },
        },
    ]
