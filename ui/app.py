"""
Verdict, the Agentic SLA Auditor: operations console.

Pages: Overview, Audits, Review queue, Telemetry, Activity.
Run with:  streamlit run ui/app.py
"""
import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from ui.seed_users import DEFAULT_PASSWORD  # noqa: E402
from core.present import credit_text, plain_reason  # noqa: E402
from ui.users import ROLE_LABEL, authenticate, can, load_users  # noqa: E402
RESULTS_PATH = BASE / "audit_results.json"
RUN_INFO_PATH = BASE / "audit_run.json"


def _run_info():
    try:
        return json.loads(RUN_INFO_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


RUN_INFO = _run_info()
DATA_DIR = Path(RUN_INFO.get("data_dir", BASE))
CONTRACTS_PATH = DATA_DIR / "contracts.json"
TELEMETRY_PATH = DATA_DIR / "telemetry_logs.csv"
LEDGER_DIR = BASE / "ledger" / ("v1" if DATA_DIR == BASE else DATA_DIR.name)

st.set_page_config(page_title="Verdict | Agentic SLA Auditor", page_icon="V", layout="wide", initial_sidebar_state="expanded")

st.session_state.setdefault("authenticated", False)
st.session_state.setdefault("page", "Overview")
st.session_state.setdefault("run_message", "")
st.session_state.setdefault("open_contract", None)
st.session_state.setdefault("account", None)

# ------------------------------------------------------------------ styling
BLUE, BLUE_DK, YELLOW = "#27509B", "#1B3A78", "#FCE500"
INK, MUTED, LINE, CANVAS, WHITE = "#1B2430", "#5F6B7A", "#DDE1E8", "#F5F6F8", "#FFFFFF"
GREEN, AMBER, RED = "#1A7F4B", "#A85A00", "#B42318"

st.markdown(
    f"""
<style>
html, body, .stApp, [data-testid="stAppViewContainer"] {{
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 14px; color: {INK};
    font-variant-numeric: tabular-nums;
}}
.stApp {{ background: {CANVAS}; }}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer {{ display: none !important; }}
.block-container {{ max-width: 1360px; padding: 76px 28px 40px; }}
h1, h2, h3, h4 {{ color: {INK}; letter-spacing: 0; }}
p, label, span, li {{ color: inherit; }}

/* top bar */
.topbar {{
    position: fixed; top: 0; left: 0; right: 0; height: 52px; z-index: 1000000;
    display: flex; align-items: center; gap: 14px; padding: 0 18px;
    background: {WHITE}; border-bottom: 1px solid {LINE};
}}
.mark {{ width: 28px; height: 28px; border-radius: 4px; background: {BLUE}; display: flex; align-items: center;
         justify-content: center; border-bottom: 4px solid {YELLOW}; color: #fff; font-weight: 700; font-size: 14px; }}
.topbar .name {{ font-weight: 600; font-size: 15px; }}
.topbar .sep {{ width: 1px; height: 22px; background: {LINE}; }}
.topbar .env {{ color: {MUTED}; font-size: 13px; }}
.topbar .grow {{ flex: 1; }}
.topbar .user {{ display: flex; align-items: center; gap: 8px; font-size: 13px; }}
.avatar {{ width: 26px; height: 26px; border-radius: 50%; background: #E7ECF6; color: {BLUE};
           display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; }}

/* left navigation */
[data-testid="stSidebar"] {{ background: {WHITE}; border-right: 1px solid {LINE}; top: 52px; height: calc(100vh - 52px);
    transform: none !important; margin-left: 0 !important; min-width: 244px !important; width: 244px !important; }}
[data-testid="stSidebarContent"] {{ padding: 22px 10px 16px; }}
[data-testid="stSidebarCollapseButton"], [data-testid="stSidebarHeader"] {{ display: none; }}
[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"],
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {{
    justify-content: flex-start; text-align: left; width: 100%; min-height: 36px; padding: 0 12px; margin: 1px 0;
    border: 0; border-radius: 4px; font-weight: 500; font-size: 14px; box-shadow: none;
}}
[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] {{ background: transparent; color: {INK}; }}
[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover {{ background: {CANVAS}; }}
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {{
    background: #E9EFFA; color: {BLUE_DK}; border-left: 3px solid {BLUE}; font-weight: 600; border-radius: 0 4px 4px 0;
}}
.nav-group {{ color: {MUTED}; font-size: 12px; margin: 14px 12px 8px; }}

[data-testid="stSidebar"] button p {{ text-align: left; width: 100%; }}
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"],
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover,
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:focus {{ background: #E9EFFA !important; color: {BLUE_DK} !important; }}
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] p {{ color: {BLUE_DK} !important; font-weight: 600; }}
[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] p {{ color: {INK}; }}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.15rem; }}
[data-testid="stSidebar"] button > div {{ justify-content: flex-start; width: 100%; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ background: {WHITE}; border-radius: 6px; border-color: {LINE}; }}
h3.ph {{ font-size: 15px; font-weight: 600; margin: 0 0 6px; padding: 0; }}

.usercard {{ margin: 18px 4px 6px; padding: 10px 12px; border: 1px solid {LINE}; border-radius: 6px; font-size: 13px; }}
.usercard b {{ font-size: 14px; }}
.rolechip {{ display: inline-block; margin-top: 4px; padding: 1px 8px; border-radius: 10px; background: #E9EFFA; color: {BLUE_DK};
             font-size: 12px; font-weight: 600; }}
.mix {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #EEF0F3; margin: 8px 0 10px; }}
.mix span {{ display: block; height: 100%; }}
.legend {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: 13px; color: {MUTED}; }}
.legend i {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }}

/* page header */
.crumbs {{ color: {MUTED}; font-size: 13px; margin-bottom: 4px; }}
h1.page-title {{ font-size: 22px !important; line-height: 1.3 !important; font-weight: 600 !important; margin: 0 0 2px !important; padding: 0 !important; }}
.page-sub {{ color: {MUTED}; margin: 0 0 18px; }}

/* surfaces */
.panel {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 6px; padding: 16px 18px; }}
.panel h3 {{ font-size: 15px; font-weight: 600; margin: 0 0 10px; }}
.stats {{ display: flex; background: {WHITE}; border: 1px solid {LINE}; border-radius: 6px; margin-bottom: 18px; }}
.stat {{ flex: 1; padding: 14px 18px; border-right: 1px solid {LINE}; }}
.stat:last-child {{ border-right: 0; }}
.stat .n {{ font-size: 24px; font-weight: 600; line-height: 1.2; }}
.stat .l {{ color: {MUTED}; font-size: 13px; }}
.facts {{ display: grid; grid-template-columns: 190px 1fr; row-gap: 9px; column-gap: 12px; margin: 0; }}
.facts dt {{ color: {MUTED}; }}
.facts dd {{ margin: 0; word-break: break-word; }}
.chip {{ display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px 2px 7px; border-radius: 4px; font-size: 12.5px;
         font-weight: 600; border: 1px solid transparent; }}
.chip i {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; }}
.chip.ok {{ background: #E8F5EE; color: {GREEN}; border-color: #BFE3CE; }} .chip.ok i {{ background: {GREEN}; }}
.chip.warn {{ background: #FFF3E0; color: {AMBER}; border-color: #F1D3A6; }} .chip.warn i {{ background: {AMBER}; }}
.chip.bad {{ background: #FDECEA; color: {RED}; border-color: #F3C2BD; }} .chip.bad i {{ background: {RED}; }}
.callout {{ border-left: 3px solid {BLUE}; background: #F1F5FC; padding: 10px 14px; border-radius: 0 4px 4px 0; }}
.muted {{ color: {MUTED}; }}
mark {{ background: #FFF2A8; color: {INK}; padding: 0 2px; }}
.signin {{ max-width: 380px; margin: 9vh auto 0; }}
.signin .panel {{ padding: 26px 26px 22px; }}

/* controls */
button[data-testid="stBaseButton-primary"] {{ background: {BLUE}; border-color: {BLUE}; color: #fff; border-radius: 4px; }}
button[data-testid="stBaseButton-primary"]:hover {{ background: {BLUE_DK}; border-color: {BLUE_DK}; }}
button[data-testid="stBaseButton-secondary"] {{ border-radius: 4px; border-color: {LINE}; }}
[data-testid="stMetric"] {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 6px; padding: 10px 14px; }}
:focus-visible {{ outline: 2px solid {BLUE}; outline-offset: 2px; }}
@media (max-width: 1100px) {{
    /* side-by-side panels stack on narrower windows so tables are never cropped */
    div[data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
    div[data-testid="stColumn"] {{ min-width: 100% !important; }}
}}
@media (max-width: 900px) {{
    .block-container {{ padding: 70px 14px 30px 262px; }}  /* keep content clear of the permanent left nav */
    .stats {{ flex-wrap: wrap; }} .stat {{ flex: 1 1 45%; border-bottom: 1px solid {LINE}; }}
    .facts {{ grid-template-columns: 1fr; row-gap: 2px; }} .facts dd {{ margin-bottom: 8px; }}
    .topbar .env {{ display: none; }}
}}
</style>
""",
    unsafe_allow_html=True,
)


def topbar(account: dict = None) -> None:
    who = ""
    if account:
        first = account["name"].split()
        initials = (first[0][:1] + first[-1][:1]).upper()
        who = (f'<div class="user"><span class="avatar">{html.escape(initials)}</span>'
               f'<span>{html.escape(account["name"])}</span><span class="muted">{html.escape(ROLE_LABEL.get(account["role"], ""))}</span></div>')
    st.markdown(
        f'<div class="topbar"><div class="mark">V</div><div class="name">Verdict</div><div class="sep"></div>'
        f'<div class="env">Agentic SLA Auditor</div><div class="grow"></div>{who}</div>',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------ sign in
if not st.session_state.authenticated or not st.session_state.account:
    topbar()
    _l, mid, _r = st.columns([1, 1.5, 1])
    with mid:
        st.markdown("<div style='height:5vh'></div>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="mark" style="margin-bottom:14px;">V</div>'
                        '<h3 class="ph" style="font-size:20px;">Sign in to Verdict</h3>'
                        '<p class="muted" style="margin:0 0 6px;">Fleet service contract audits for commercial and finance teams.</p>',
                        unsafe_allow_html=True)
            # Demo convenience: choose a person and their email and password are filled in (the password stays masked).
            # Set SLA_UI_AUTOFILL=0 to turn this off for anything shared.
            people = load_users()
            fill_email, fill_pw = "", ""
            if os.getenv("SLA_UI_AUTOFILL", "1") != "0" and people:
                options = {f'{u["name"]} ({ROLE_LABEL.get(u["role"], u["role"])})': u for u in people}
                names = list(options)
                pick = st.selectbox("Sign in as", names, index=min(1, len(names) - 1), key="signin_as")
                fill_email = options[pick]["email"]
                fill_pw = os.getenv("SLA_UI_PASSWORD") or DEFAULT_PASSWORD
            with st.form("signin", border=False):
                email = st.text_input("Work email", value=fill_email, placeholder="name@company.com", key=f"email_{fill_email}")
                pw = st.text_input("Password", type="password", value=fill_pw, key=f"pw_{fill_email}")
                submit = st.form_submit_button("Sign in", type="primary", width="stretch")
                if submit:
                    account = authenticate(email, pw)
                    if account:
                        st.session_state.authenticated = True
                        st.session_state.account = account
                        st.rerun()
                    else:
                        st.error("The email or password is incorrect.")
        st.markdown('<p class="muted" style="font-size:12.5px;text-align:center;">Access is limited to authorised staff. '
                    'Sign-ins and decisions are recorded in the audit log.</p>', unsafe_allow_html=True)
    st.stop()

ACCOUNT = st.session_state.account
topbar(ACCOUNT)


# ------------------------------------------------------------------ data
def load_results():
    try:
        return pd.DataFrame(json.loads(RESULTS_PATH.read_text(encoding="utf-8")))
    except Exception:
        return pd.DataFrame()


def load_contracts():
    try:
        return json.loads(CONTRACTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def load_telemetry():
    try:
        return pd.read_csv(TELEMETRY_PATH)
    except Exception:
        return pd.DataFrame()


def get_ledger():
    from core.ledger import Ledger
    return Ledger(LEDGER_DIR)


def load_bundle(proof_id):
    try:
        return get_ledger().load_proof(proof_id)
    except Exception:
        return None


def run_audit(mock: bool, dataset: str) -> bool:
    cmd = [sys.executable, str(BASE / "harness.py")] + (["--mock"] if mock else [])
    if dataset.startswith("Generated"):
        cmd += ["--data", str(BASE / "data" / "v2")]
    with st.spinner("Running audit across all contracts"):
        proc = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True)
    ok = proc.returncode == 0
    st.session_state.run_message = (proc.stdout if ok else (proc.stderr or proc.stdout)).strip()
    return ok


df = load_results()
contracts = load_contracts()
telemetry = load_telemetry()
contract_by_id = {c.get("contract_id"): c for c in contracts}


# ------------------------------------------------------------------ helpers
STATUS_LABEL = {"COMPLIANT": ("ok", "Compliant"), "BREACH": ("bad", "Breach"), "ESCALATED": ("warn", "Needs review")}
REASON_GROUPS = [
    ("outcome depends on how the contract is read", "The contract can be read two or more ways"),
    ("telemetry rows are trusted", "Doubtful telemetry rows decide the outcome"),
    ("no usable telemetry", "No usable telemetry"),
    ("mostly unusable", "Telemetry is mostly unusable"),
    ("vague or missing", "A threshold is vague or missing"),
    ("no numeric", "No numeric threshold found"),
    ("extractor failed", "The reader failed"),
]


def chip(status: str) -> str:
    cls, label = STATUS_LABEL.get(status, ("warn", status.title()))
    return f'<span class="chip {cls}"><i></i>{label}</span>'


def money(x) -> str:
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "-"


def header(title: str, crumbs: str = "", sub: str = "") -> None:
    st.markdown(f'<div class="crumbs">{html.escape(crumbs)}</div><h1 class="page-title">{html.escape(title)}</h1>'
                f'<p class="page-sub">{html.escape(sub)}</p>', unsafe_allow_html=True)


def facts(pairs) -> None:
    body = "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in pairs)
    st.markdown(f'<dl class="facts">{body}</dl>', unsafe_allow_html=True)


def reason_group(reason: str) -> str:
    low = str(reason).lower()
    for key, label in REASON_GROUPS:
        if key in low:
            return label
    return "Other"


def envelope_frame(env) -> pd.DataFrame:
    if not env:
        return pd.DataFrame()
    terms = {r["reading_id"]: r for r in env.get("readings", [])}
    rows = []
    for c in env.get("cells", []):
        r = terms.get(c["reading_id"], {})
        up, rs = r.get("uptime_threshold"), r.get("resolution_threshold_hours")
        outcome = "Cannot be decided" if c["status"] == "UNDETERMINED" else STATUS_LABEL.get(c["status"], ("", c["status"].title()))[1]
        rows.append({
            "Reading": c["reading_id"],
            "Uptime target": "not checked" if up is None else f"{up:g}%",
            "Repair limit": "not checked" if rs is None else f"{rs:g} h",
            "Telemetry used": {"strict": "Trusted rows only", "inclusive": "Including doubtful rows"}.get(c["scenario"], c["scenario"]),
            "Fleet uptime": None if c.get("mean_uptime_percent") is None else round(c["mean_uptime_percent"], 2),
            "Slowest repair (h)": None if c.get("max_resolution_hours") is None else round(c["max_resolution_hours"], 1),
            "Outcome": outcome,
            "Credit": money(c.get("penalty_usd", 0)) if c["status"] == "BREACH" else "-",
        })
    return pd.DataFrame(rows)


def highlighted_contract(text: str, bundle) -> str:
    quotes = []
    if bundle:
        ext = bundle.get("extraction", {})
        quotes = [c["quote"] for c in ext.get("uptime_candidates", []) + ext.get("resolution_candidates", [])] + ext.get("vague_terms", [])
    out = html.escape(text)
    for q in sorted(set(quotes), key=len, reverse=True):
        eq = html.escape(q.strip())
        out = out.replace(eq, f"<mark>{eq}</mark>")
    return f'<div style="white-space:pre-wrap;max-height:360px;overflow:auto;line-height:1.55;">{out}</div>'


def style_status(frame: pd.DataFrame, col: str = "Status"):
    colours = {"Compliant": GREEN, "Breach": RED, "Needs review": AMBER}
    return frame.style.map(lambda v: f"color: {colours.get(v, INK)}; font-weight: 600;", subset=[col])


def _load_meta() -> dict:
    try:
        return json.loads((DATA_DIR / "contract_meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


META = _load_meta()


def customer_of(cid: str) -> str:
    return META.get(cid, {}).get("customer", "-")


def table_of_results(src: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "Contract": src["contract_id"],
        "Customer": src["contract_id"].map(customer_of),
        "Status": src["status"].map(lambda s: STATUS_LABEL.get(s, ("", s.title()))[1]),
        "Reason": src.apply(plain_reason, axis=1),
        "Credit": src.apply(credit_text, axis=1),
    })
    return out.reset_index(drop=True)


COLUMN_CONFIG = {
    "Contract": st.column_config.TextColumn(width="small"),
    "Customer": st.column_config.TextColumn(width="medium"),
    "Status": st.column_config.TextColumn(width="small"),
    "Reason": st.column_config.TextColumn(width="large"),
    "Credit": st.column_config.TextColumn(width="small"),
}


def go(page: str, contract: str = None) -> None:
    st.session_state.page = page
    st.session_state.open_contract = contract


# ------------------------------------------------------------------ left navigation
PAGES = ["Overview", "Audits", "Review queue", "Telemetry", "Activity"]
with st.sidebar:
    st.markdown('<div class="nav-group">Workspace</div>', unsafe_allow_html=True)
    open_count = int((df.status == "ESCALATED").sum()) if not df.empty else 0
    for p in PAGES:
        label = f"{p} ({open_count})" if p == "Review queue" and open_count else p
        if st.button(label, key=f"nav_{p}", type="primary" if st.session_state.page == p else "secondary", width="stretch"):
            go(p)
            st.rerun()

    if can(ACCOUNT["role"], "run"):
        st.markdown('<div class="nav-group">Audit run</div>', unsafe_allow_html=True)
        v2_ready = (BASE / "data" / "v2" / "contracts.json").exists()
        dataset_choice = st.selectbox("Contracts", ["Bundled set (50 contracts)"] + (["Generated set (realistic, labelled)"] if v2_ready else []))
        reader_choice = st.radio("Reader", ["Offline (pattern reader)", "AI and pattern reader"], help="The AI reader needs a key in .env.")
        if st.button("Run audit", key="run_audit", width="stretch"):
            if run_audit(reader_choice.startswith("Offline"), dataset_choice):
                go("Overview")
                st.rerun()
            else:
                st.error("The audit run failed. Details are on the Activity page.")
    st.markdown(
        f'<div class="usercard"><b>{html.escape(ACCOUNT["name"])}</b><br>'
        f'<span class="muted">{html.escape(ACCOUNT.get("title", ""))}</span><br>'
        f'<span class="rolechip">{html.escape(ROLE_LABEL.get(ACCOUNT["role"], ""))}</span></div>', unsafe_allow_html=True)
    if st.button("Sign out", key="sign_out", width="stretch"):
        st.session_state.authenticated = False
        st.session_state.account = None
        st.rerun()

page = st.session_state.page

if df.empty and page != "Activity":
    header("No audit results yet", "Overview")
    st.markdown('<div class="panel">Run an audit from the left panel to see results here.</div>', unsafe_allow_html=True)
    st.stop()


# ------------------------------------------------------------------ Overview
def page_overview():
    total = len(df)
    n_ok, n_breach, n_esc = (int((df.status == s).sum()) for s in ("COMPLIANT", "BREACH", "ESCALATED"))
    credits = float(pd.to_numeric(df.get("penalty_usd", 0), errors="coerce").fillna(0).sum())
    when = datetime.fromtimestamp(RESULTS_PATH.stat().st_mtime).strftime("%d %b %Y, %H:%M") if RESULTS_PATH.exists() else "unknown"
    first_name = ACCOUNT["name"].split()[0]
    header("Overview", "Workspace",
           f"Hello {first_name}. Last audit run {when}: {RUN_INFO.get('contracts', total)} contracts, {RUN_INFO.get('mode', 'offline')} reader.")
    st.markdown(
        '<div class="stats">'
        f'<div class="stat"><div class="n">{total}</div><div class="l">Contracts audited</div></div>'
        f'<div class="stat"><div class="n">{n_ok}</div><div class="l">Compliant</div></div>'
        f'<div class="stat"><div class="n">{n_breach}</div><div class="l">Breach</div></div>'
        f'<div class="stat"><div class="n">{n_esc}</div><div class="l">Need review</div></div>'
        f'<div class="stat"><div class="n">{(total - n_esc) / max(total, 1):.0%}</div><div class="l">Decided without a person</div></div>'
        f'<div class="stat"><div class="n">${credits:,.0f}</div><div class="l">Credits on automated breaches</div></div>'
        '</div>', unsafe_allow_html=True)

    pct = lambda n: 100.0 * n / max(total, 1)  # noqa: E731
    with st.container(border=True):
        st.markdown('<h3 class="ph">Outcome of the latest run</h3>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="mix"><span style="width:{pct(n_ok):.1f}%;background:{GREEN}"></span>'
            f'<span style="width:{pct(n_breach):.1f}%;background:{RED}"></span>'
            f'<span style="width:{pct(n_esc):.1f}%;background:{AMBER}"></span></div>'
            f'<div class="legend"><span><i style="background:{GREEN}"></i>Compliant {n_ok} ({pct(n_ok):.0f}%)</span>'
            f'<span><i style="background:{RED}"></i>Breach {n_breach} ({pct(n_breach):.0f}%)</span>'
            f'<span><i style="background:{AMBER}"></i>Needs review {n_esc} ({pct(n_esc):.0f}%)</span></div>', unsafe_allow_html=True)
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    left, right = st.columns([1.15, 1])
    with left:
        with st.container(border=True):
            st.markdown('<h3 class="ph">Waiting for review</h3>', unsafe_allow_html=True)
            q = df[df.status == "ESCALATED"].head(8)
            if q.empty:
                st.markdown('<span class="muted">Nothing is waiting for a person.</span>', unsafe_allow_html=True)
            else:
                view = pd.DataFrame({"Contract": q.contract_id, "Customer": q.contract_id.map(customer_of), "Why": q.apply(plain_reason, axis=1)})
                st.dataframe(view, hide_index=True, width="stretch", height=35 * (len(view) + 1) + 3,
                             column_config={"Contract": st.column_config.TextColumn(width="small"), "Customer": st.column_config.TextColumn(width="medium"), "Why": st.column_config.TextColumn(width="large")})
                st.button("Open review queue", on_click=go, args=("Review queue",), key="ov_queue")
    with right:
        with st.container(border=True):
            st.markdown('<h3 class="ph">Why contracts need review</h3>', unsafe_allow_html=True)
            esc = df[df.status == "ESCALATED"]
            if esc.empty:
                st.markdown('<span class="muted">No escalations in this run.</span>', unsafe_allow_html=True)
            else:
                grp = esc.reason.map(reason_group).value_counts().rename_axis("Reason").reset_index(name="Contracts")
                st.dataframe(grp, hide_index=True, width="stretch", height=35 * (len(grp) + 1) + 3, column_config={
                    "Reason": st.column_config.TextColumn(width="large"),
                    "Contracts": st.column_config.ProgressColumn(format="%d", min_value=0, max_value=int(grp["Contracts"].max()))})

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown('<h3 class="ph">Largest credits on automated breaches</h3>', unsafe_allow_html=True)
        b = df[df.status == "BREACH"].copy()
        b["amount"] = pd.to_numeric(b.get("penalty_usd", 0), errors="coerce").fillna(0)
        b = b.sort_values("amount", ascending=False).head(6)
        if b.empty:
            st.markdown('<span class="muted">No breaches in this run.</span>', unsafe_allow_html=True)
        else:
            basis = {"contract_clause": "Rate in contract", "contract_tiers": "Credit table in contract", "assumed_default_rate": "Assumed rate",
                     "not_quantifiable": "No amount available", "fee_unknown": "Monthly fee not provided", "tier_not_found": "Credit table not readable"}
            v = pd.DataFrame({"Contract": b.contract_id, "Customer": b.contract_id.map(customer_of),
                              "Credit": b.apply(credit_text, axis=1),
                              "Basis": b["penalty_basis"].map(lambda x: basis.get(x, "-")),
                              "Needs commercial review": b["needs_commercial_review"].map(lambda x: "Yes" if x else "No")})
            st.dataframe(v, hide_index=True, width="stretch")


# ------------------------------------------------------------------ contract detail (shared)
def contract_detail(row: pd.Series) -> None:
    cid = row["contract_id"]
    basis = {"contract_clause": "Rate stated in the contract", "contract_tiers": "Credit table in the contract",
             "assumed_default_rate": "Assumed rate, needs commercial review", "not_quantifiable": "No amount can be calculated",
             "fee_unknown": "Credit is a percentage of a monthly fee that was not provided",
             "tier_not_found": "The contract's credit table could not be read"}
    exposure = ""
    if row["status"] == "ESCALATED" and pd.notna(row.get("exposure_max_usd")):
        exposure = f'{money(row.get("exposure_min_usd") or 0)} to {money(row.get("exposure_max_usd"))}'
    decided = row.get("decided_by", "machine")
    with st.container(border=True):
        st.markdown(f'<h3 class="ph">{html.escape(cid)} &nbsp; {chip(row["status"])}</h3>', unsafe_allow_html=True)
        facts([
            ("Decision", html.escape(plain_reason(row))),
            ("Customer", html.escape(customer_of(cid))),
            ("Service period", html.escape(META.get(cid, {}).get("period", "-"))),
            ("Region", html.escape(META.get(cid, {}).get("region", "-"))),
            ("Credit", html.escape(credit_text(row))),
            ("Credit basis", html.escape(basis.get(row.get("penalty_basis"), "-"))),
            ("Amount at stake", exposure or "-"),
            ("Commercial review", "Required" if row.get("needs_commercial_review") else "Not required"),
            ("Decided by", html.escape("System" if decided == "machine" else str(decided))),
            ("Proof", f'<code>{html.escape(str(row.get("proof_id") or "-"))}</code>'),
        ])

    env = row.get("envelope")
    if isinstance(env, dict) and env.get("cells"):
        with st.container(border=True):
            st.markdown('<h3 class="ph">Readings of the contract and the telemetry</h3>'
                        '<p class="muted" style="margin:0 0 8px;">A verdict is applied automatically only when every row below gives the same outcome.</p>',
                        unsafe_allow_html=True)
            st.dataframe(envelope_frame(env), hide_index=True, width="stretch")
            for note in env.get("notes", []):
                st.caption(note)

    brief = row.get("brief")
    if isinstance(brief, str) and brief:
        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
        with st.expander("Review brief", expanded=True):
            st.code(brief, language=None)

    pid, citem = row.get("proof_id"), contract_by_id.get(cid)
    if isinstance(pid, str) and pid:
        bundle = load_bundle(pid)
        if bundle and citem:
            text = citem.get("contract_text") or citem.get("raw_text") or ""
            with st.expander("Contract text with the cited sentences marked"):
                st.markdown(highlighted_contract(text, bundle), unsafe_allow_html=True)
            if st.button("Check this proof", key=f"verify_{cid}_{pid}"):
                from core.verify_proof import verify_bundle
                ok, report = verify_bundle(bundle, text, telemetry)
                (st.success if ok else st.error)("The same decision is reproduced from the contract and telemetry." if ok
                                                 else "The recomputed decision does not match the sealed one.")
                st.code("\n".join(report), language=None)
    trace = row.get("execution_trace")
    if isinstance(trace, list) and trace:
        with st.expander("Processing steps"):
            for step in trace:
                st.text(str(step))


# ------------------------------------------------------------------ Audits
def page_audits():
    header("Audits", "Workspace / Audits", "Every contract in the latest run. Select a row to see how it was decided.")
    f1, f2, f3 = st.columns([1, 1, 2])
    status = f1.selectbox("Status", ["All", "Compliant", "Breach", "Needs review"])
    search = f2.text_input("Contract", placeholder="Contract ID")
    text = f3.text_input("Reason", placeholder="Search in the decision reason")
    src = df.copy()
    if status != "All":
        inv = {v[1]: k for k, v in STATUS_LABEL.items()}
        src = src[src.status == inv[status]]
    if search.strip():
        src = src[src.contract_id.astype(str).str.contains(search.strip(), case=False, na=False)]
    if text.strip():
        src = src[src.reason.astype(str).str.contains(text.strip(), case=False, na=False)]
    src = src.reset_index(drop=True)
    shown = table_of_results(src)
    cap, exp = st.columns([4, 1])
    cap.caption(f"{len(src)} of {len(df)} contracts")
    exp.download_button("Export CSV", shown.to_csv(index=False), file_name="sla_audits.csv", mime="text/csv", width="stretch")
    event = st.dataframe(style_status(shown), hide_index=True, width="stretch", height=380, column_config=COLUMN_CONFIG,
                         on_select="rerun", selection_mode="single-row", key="audits_table")
    rows = event.selection.rows if event and event.selection else []
    chosen = src.iloc[rows[0]] if rows else None
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    if chosen is None:
        st.markdown('<div class="panel muted">Select a contract above to see its decision, evidence and proof.</div>', unsafe_allow_html=True)
    else:
        contract_detail(chosen)


# ------------------------------------------------------------------ Review queue
def page_review():
    header("Review queue", "Workspace / Review queue",
           "Contracts the system would not decide on its own. Your ruling is recorded and can be replayed later.")
    queue = df[df.status == "ESCALATED"].reset_index(drop=True)
    if queue.empty:
        st.markdown('<div class="panel">Nothing is waiting for review.</div>', unsafe_allow_html=True)
        return
    left, right = st.container(), st.container()  # list above, case below: reads well at any width
    with left:
        view = pd.DataFrame({"Contract": queue.contract_id,
                             "Customer": queue.contract_id.map(customer_of),
                             "Why": queue.apply(plain_reason, axis=1),
                             "At stake": queue.get("exposure_max_usd")})
        event = st.dataframe(view, hide_index=True, width="stretch", height=300, on_select="rerun", selection_mode="single-row",
                             key="queue_table", column_config={"At stake": st.column_config.NumberColumn(format="$%.0f", width="small"),
                                            "Customer": st.column_config.TextColumn(width="medium"), "Why": st.column_config.TextColumn(width="large")})
    rows = event.selection.rows if event and event.selection else []
    with right:
        if not rows:
            st.markdown('<div class="panel muted">Select a contract on the left to review it.</div>', unsafe_allow_html=True)
            return
        row = queue.iloc[rows[0]]
        contract_detail(row)
        env = row.get("envelope") if isinstance(row.get("envelope"), dict) else {}
        reading_ids = [r["reading_id"] for r in env.get("readings", [])]
        pid = row.get("proof_id")
        with st.container(border=True):
            st.markdown('<h3 class="ph">Your ruling</h3>', unsafe_allow_html=True)
            sug = row.get("suggested_ruling")
            if isinstance(sug, dict):
                st.markdown(f'<div class="callout">Earlier rulings on this clause pattern ({sug.get("based_on_rulings")}) chose '
                            f'<b>{html.escape(str(sug.get("reading_id")))}</b>. This is a suggestion only.</div>', unsafe_allow_html=True)
            if not can(ACCOUNT["role"], "rule"):
                st.markdown('<div class="callout">Your role can view cases but cannot record rulings. '
                            'Ask a reviewer or administrator to settle this one.</div>', unsafe_allow_html=True)
            elif not reading_ids or not isinstance(pid, str):
                st.markdown('<span class="muted">There is no reading to choose between (for example, the telemetry is missing or unusable). '
                            'Correct the input data and run the audit again.</span>', unsafe_allow_html=True)
            else:
                c1, c2 = st.columns(2)
                chosen = c1.selectbox("Reading that applies", reading_ids, key=f"rd_{pid}")
                scen_label = c2.selectbox("Telemetry to rely on", ["No preference", "Trusted rows only", "Including doubtful rows"], key=f"sc_{pid}")
                note = st.text_input("Reason (kept in the audit record)", key=f"nt_{pid}")
                st.text_input("Recorded as", value=ACCOUNT["name"], disabled=True, key=f"rv_{pid}")
                reviewer = ACCOUNT["name"]
                if st.button("Save ruling", type="primary", key=f"save_{pid}"):
                    from core.pipeline import PipelineConfig, resolve_escalation
                    from core.precedents import PrecedentStore
                    cfg = PipelineConfig(ledger=get_ledger(), precedents=PrecedentStore(BASE / "ledger" / "precedents.json"))
                    scn = {"Trusted rows only": "strict", "Including doubtful rows": "inclusive"}.get(scen_label)
                    try:
                        res = resolve_escalation(cfg, pid, chosen, reviewer.strip() or "reviewer", scn, note)
                    except Exception as exc:
                        st.error(str(exc))
                    else:
                        data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
                        for i, r in enumerate(data):
                            if r["contract_id"] == row["contract_id"]:
                                data[i] = res.model_dump()
                        RESULTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        st.success(f"Ruling saved. {row['contract_id']} is now {STATUS_LABEL.get(res.status, ('', res.status))[1].lower()}.")
                        st.rerun()


# ------------------------------------------------------------------ Telemetry
def page_telemetry():
    header("Telemetry", "Workspace / Telemetry", "Vehicle events used for the audits, with data-quality checks applied.")
    if telemetry.empty:
        st.markdown('<div class="panel">No telemetry file was found for this run.</div>', unsafe_allow_html=True)
        return
    from agents.data_trust import classify_rows
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", f"{len(telemetry):,}")
    c2.metric("Contracts", int(telemetry["contract_id"].nunique()))
    faults = int((telemetry.get("sensor_status", pd.Series(dtype=str)).astype(str).str.upper() == "FAULT").sum())
    c3.metric("Sensor faults", faults)
    c4.metric("Vehicles", int(telemetry["truck_id"].nunique()) if "truck_id" in telemetry else 0)
    ids = ["All contracts"] + sorted(telemetry["contract_id"].astype(str).unique().tolist())
    pick = st.selectbox("Contract", ids)
    view = telemetry if pick == "All contracts" else telemetry[telemetry["contract_id"].astype(str) == pick]
    if pick != "All contracts" and len(view):
        counts = classify_rows(view)["_class"].value_counts()
        st.caption(f"{int(counts.get('clean', 0))} clean, {int(counts.get('suspect', 0))} doubtful, "
                   f"{int(counts.get('unusable', 0))} unusable rows for {pick}.")
    shown = view.head(2000).copy()
    if "event_start" in shown:
        shown["event_start"] = pd.to_datetime(shown["event_start"], errors="coerce", utc=True).dt.strftime("%d %b %Y %H:%M")
    for col in ("event_type", "severity", "sensor_status"):
        if col in shown:
            shown[col] = shown[col].astype(str).str.replace("_", " ").str.capitalize()
    if "planned_maintenance" in shown:
        shown["planned_maintenance"] = shown["planned_maintenance"].map(lambda v: "Yes" if str(v).lower() in ("true", "1") else "No")
    shown = shown.rename(columns={"log_id": "Log", "contract_id": "Contract", "truck_id": "Vehicle", "vehicle_class": "Class",
                                  "event_type": "Event", "severity": "Severity", "event_start": "Started", "resolution_hours": "Repair time (h)",
                                  "downtime_minutes": "Downtime (min)", "actual_uptime_percent": "Uptime (%)", "sensor_status": "Sensor",
                                  "planned_maintenance": "Planned maintenance"})
    st.dataframe(shown, hide_index=True, width="stretch", height=460, column_config={
        "Repair time (h)": st.column_config.NumberColumn(format="%.1f"), "Uptime (%)": st.column_config.NumberColumn(format="%.2f")})


# ------------------------------------------------------------------ Activity
def page_activity():
    header("Activity", "Workspace / Activity", "The audit record and how each run behaved.")
    try:
        ledger = get_ledger()
        ok, msg = ledger.verify_chain()
        entries = ledger.entries()
        st.markdown(f'<div class="callout" style="margin-bottom:14px;">{"Audit record is intact" if ok else "The audit record has been altered"}: '
                    f'{html.escape(msg)}. {len(entries)} decisions recorded.</div>', unsafe_allow_html=True)
        if entries:
            led = pd.DataFrame(entries)
            led["verdict"] = led["verdict"].map(lambda s: STATUS_LABEL.get(s, ("", str(s)))[1])
            cols = [c for c in ("seq", "ts", "event", "actor", "contract_id", "verdict", "proof_id") if c in led.columns]
            st.markdown("<h3 style='font-size:15px;'>Decisions</h3>", unsafe_allow_html=True)
            st.dataframe(led[cols].tail(100).iloc[::-1], hide_index=True, width="stretch", height=300)
    except Exception as exc:
        st.markdown(f'<div class="panel muted">No audit record for this dataset yet ({html.escape(str(exc))}).</div>', unsafe_allow_html=True)

    try:
        from core.telemetry import read_traces
        traces = read_traces(LEDGER_DIR / "traces.jsonl", limit=300)
    except Exception:
        traces = []
    st.markdown("<h3 style='font-size:15px;margin-top:18px;'>Run performance</h3>", unsafe_allow_html=True)
    if traces:
        rows = []
        for t in traces:
            sp = {s["name"]: s["duration_ms"] for s in t["spans"]}
            ai = next((s["attrs"] for s in t["spans"] if s["name"] == "read.ai"), {})
            rows.append({"Contract": t["attrs"].get("contract_id"), "Total (ms)": t["duration_ms"], "Pattern reader (ms)": sp.get("read.pattern"),
                         "AI reader (ms)": sp.get("read.ai"), "Readings": t["attrs"].get("readings"),
                         "AI calls": ai.get("gen_ai.calls"), "AI input tokens": ai.get("gen_ai.usage.input_tokens")})
        tdf = pd.DataFrame(rows)
        a, b, c = st.columns(3)
        a.metric("Audits traced", len(tdf))
        b.metric("Median audit time", f"{tdf['Total (ms)'].median():.0f} ms")
        c.metric("AI calls", int(tdf["AI calls"].fillna(0).sum()))
        st.dataframe(tdf.tail(100).iloc[::-1], hide_index=True, width="stretch", height=260)
        st.caption("Traces hold timings and counts only. Contract text is never recorded.")
    else:
        st.markdown('<span class="muted">No traces yet. Run an audit.</span>', unsafe_allow_html=True)
    if st.session_state.run_message:
        with st.expander("Output of the last run"):
            st.code(st.session_state.run_message, language=None)


{"Overview": page_overview, "Audits": page_audits, "Review queue": page_review,
 "Telemetry": page_telemetry, "Activity": page_activity}[page]()
