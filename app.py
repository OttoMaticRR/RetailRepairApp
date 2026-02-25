import streamlit as st
import pandas as pd
import pytz
from datetime import datetime
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from streamlit_option_menu import option_menu

# ---------------------
# Page setup
# ---------------------
st.set_page_config(
    page_title="Service Dashboard",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)
# --- Toppheader: tittel + dato til høyre ---
st.markdown("""
<style>
.header-row{
  display:flex; align-items:flex-end; justify-content:space-between;
  margin: 0 0 12px 0;
}
.header-title{ font-size:2.2rem; font-weight:800; line-height:1; }
.page-date{
  font-size:0.95rem; font-weight:600; letter-spacing:.2px;
  color:#9CA3AF; padding:6px 10px; border-radius:12px;
  border:1px solid rgba(0,0,0,.08);
  background: rgba(255,255,255,.06);
}
</style>
""", unsafe_allow_html=True)

def page_header(title: str, dateobj):
    st.markdown(
        f"""
        <div class="header-row">
          <div class="header-title">{title}</div>
          <div class="page-date">{dateobj:%d.%m.%Y}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# ---------------------
# Secrets / config
# ---------------------
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

GSHEETS_SHEET_ID = st.secrets["gsheets"]["sheet_id"]
GSHEETS_WORKSHEET = st.secrets["gsheets"].get("worksheet", "Sheet1")
TZ_NAME = st.secrets["gsheets"].get("timezone", "Europe/Oslo")
TZ = pytz.timezone(TZ_NAME)

AUTH_USER = st.secrets["auth"]["username"]
AUTH_PASS = st.secrets["auth"]["password"]

# ---------------------
# Styles (CSS)
# ---------------------
KPI_HEIGHT = 140  # ev. justér

st.markdown(f"""
<style>
:root {{ --kpi-height: {KPI_HEIGHT}px; }}

.kpi-card {{
  height: var(--kpi-height);
  display: flex;
  flex-direction: column;
  justify-content: center;       /* vertikalt midtstilt */
  align-items: center;           /* horisontalt midtstilt */
  text-align: center;            /* midtstill tekst */
  background: #ffffff;
  color: #111827 !important;
  border-radius: 16px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.08);
  border: 1px solid rgba(0,0,0,0.04);
  padding: 18px 22px;
}}

.kpi-label {{
  font-size: 0.95rem;
  color: #6b7280 !important;
  margin-bottom: 6px;
  text-align: center;
}}

.kpi-value {{
  font-size: 2.4rem;
  font-weight: 700;
  line-height: 1.1;
  color: #111827 !important;
  text-align: center;
}}

/* Default: nøytral (IKKE grønn) */
.kpi-sub {{
  font-size: 0.9rem;
  margin-top: 6px;
  font-weight: 600;
  text-align: center;
  color: #9CA3AF !important;
}}

/* Fargeklasser (må ha !important for å vinne) */
.kpi-sub-green {{ color:#10b981 !important; }}
.kpi-sub-red   {{ color:#ef4444 !important; }}
.kpi-sub-gray  {{ color:#9CA3AF !important; }}
</style>
""", unsafe_allow_html=True)

# ---------------------
# Auth
# ---------------------
def require_login():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    with st.sidebar:
        st.markdown("### 🔐 Login")
        with st.form("login_form", clear_on_submit=False):
            u = st.text_input("Brukernavn", value="", placeholder="Brukernavn")
            p = st.text_input("Passord", value="", type="password", placeholder="••••••••••")
            ok = st.form_submit_button("Logg inn")
        if ok:
            if u == AUTH_USER and p == AUTH_PASS:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Feil brukernavn eller passord.")
    st.title("🛡️ Service Dashboard")
    st.write("Vennligst logg inn fra menyen til venstre.")
    st.stop()

require_login()

with st.sidebar:
    if st.button("Logout"):
        for k in ["authenticated"]:
            if k in st.session_state: del st.session_state[k]
        st.rerun()

# ---------------------
# Google Sheets client
# ---------------------
@st.cache_resource
def get_gspread_client():
    creds_info = st.secrets["google_service_account"]
    credentials = Credentials.from_service_account_info(creds_info, scopes=SCOPE)
    return gspread.authorize(credentials)

# ---------------------
# Fetch data (cache 5 minutes)
# ---------------------
@st.cache_data(ttl=300, show_spinner=True)
def fetch_data():
    gc = get_gspread_client()
    sh = gc.open_by_key(GSHEETS_SHEET_ID)
    ws = sh.worksheet(GSHEETS_WORKSHEET)

    values = ws.get_all_values()  # rått fra Sheets
    if not values:
        return pd.DataFrame()

    def norm_header(x: str) -> str:
        x = str(x)
        x = x.replace("\u00A0", " ")   # NBSP
        x = x.replace("\u200b", "")    # zero-width
        x = x.replace("\ufeff", "")    # BOM
        x = " ".join(x.split())        # kollaps spaces
        return x.strip()

    headers = [norm_header(h) for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    # Fjern helt tomme rader
    df = df.replace("", pd.NA).dropna(how="all")

    expected = [
        "Service status date",
        "Service status",
        "Service repair date",
        "Service date product received",
        "Product brand",
        "Service technician",
        "Service priority",
        "Service number",
    ]

    # Case-insensitive rename (normalisert)
    cols_norm = {norm_header(c).casefold(): c for c in df.columns}
    for wanted in expected:
        key = wanted.casefold()
        if wanted not in df.columns and key in cols_norm:
            df.rename(columns={cols_norm[key]: wanted}, inplace=True)

    # Manglende kolonner -> NA
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    def clean_date_series(s: pd.Series) -> pd.Series:
        txt = (
            s.astype(str)
             .str.replace("\u00A0", " ", regex=False)
             .str.replace("\u200b", "", regex=False)
             .str.replace("\ufeff", "", regex=False)
             .str.replace(",", " ", regex=False)
             .str.replace(r"\s+", " ", regex=True)
             .str.strip()
        )
        txt = txt.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NaN": pd.NA, "<NA>": pd.NA})

        # 1) vanlig parsing
        dt = pd.to_datetime(txt, errors="coerce", dayfirst=True)

        # 2) fallback: Excel/Sheets serial
        num = pd.to_numeric(txt, errors="coerce")
        serial_mask = dt.isna() & num.notna() & (num > 20000) & (num < 60000)
        if serial_mask.any():
            dt.loc[serial_mask] = pd.to_datetime(num.loc[serial_mask], unit="D", origin="1899-12-30")

        return dt

    for dc in ["Service status date", "Service repair date", "Service date product received"]:
        df[dc] = clean_date_series(df[dc])

    # Trim tekstkolonner (VIKTIG: inkluder priority + number)
    for sc in ["Service status", "Product brand", "Service technician", "Service priority", "Service number"]:
        df[sc] = (
            df[sc]
            .astype(str)
            .str.replace("\u00A0", " ", regex=False)
            .str.strip()
        )

    return df


# ---------------------
# Helpers
# ---------------------
def today_oslo():
    return datetime.now(TZ).date()

def filter_today(df, date_col):
    return df[df[date_col].dt.date == today_oslo()]

def kpi(label, value, sub=None, sub_color=None):
    # sub_color: "green", "red", "gray"
    cls = ""
    if sub_color == "green":
        cls = "kpi-sub-green"
    elif sub_color == "red":
        cls = "kpi-sub-red"
    elif sub_color == "gray":
        cls = "kpi-sub-gray"

    st.markdown(
        f"""
        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{value if value is not None else 0}</div>
          {f'<div class="kpi-sub {cls}">{sub}</div>' if sub else ''}
        </div>
        """,
        unsafe_allow_html=True
    )

def two_cols(fig_left_title, fig_left, fig_right_title, fig_right):
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(fig_left_title)
        st.plotly_chart(fig_left, use_container_width=True)
    with c2:
        st.subheader(fig_right_title)
        st.plotly_chart(fig_right, use_container_width=True)

def _counts_table(series: pd.Series, left_header: str, right_header: str) -> pd.DataFrame:
    s = pd.Series(series, dtype=object)

    # Behold NA før vi gjør str()
    s = s.where(s.notna(), other="Ukjent")
    s = s.astype(str).str.strip()

    # Rydd opp “spøkelsesverdier”
    s = s.replace({
        "": "Ukjent",
        "nan": "Ukjent",
        "None": "Ukjent",
        "NaN": "Ukjent",
        "<NA>": "Ukjent",
        "N/A": "Ukjent",
    })

    out = (
        s.value_counts(dropna=False)
         .rename_axis(left_header)
         .reset_index(name=right_header)
    )

    out.insert(0, "Nr", range(1, len(out) + 1))
    out = out.astype({"Nr": int, right_header: int})
    out[left_header] = out[left_header].astype(object)
    return out

def avg_tat_days(df_in: pd.DataFrame, end_day) -> float:
    """
    Snitt TAT (dager):
    (reparert hvis finnes ellers end_day) - innlevert.
    """
    if df_in.empty:
        return 0.0

    d = df_in.copy()
    d["received_dt"] = pd.to_datetime(d["Service date product received"], errors="coerce")
    d["repaired_dt"] = pd.to_datetime(d["Service repair date"], errors="coerce")

    d = d.dropna(subset=["received_dt"])
    if d.empty:
        return 0.0

    end_ts = pd.Timestamp(end_day)
    end_dt = d["repaired_dt"].fillna(end_ts)

    tat_days = (end_dt - d["received_dt"]).dt.total_seconds() / 86400.0
    tat_days = tat_days[(tat_days >= 0) & tat_days.notna()]
    return float(tat_days.mean()) if not tat_days.empty else 0.0
    
    return out
    
def latest_date_in_data(df: pd.DataFrame):
    """Finn siste dato som finnes i datasettet (statusdato eller reparasjonsdato)."""
    candidates = []

    if "Service status date" in df.columns:
        s = pd.to_datetime(df["Service status date"], errors="coerce")
        s = s.dropna()
        if not s.empty:
            candidates.append(s.dt.date.max())

    if "Service repair date" in df.columns:
        r = pd.to_datetime(df["Service repair date"], errors="coerce")
        r = r.dropna()
        if not r.empty:
            candidates.append(r.dt.date.max())

    return max(candidates) if candidates else today_oslo()


def filter_on_day(df: pd.DataFrame, date_col: str, day):
    """Filtrer rader hvor date_col matcher valgt dag."""
    s = pd.to_datetime(df[date_col], errors="coerce")
    return df[s.dt.date == day]

    return out

def _clean_text(s: pd.Series, unknown="Ukjent") -> pd.Series:
    x = pd.Series(s, dtype=object)
    x = x.where(x.notna(), other=unknown)   # viktig: NA før astype(str)
    x = x.astype(str).str.strip()
    return x.replace({
        "": unknown,
        "nan": unknown,
        "None": unknown,
        "NaN": unknown,
        "<NA>": unknown,
        "N/A": unknown,
    })

# ---------------------
# Data
# ---------------------
df_raw = fetch_data()

# Standard datasett uten SPS (brukes av alle faner)
if "Service priority" in df_raw.columns:
    prio = df_raw["Service priority"].astype(str).str.strip().str.casefold()
    df = df_raw[prio != "sps"].copy()
    df_sps = df_raw[prio == "sps"].copy()   # (valgfritt) til egen SPS-fane senere
else:
    df = df_raw.copy()
    df_sps = pd.DataFrame()

default_day = latest_date_in_data(df)

# Source-of-truth i session_state
if "day" not in st.session_state:
    st.session_state["day"] = default_day

# Toggle for datepicker
if "show_datepicker" not in st.session_state:
    st.session_state["show_datepicker"] = False

# Faktisk i dag (Oslo) – brukes til å låse fremover
_real_today = today_oslo()

# Hvis du har navigert til fremtid ved en feil, clamp tilbake
if st.session_state["day"] > _real_today:
    st.session_state["day"] = _real_today

with st.sidebar:
    st.caption("Vis dato")

    # Litt CSS for å gjøre pil-knappene kompakte
    st.markdown("""
    <style>
    section[data-testid="stSidebar"] button[kind="secondary"]{
        padding: 0.15rem 0.35rem !important;
        min-height: 2.1rem !important;
        font-weight: 700 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # --- Rad: ←  DATO  → ---
    c_prev, c_date, c_next = st.columns([1, 4, 1], vertical_alignment="center")

    with c_prev:
        if st.button("←", use_container_width=True, help="Forrige dag"):
            st.session_state["day"] = st.session_state["day"] - pd.Timedelta(days=1)
            st.rerun()

    with c_date:
        st.markdown(
            f"<div style='text-align:center; font-size:1.05rem; font-weight:800;'>"
            f"{st.session_state['day']:%d.%m.%Y}</div>",
            unsafe_allow_html=True
        )

    with c_next:
        is_future = st.session_state["day"] >= _real_today
        if st.button("→", use_container_width=True, help="Neste dag", disabled=is_future):
            st.session_state["day"] = st.session_state["day"] + pd.Timedelta(days=1)
            st.rerun()

    # Litt luft
    st.markdown("<div style='height:0.25rem'></div>", unsafe_allow_html=True)

    # --- Rad: I dag + Endre ---
    c_today, c_pick = st.columns(2)
    with c_today:
        if st.button("I dag", use_container_width=True):
            st.session_state["day"] = _real_today
            st.rerun()

    with c_pick:
        if st.button("Endre", use_container_width=True):
            st.session_state["show_datepicker"] = not st.session_state["show_datepicker"]

    # --- Datepicker vises bare når du trykker Endre ---
    if st.session_state["show_datepicker"]:
        picked = st.date_input(
            label="",
            value=st.session_state["day"],
            max_value=_real_today,          # <- hindrer valg i fremtiden
            label_visibility="collapsed",
        )
        if picked != st.session_state["day"]:
            st.session_state["day"] = picked
            st.session_state["show_datepicker"] = False
            st.rerun()

# Bruk valgt dato overalt i dashboardet
today = st.session_state["day"]


# ---------------------
# Sidebar menu
# ---------------------
with st.sidebar:
    selected = option_menu(
        None,
        ["Dashboard", "Reparert", "Innlevert", "Inhouse", "Arbeidet på", "Historikk", "Teknikere", "Kunder"],
        icons=["speedometer2", "bag-fill", "box-seam", "house-door-fill", "hammer", "calendar3", "people-fill", "people"],
        menu_icon="list",
        default_index=0,
        styles={
            "container": {"padding": "0!important"},
            "icon": {"font-size": "18px"},
            "nav-link": {
                "font-size": "16px",
                "text-align": "left",
                "padding": "8px 12px",
                "margin": "4px 6px",
                "border-radius": "12px",
            },
            "nav-link-selected": {"background-color": "#ef4444", "color": "white"},
        },    
    )

    st.markdown('<div class="sidebar-footer">Secure dashboard</div>', unsafe_allow_html=True)

# ---------------------
# Header
# ---------------------
page_header(selected, today)

# ---------------------
# Views
# ---------------------
if selected == "Dashboard":
    # -----------------------------
    # Dashboard (one-glance)
    # -----------------------------
    REQUIRED = [
        "Service status",
        "Service status date",
        "Service repair date",
        "Service date product received",  # innlevertdato
        "Product brand",
        "Service technician",
    ]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        st.error(f"Mangler kolonner i datasettet: {', '.join(missing)}")
        st.stop()

    end_day = pd.Timestamp(today)

    def _clean_text(s: pd.Series, unknown="Ukjent") -> pd.Series:
        x = pd.Series(s, dtype=object).astype(str).str.strip()
        return x.replace({"": unknown, "nan": unknown, "None": unknown, "NaN": unknown})

    def _last_business_days(end_date, n=30):
        end_ts = pd.Timestamp(end_date)
        bdays = pd.bdate_range(end=end_ts, periods=n)
        return [d.date() for d in bdays]

    def _trend_arrow_color(delta: float, good_when_up: bool, eps: float):
        # good_when_up=True => ↑ grønn, ↓ rød
        # good_when_up=False => ↓ grønn, ↑ rød
        if delta > eps:
            return ("↑", "green" if good_when_up else "red")
        if delta < -eps:
            return ("↓", "red" if good_when_up else "green")
        return ("→", "gray")

    # -----------------------------
    # Data-kopier med rensede felt
    # -----------------------------
    d = df.copy()
    d["brand"] = _clean_text(d["Product brand"], unknown="Ukjent")
    d["tech"] = _clean_text(d["Service technician"], unknown="Ukjent")
    d["status_clean"] = _clean_text(d["Service status"], unknown="").str.casefold()

    d["received_dt"] = pd.to_datetime(d["Service date product received"], errors="coerce")
    d["repaired_dt"] = pd.to_datetime(d["Service repair date"], errors="coerce")
    d["status_dt"] = pd.to_datetime(d["Service status date"], errors="coerce")
    d["rep_date"] = d["repaired_dt"].dt.date

    # -----------------------------
    # KPI 1: Snitt reparert pr tekniker pr arbeidsdag (siste 30 arb.dager) + trend
    # -----------------------------
    last_30_bd = _last_business_days(today, 30)
    prev_30_bd = _last_business_days(pd.Timestamp(last_30_bd[0]) - pd.Timedelta(days=1), 30)

    rep_last = d[d["rep_date"].isin(last_30_bd)].copy()
    rep_prev = d[d["rep_date"].isin(prev_30_bd)].copy()

    tech_last = rep_last["tech"].nunique()
    tech_prev = rep_prev["tech"].nunique()

    avg_rep_per_tech_day_last = (len(rep_last) / (max(tech_last, 1) * 30)) if tech_last > 0 else 0.0
    avg_rep_per_tech_day_prev = (len(rep_prev) / (max(tech_prev, 1) * 30)) if tech_prev > 0 else 0.0

    delta_rep_per_tech_day = avg_rep_per_tech_day_last - avg_rep_per_tech_day_prev
    a1, c1 = _trend_arrow_color(delta_rep_per_tech_day, good_when_up=True, eps=0.01)

    kpi1_value = f"{avg_rep_per_tech_day_last:.2f}"
    kpi1_sub = f"{a1} {delta_rep_per_tech_day:+.2f} vs forrige 30"

    # -----------------------------
    # KPI 2: TAT nå (ÅPNE saker) snitt + trend på ferdige saker siste 30 vs forrige 30
    # -----------------------------
    open_cases = d[d["repaired_dt"].isna()].dropna(subset=["received_dt"]).copy()
    if open_cases.empty:
        tat_now_avg = 0.0
        oldest_open_days = 0
    else:
        tat_open_days = (end_day - open_cases["received_dt"]).dt.total_seconds() / 86400.0
        tat_open_days = tat_open_days[(tat_open_days >= 0) & tat_open_days.notna()]
        tat_now_avg = float(tat_open_days.mean()) if not tat_open_days.empty else 0.0
        oldest_open_days = int(tat_open_days.max()) if not tat_open_days.empty else 0

    # trend: ferdige saker (reparert) i siste 30 kalenderdager vs forrige 30
    closed = d.dropna(subset=["received_dt", "repaired_dt"]).copy()
    start_30 = end_day - pd.Timedelta(days=30)
    start_prev30 = end_day - pd.Timedelta(days=60)

    closed_last = closed[(closed["repaired_dt"] > start_30) & (closed["repaired_dt"] <= end_day)].copy()
    closed_prev = closed[(closed["repaired_dt"] > start_prev30) & (closed["repaired_dt"] <= start_30)].copy()

    def _avg_tat_closed_days(x: pd.DataFrame) -> float:
        if x.empty:
            return 0.0
        tat_days = (x["repaired_dt"] - x["received_dt"]).dt.total_seconds() / 86400.0
        tat_days = tat_days[(tat_days >= 0) & tat_days.notna()]
        return float(tat_days.mean()) if not tat_days.empty else 0.0

    tat_closed_last = _avg_tat_closed_days(closed_last)
    tat_closed_prev = _avg_tat_closed_days(closed_prev)
    delta_tat_closed = tat_closed_last - tat_closed_prev

    # For TAT trend: ned er bra
    a2, c2 = _trend_arrow_color(delta_tat_closed, good_when_up=False, eps=0.2)

    kpi2_value = f"{tat_now_avg:.1f} dager"
    kpi2_sub = f"{a2} {delta_tat_closed:+.1f} (ferdig 30d vs forrige)"
    # ekstra info (eldste) – vises i label eller i sub-tekst
    kpi2_label = f"TAT nå (åpne saker) – eldste: {oldest_open_days}d"

    # -----------------------------
    # KPI 3: Snitt reparert pr dag totalt (siste 30 arb.dager) + trend
    # -----------------------------
    avg_rep_per_day_last = len(rep_last) / 30.0
    avg_rep_per_day_prev = len(rep_prev) / 30.0
    delta_rep_per_day = avg_rep_per_day_last - avg_rep_per_day_prev

    a3, c3 = _trend_arrow_color(delta_rep_per_day, good_when_up=True, eps=0.2)
    kpi3_value = f"{avg_rep_per_day_last:.2f}"
    kpi3_sub = f"{a3} {delta_rep_per_day:+.2f} vs forrige 30"

    # KPI-rad
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Snitt reparert pr tekniker pr dag (30 arb.dager)", kpi1_value, sub=kpi1_sub, sub_color=c1)
    with k2:
        kpi(kpi2_label, kpi2_value, sub=kpi2_sub, sub_color=c2)
    with k3:
        kpi("Snitt reparert pr dag totalt (30 arb.dager)", kpi3_value, sub=kpi3_sub, sub_color=c3)

   # -----------------------------
    # Graf 1: Innlevert pr merke (ALLE som har status "Innlevert" nå)
    # Graf 2: Reparert pr merke (VALGT DATO)
    # -----------------------------

    # Graf 1: alle som har status Innlevert (uansett statusdato)
    delivered_now = d[d["status_clean"] == "innlevert"].copy()

    delivered_df = (
        delivered_now["brand"]
        .value_counts()
        .rename_axis("Merke")
        .reset_index(name="Antall")
        .sort_values("Antall", ascending=False)
    )

    if delivered_df.empty:
        fig_del = px.bar(pd.DataFrame({"Merke": [], "Antall": []}), x="Merke", y="Antall")
    else:
        fig_del = px.bar(delivered_df, x="Merke", y="Antall", text="Antall")
        fig_del.update_layout(
            xaxis_title="Merke",
            yaxis_title="Antall",
            xaxis={"categoryorder": "array", "categoryarray": delivered_df["Merke"].tolist()},
        )
        fig_del.update_traces(textposition="outside", cliponaxis=False)

    # Graf 2: reparert på valgt dato (uendret)
    repaired_today = d[d["rep_date"] == today].copy()

    repaired_df = (
        repaired_today["brand"]
        .value_counts()
        .rename_axis("Merke")
        .reset_index(name="Antall")
        .sort_values("Antall", ascending=False)
    )

    if repaired_df.empty:
        fig_rep = px.bar(pd.DataFrame({"Merke": [], "Antall": []}), x="Merke", y="Antall")
    else:
        fig_rep = px.bar(repaired_df, x="Merke", y="Antall", text="Antall")
        fig_rep.update_layout(
            xaxis_title="Merke",
            yaxis_title="Antall",
            xaxis={"categoryorder": "array", "categoryarray": repaired_df["Merke"].tolist()},
        )
        fig_rep.update_traces(textposition="outside", cliponaxis=False)

    two_cols("Innlevert pr merke", fig_del, "Reparert pr merke", fig_rep)

    with st.expander("Hvordan beregnes dette?", expanded=False):
        st.markdown(
            """
- **Snitt reparert pr tekniker pr dag (30 arb.dager)** = reparasjoner siste 30 arbeidsdager / (aktive teknikere i perioden × 30).
- **TAT nå (åpne saker)** = snitt( i dag − innlevert ) for saker uten reparertdato. *Eldste* viser maks( i dag − innlevert ).
- **Trend på TAT** = snitt( reparert − innlevert ) for saker ferdigstilt siste 30 dager vs forrige 30 (↓ grønn = bedre).
- **Snitt reparert pr dag totalt** = reparasjoner siste 30 arbeidsdager / 30.
            """
        )

elif selected == "Reparert":
    repaired_today = filter_on_day(df, "Service repair date", today)
    total_repaired = len(repaired_today)

    # KPI-er
    n_distinct_brands = (
        repaired_today["Product brand"]
        .replace(["", "nan"], pd.NA).dropna().nunique()
    )
    tech_counts = repaired_today["Service technician"].value_counts()
    top_tech = tech_counts.index[0] if not tech_counts.empty else "-"
    top_tech_count = int(tech_counts.iloc[0]) if not tech_counts.empty else 0

    k1, k2, k3 = st.columns(3)
    with k1: kpi("Totalt reparert i dag", total_repaired)
    with k2: kpi("Merker", n_distinct_brands)
    with k3: kpi("Topp tekniker", top_tech,
                 sub=(f"↑ {top_tech_count} repairs" if top_tech != "-" else None))

    if not repaired_today.empty:
        # --- Merker sortert synkende på antall ---
        brand_df = (
            repaired_today["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Unknown", "nan": "Unknown", "None": "Unknown"})
            .value_counts()
            .rename_axis("Product brand")
            .reset_index(name="Repairs")
            .sort_values("Repairs", ascending=False)
        )
        brand_bar = px.bar(brand_df, x="Product brand", y="Repairs", text="Repairs")
        brand_bar.update_layout(
            xaxis_title="Brand",
            yaxis_title="Repairs",
            xaxis={
                # behold sorteringen vi nettopp laget
                "categoryorder": "array",
                "categoryarray": brand_df["Product brand"].tolist(),
            },
        )
        brand_bar.update_traces(textposition="outside", cliponaxis=False)

        # Tekniker (beholdt som før)
        tech_pie = px.pie(
            repaired_today.groupby("Service technician").size().reset_index(name="Repairs"),
            names="Service technician",
            values="Repairs",
            hole=0.35,
        )
    else:
        brand_bar = px.bar(pd.DataFrame({"Product brand": [], "Repairs": []}),
                           x="Product brand", y="Repairs")
        tech_pie = px.pie(pd.DataFrame({"Service technician": [], "Repairs": []}),
                          names="Service technician", values="Repairs")

    two_cols("Merker reparert i dag", brand_bar, "Tekniker reparert i dag", tech_pie)

    with st.expander("Vis tabell", expanded=False):
        c1, c2 = st.columns(2)
        tbl_brand = _counts_table(repaired_today["Product brand"], "Brand", "Repairs")
        c1.markdown("#### Merker")
        c1.dataframe(tbl_brand, use_container_width=True, hide_index=True)

        tbl_tech = _counts_table(repaired_today["Service technician"], "Technician", "Repairs")
        c2.markdown("#### Teknikere")
        c2.dataframe(tbl_tech, use_container_width=True, hide_index=True)



elif selected == "Innlevert":
    # Filtrer "Innlevert" (alle)
    delivered = df[df["Service status"].astype(str).str.strip().str.casefold() == "innlevert"].copy()

    # Innlevert på valgt dato (today = selected_day)
    delivered_today = filter_on_day(delivered, "Service status date", today)

    # Topp merke blant alle innleverte
    if not delivered.empty:
        brand_counts = (
            delivered["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})
            .value_counts()
        )
        top_brand = brand_counts.index[0]
        top_brand_count = int(brand_counts.iloc[0])
    else:
        top_brand, top_brand_count = "-", 0

    # KPI-er (3 kolonner)
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Totalt innlevert", len(delivered))
    with k2:
        kpi("Innlevert (valgt dato)", len(delivered_today))
    with k3:
        kpi("Topp merke", top_brand, sub=(f"{top_brand_count} enheter" if top_brand != "-" else None))

    # Diagrammer
    if not delivered.empty:
        # Merke — sortert synkende på antall
        brand_df = (
            delivered["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})
            .value_counts()
            .rename_axis("Merke")
            .reset_index(name="Antall")
            .sort_values("Antall", ascending=False)
        )
        brand_bar = px.bar(brand_df, x="Merke", y="Antall", text="Antall")
        brand_bar.update_layout(
            xaxis_title="Merke",
            yaxis_title="Antall",
            xaxis={"categoryorder": "array", "categoryarray": brand_df["Merke"].tolist()},
        )
        brand_bar.update_traces(textposition="outside", cliponaxis=False)

        # Statusdato — kronologisk
        date_df = (
            delivered.assign(date=pd.to_datetime(delivered["Service status date"], errors="coerce").dt.date)
                     .dropna(subset=["date"])
                     .groupby("date").size().reset_index(name="Antall")
                     .sort_values("date")
        )
        date_bar = px.bar(date_df, x="date", y="Antall", text="Antall")
        date_bar.update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
        date_bar.update_traces(textposition="outside", cliponaxis=False)

    else:
        brand_bar = px.bar(pd.DataFrame({"Merke": [], "Antall": []}), x="Merke", y="Antall")
        date_bar  = px.bar(pd.DataFrame({"date": [], "Antall": []}), x="date", y="Antall")

    two_cols("Innlevert per merke", brand_bar, "Innlevert per statusdato", date_bar)




elif selected == "Inhouse":
    # Alt uten reparasjonsdato er "inhouse"
    inhouse = df[df["Service repair date"].isna()].copy()

    # --- Gruppér status: alle som begynner med "Venter på ekstern part ..." slås sammen ---
    if not inhouse.empty:
        raw_status = inhouse["Service status"].astype(str).str.strip()
        low = raw_status.str.casefold()
        mask = low.str.match(r"^venter på ekstern part\b")
        inhouse["status_group"] = raw_status.where(~mask, "Venter på ekstern part")
    else:
        inhouse["status_group"] = pd.Series(dtype="object")

    # --- KPI-er ---
    total_inhouse = len(inhouse)

    # Topp status (på den grupperte statusen)
    if total_inhouse > 0:
        status_counts = inhouse["status_group"].value_counts(dropna=False)
        top_status = status_counts.index[0]
        top_status_count = int(status_counts.iloc[0])
    else:
        top_status, top_status_count = "-", 0

    # Topp merke
    if total_inhouse > 0:
        brand_counts = (
            inhouse["Product brand"].astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent"})
            .value_counts()
        )
        top_brand = brand_counts.index[0]
        top_brand_count = int(brand_counts.iloc[0])
    else:
        top_brand, top_brand_count = "-", 0

    # Vis 3 KPI-kort: Antall • Topp status • Topp merke
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Totalt inhouse", total_inhouse)
    with k2:
        kpi("Topp status", top_status,
            sub=(f"{top_status_count} enheter" if top_status != "-" else None))
    with k3:
        kpi("Topp merke", top_brand,
            sub=(f"{top_brand_count} enheter" if top_brand != "-" else None))

    # --- Diagrammer ---
    if total_inhouse > 0:
        # Inhouse per (gruppert) status – sortert synkende
        status_df = (
            inhouse["status_group"]
            .value_counts(dropna=False)
            .rename_axis("Status")
            .reset_index(name="Antall")
            .sort_values("Antall", ascending=False)
        )
        status_bar = px.bar(status_df, x="Status", y="Antall", text="Antall")
        status_bar.update_layout(xaxis_title="Status", yaxis_title="Antall")
        status_bar.update_traces(textposition="outside", cliponaxis=False)

        # Inhouse per statusdato – kronologisk
        date_df = (
            inhouse.assign(date=inhouse["Service status date"].dt.date)
                   .groupby("date").size().reset_index(name="Antall")
                   .sort_values("date")
        )
        date_bar = px.bar(date_df, x="date", y="Antall", text="Antall")
        date_bar.update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
        date_bar.update_traces(textposition="outside", cliponaxis=False)
    else:
        status_bar = px.bar(pd.DataFrame({"Status": [], "Antall": []}),
                            x="Status", y="Antall")
        date_bar   = px.bar(pd.DataFrame({"date": [], "Antall": []}),
                            x="date", y="Antall")

    two_cols("Inhouse per status", status_bar, "Inhouse per statusdato", date_bar)


elif selected == "Arbeidet på":
    # Denne fanen skal ALLTID vise faktisk i dag (Oslo) – uavhengig av valgt dato i sidebar
    work_day = today_oslo()

    # Filtrer saker for i dag som IKKE er 'Innlevert'
    wt = df[
        (pd.to_datetime(df["Service status date"], errors="coerce").dt.date == work_day)
        & (df["Service status"].astype(str).str.strip().str.casefold() != "innlevert")
    ].copy()

    # Gruppér status: samle alle som begynner med "Venter på ekstern part ..."
    if not wt.empty:
        raw_status = wt["Service status"].astype(str).str.strip()
        mask = raw_status.str.casefold().str.match(r"^venter på ekstern part\b")
        wt["status_group"] = raw_status.where(~mask, "Venter på ekstern part")

        # KPI: mest satte status
        status_counts = wt["status_group"].value_counts(dropna=False)
        top_status = status_counts.index[0] if not status_counts.empty else "-"
        top_status_count = int(status_counts.iloc[0]) if not status_counts.empty else 0

        # KPI: topp tekniker
        tech_counts = wt["Service technician"].astype(str).str.strip().value_counts(dropna=False)
        top_tech2 = tech_counts.index[0] if not tech_counts.empty else "-"
        top_tech2_count = int(tech_counts.iloc[0]) if not tech_counts.empty else 0
    else:
        top_status, top_status_count = "-", 0
        top_tech2, top_tech2_count = "-", 0
        wt["status_group"] = pd.Series(dtype="object")

    # KPI-kort
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Totalt arbeidet på i dag", len(wt))
    with k2:
        kpi("Mest satte status", top_status,
            sub=(f"{top_status_count} enheter" if top_status != "-" else None))
    with k3:
        kpi("Topp tekniker", top_tech2,
            sub=(f"{top_tech2_count} enheter" if top_tech2 != "-" else None))

    # Diagrammer
    if not wt.empty:
        # Venstre: per merke (sortert synkende)
        brand_df2 = (
            wt["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent", "<NA>": "Ukjent"})
            .value_counts()
            .rename_axis("Merke")
            .reset_index(name="Antall")
            .sort_values("Antall", ascending=False)
        )
        brand_bar2 = px.bar(brand_df2, x="Merke", y="Antall", text="Antall")
        brand_bar2.update_layout(
            xaxis_title="Merke",
            yaxis_title="Antall",
            xaxis={"categoryorder": "array", "categoryarray": brand_df2["Merke"].tolist()},
        )
        brand_bar2.update_traces(textposition="outside", cliponaxis=False)

        # Høyre: per status (gruppert + sortert synkende)
        status_df2 = (
            wt["status_group"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent", "<NA>": "Ukjent"})
            .value_counts(dropna=False)
            .rename_axis("Status")
            .reset_index(name="Antall")
            .sort_values("Antall", ascending=False)
        )
        status_bar2 = px.bar(status_df2, x="Status", y="Antall", text="Antall")
        status_bar2.update_layout(xaxis_title="Status", yaxis_title="Antall")
        status_bar2.update_traces(textposition="outside", cliponaxis=False)
    else:
        brand_bar2 = px.bar(pd.DataFrame({"Merke": [], "Antall": []}), x="Merke", y="Antall")
        status_bar2 = px.bar(pd.DataFrame({"Status": [], "Antall": []}), x="Status", y="Antall")

    two_cols("Arbeidet på i dag per merke", brand_bar2, "Arbeidet på i dag per status", status_bar2)

    # Tabeller
    with st.expander("Vis tabell", expanded=False):
        c1, c2 = st.columns(2)

        tbl_brand2 = _counts_table(wt["Product brand"], "Brand", "Enheter")
        c1.markdown("#### Arbeidet på (i dag) per merke")
        c1.dataframe(tbl_brand2, use_container_width=True, hide_index=True)

        tbl_tech2 = _counts_table(wt["Service technician"], "Technician", "Enheter")
        c2.markdown("#### Arbeidet på (i dag) per tekniker")
        c2.dataframe(tbl_tech2, use_container_width=True, hide_index=True)



elif selected == "Historikk":
    rep_series = df["Service repair date"]

    # Robust konvertering til dato (tz-aware eller ikke)
    if pd.api.types.is_datetime64tz_dtype(rep_series):
        date_only = rep_series.dt.tz_convert(TZ_NAME).dt.date
    else:
        date_only = rep_series.dt.date

    # Antall reparerte per dato (kun dager som faktisk har reparasjoner)
    hist = (
        pd.DataFrame({"date": date_only})
        .dropna()
        .value_counts("date")
        .reset_index(name="Repairs")
        .sort_values("date")
    )

    if hist.empty:
        st.info("Ingen reparasjoner i datasettet.")
        st.stop()

    # -----------------------------
    # Ukesvis graf (uke = mandag–søndag)
    # -----------------------------
    hist_week = hist.copy()
    hist_week["date_ts"] = pd.to_datetime(hist_week["date"])

    # Mandag i samme uke (0=mandag, 6=søndag)
    hist_week["week_start"] = hist_week["date_ts"] - pd.to_timedelta(hist_week["date_ts"].dt.weekday, unit="D")
    hist_week["week_start"] = hist_week["week_start"].dt.normalize()

    weekly = (
        hist_week.groupby("week_start", as_index=False)["Repairs"]
        .sum()
        .sort_values("week_start")
    )

    # Viktig: Ikke vis "flatt" før første datapunkt (weekly inneholder kun uker med data)
    fig_hist = px.line(weekly, x="week_start", y="Repairs", markers=True)
    fig_hist.update_layout(
        xaxis_title="Uke (start mandag)",
        yaxis_title="Antall reparert",
        hovermode="x unified",
    )
    st.plotly_chart(fig_hist, use_container_width=True)

    # -----------------------------
    # Tabeller for valgt dato (bruker venstre datovelger = today)
    # -----------------------------
    selected_date = today  # bruker kun globalt valgt dato (ingen slider her)

    # Filtrer dagens/valgt dato fra date_only (som er en "date"-serie)
    day_df = df[date_only == selected_date]

    with st.expander("Tabeller for valgt dato", expanded=True):
        c1, c2 = st.columns(2)
        c1.markdown(f"#### Reparert per merke ({selected_date:%d.%m.%Y})")
        c1.dataframe(
            _counts_table(day_df["Product brand"], "Brand", "Repairs"),
            use_container_width=True,
            hide_index=True,
        )

        c2.markdown(f"#### Reparert per tekniker ({selected_date:%d.%m.%Y})")
        c2.dataframe(
            _counts_table(day_df["Service technician"], "Technician", "Repairs"),
            use_container_width=True,
            hide_index=True,
        )

elif selected == "Teknikere":
    # Bruk kun rader med reparasjonsdato
    rep = df.dropna(subset=["Service repair date"]).copy()
    if rep.empty:
        st.info("Ingen reparasjoner i datasettet.")
    else:
        # Rens tekniker + dato
        rep["Tekniker"] = (
            rep["Service technician"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})
        )
        rep["rep_date"] = pd.to_datetime(rep["Service repair date"], errors="coerce").dt.date
        rep = rep.dropna(subset=["rep_date"])

        if rep.empty:
            st.info("Ingen gyldige reparasjonsdatoer i datasettet.")
        else:
            max_date = rep["rep_date"].max()
            end = pd.Timestamp(max_date)

            # --- Arbeidsdager (man-fre) ---
            # Siste 7 og 30 ARBEIDSDAGER (ikke kalenderdager)
            start_7 = pd.bdate_range(end=end, periods=7)[0].date()
            start_30 = pd.bdate_range(end=end, periods=30)[0].date()

            rep_7 = rep[(rep["rep_date"] >= start_7) & (rep["rep_date"] <= max_date)]
            rep_30 = rep[(rep["rep_date"] >= start_30) & (rep["rep_date"] <= max_date)]

            total_7 = rep_7["Tekniker"].value_counts()
            total_30 = rep_30["Tekniker"].value_counts()

            # Snitt per arbeidsdag
            avg_7 = (total_7 / 7).to_frame("Snitt 7 arbeidsdager")
            avg_30 = (total_30 / 30).to_frame("Snitt 30 arbeidsdager")

            # Totaler (greit å vise)
            tot_7 = total_7.to_frame("Totalt 7 arbeidsdager")
            tot_30 = total_30.to_frame("Totalt 30 arbeidsdager")

            tech_tbl = (
                avg_7.join(avg_30, how="outer")
                    .join(tot_7, how="outer")
                    .join(tot_30, how="outer")
                    .fillna(0)
                    .reset_index()
            )
            tech_tbl.rename(columns={tech_tbl.columns[0]: "Tekniker"}, inplace=True)

            # Trend: forskjell mellom snitt 7 og snitt 30
            tech_tbl["Trend (7-30)"] = tech_tbl["Snitt 7 arbeidsdager"] - tech_tbl["Snitt 30 arbeidsdager"]
            tech_tbl = tech_tbl.sort_values("Snitt 7 arbeidsdager", ascending=False)

            # KPI
            if not tech_tbl.empty:
                top_name = str(tech_tbl.iloc[0]["Tekniker"])
                top_avg7 = float(tech_tbl.iloc[0]["Snitt 7 arbeidsdager"])
            else:
                top_name, top_avg7 = "-", 0.0

            k1, k2, k3 = st.columns(3)
            with k1:
                kpi("Siste reparasjonsdato", f"{max_date:%d.%m.%Y}")
            with k2:
                kpi("Topp tekniker (snitt 7 arb.d)", top_name, sub=f"{top_avg7:.2f} pr arbeidsdag")
            with k3:
                kpi("Antall teknikere", int(tech_tbl["Tekniker"].nunique()))

            # Graf 1: snitt 7 vs 30 (arbeidsdager)
            plot_df = tech_tbl.melt(
                id_vars=["Tekniker", "Trend (7-30)"],
                value_vars=["Snitt 7 arbeidsdager", "Snitt 30 arbeidsdager"],
                var_name="Periode",
                value_name="Snitt pr arbeidsdag",
            )

            bar = px.bar(
                plot_df,
                x="Tekniker",
                y="Snitt pr arbeidsdag",
                color="Periode",
                barmode="group",
                text="Snitt pr arbeidsdag",
            )
            bar.update_traces(texttemplate="%{text:.2f}", textposition="outside", cliponaxis=False)
            bar.update_layout(
                xaxis_title="Tekniker",
                yaxis_title="Snitt reparert pr arbeidsdag",
                legend_title="Periode",
            )
            st.subheader("Snitt reparert pr arbeidsdag – 7 vs 30 arbeidsdager")
            st.plotly_chart(bar, use_container_width=True)

            # Graf 2: trend (positiv/negativ)
            trend_df = tech_tbl[["Tekniker", "Trend (7-30)"]].copy().sort_values("Trend (7-30)", ascending=False)
            trend_bar = px.bar(trend_df, x="Tekniker", y="Trend (7-30)", text="Trend (7-30)")
            trend_bar.update_traces(texttemplate="%{text:.2f}", textposition="outside", cliponaxis=False)
            trend_bar.update_layout(
                xaxis_title="Tekniker",
                yaxis_title="Trend (snitt 7 arbeidsdager minus snitt 30 arbeidsdager)",
            )
            st.subheader("Trend – positiv eller negativ")
            st.plotly_chart(trend_bar, use_container_width=True)

            # Tabell (safe dtypes)
            tech_tbl_out = tech_tbl.copy()
            tech_tbl_out["Tekniker"] = tech_tbl_out["Tekniker"].astype(object)
            for c in ["Snitt 7 arbeidsdager", "Snitt 30 arbeidsdager", "Trend (7-30)"]:
                tech_tbl_out[c] = tech_tbl_out[c].astype(float).round(2)
            for c in ["Totalt 7 arbeidsdager", "Totalt 30 arbeidsdager"]:
                tech_tbl_out[c] = tech_tbl_out[c].astype(int)

            with st.expander("Vis tabell", expanded=False):
                st.dataframe(tech_tbl_out, use_container_width=True, hide_index=True)

elif selected == "Kunder":
    # Inhouse = åpne saker (ingen reparasjonsdato)
    base = df[pd.to_datetime(df["Service repair date"], errors="coerce").isna()].copy()

    if base.empty:
        st.info("Ingen enheter i inhouse.")
        st.stop()

    def _clean_text(s: pd.Series, unknown="Ukjent") -> pd.Series:
        x = pd.Series(s, dtype=object).astype(str).str.strip()
        return x.replace({"": unknown, "nan": unknown, "None": unknown, "NaN": unknown})

    def _last_business_days(end_day, n=30):
        end_ts = pd.Timestamp(end_day)
        bdays = pd.bdate_range(end=end_ts, periods=n)
        return [d.date() for d in bdays]

    def _trend_arrow_color(delta: float, good_when_up: bool, eps: float):
        if delta > eps:
            return ("↑", "green" if good_when_up else "red")
        if delta < -eps:
            return ("↓", "red" if good_when_up else "green")
        return ("→", "gray")

    brands = (
        base["Product brand"].astype(str).str.strip()
        .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent", "<NA>": "Ukjent"})
        .replace({"Ukjent": pd.NA})   # så "Ukjent" ikke blir en egen fane
        .dropna()
        .unique()
        .tolist()
    )
    brands = sorted(brands, key=lambda x: x.casefold())

    if not brands:
        st.info("Fant ingen merker i inhouse-data.")
        st.stop()

    tabs = st.tabs(brands)

    # Pre-rens alt (for gjenbruk)
    d = df.copy()
    d["brand"] = _clean_text(d["Product brand"], unknown="Ukjent")
    d["tech"] = _clean_text(d["Service technician"], unknown="Ukjent")
    d["status_clean"] = _clean_text(d["Service status"], unknown="").str.casefold()

    d["received_dt"] = pd.to_datetime(d["Service date product received"], errors="coerce")  # innlevert
    d["repaired_dt"] = pd.to_datetime(d["Service repair date"], errors="coerce")
    d["status_dt"] = pd.to_datetime(d["Service status date"], errors="coerce")
    d["rep_date"] = d["repaired_dt"].dt.date

    end_day = pd.Timestamp(today)
    start_30 = end_day - pd.Timedelta(days=30)
    start_prev30 = end_day - pd.Timedelta(days=60)

    last_30_bd = _last_business_days(today, 30)
    prev_30_bd = _last_business_days(pd.Timestamp(last_30_bd[0]) - pd.Timedelta(days=1), 30)

    for tab, brand in zip(tabs, brands):
        with tab:
            # Åpne saker for dette merket (inhouse)
            b_open = d[(d["brand"].str.casefold() == brand.casefold()) & (d["repaired_dt"].isna())].copy()

            if b_open.empty:
                st.warning(f"Ingen enheter inne for {brand}.")
                continue

            # Gruppér status: slå sammen "Venter på ekstern part ..."
            raw_status = _clean_text(b_open["Service status"], unknown="Ukjent")
            mask = raw_status.str.casefold().str.match(r"^venter på ekstern part\b")
            b_open["status_group"] = raw_status.where(~mask, "Venter på ekstern part")

            # -----------------------------
            # KPI midt: TAT NÅ (ÅPNE) + trend på ferdige (30 vs forrige 30)
            # -----------------------------
            open_with_received = b_open.dropna(subset=["received_dt"]).copy()
            if open_with_received.empty:
                tat_now_avg = 0.0
                oldest_open_days = 0
            else:
                tat_open_days = (end_day - open_with_received["received_dt"]).dt.total_seconds() / 86400.0
                tat_open_days = tat_open_days[(tat_open_days >= 0) & tat_open_days.notna()]
                tat_now_avg = float(tat_open_days.mean()) if not tat_open_days.empty else 0.0
                oldest_open_days = int(tat_open_days.max()) if not tat_open_days.empty else 0

            # Trend måles på ferdige saker (for å gi stabil pil)
            closed_brand = d[
                (d["brand"].str.casefold() == brand.casefold())
                & d["received_dt"].notna()
                & d["repaired_dt"].notna()
            ].copy()

            closed_last = closed_brand[(closed_brand["repaired_dt"] > start_30) & (closed_brand["repaired_dt"] <= end_day)].copy()
            closed_prev = closed_brand[(closed_brand["repaired_dt"] > start_prev30) & (closed_brand["repaired_dt"] <= start_30)].copy()

            def _avg_tat_closed_days(x: pd.DataFrame) -> float:
                if x.empty:
                    return 0.0
                tat_days = (x["repaired_dt"] - x["received_dt"]).dt.total_seconds() / 86400.0
                tat_days = tat_days[(tat_days >= 0) & tat_days.notna()]
                return float(tat_days.mean()) if not tat_days.empty else 0.0

            tat_closed_last = _avg_tat_closed_days(closed_last)
            tat_closed_prev = _avg_tat_closed_days(closed_prev)
            tat_delta = tat_closed_last - tat_closed_prev

            # TAT trend: ned er bra
            tat_arrow, tat_color = _trend_arrow_color(tat_delta, good_when_up=False, eps=0.2)

            tat_value = f"{tat_now_avg:.1f} dager"
            tat_sub = f"{tat_arrow} {tat_delta:+.1f} (ferdig 30d vs forrige)  •  Eldste: {oldest_open_days}d"

            # -----------------------------
            # KPI høyre: Snitt reparert pr arbeidsdag (30 arb.dager) + trend
            # -----------------------------
            rep_brand = d[(d["brand"].str.casefold() == brand.casefold()) & d["rep_date"].notna()].copy()

            count_last = int(rep_brand[rep_brand["rep_date"].isin(last_30_bd)].shape[0])
            count_prev = int(rep_brand[rep_brand["rep_date"].isin(prev_30_bd)].shape[0])

            rep_avg_last = count_last / 30.0
            rep_avg_prev = count_prev / 30.0
            rep_delta = rep_avg_last - rep_avg_prev

            # reparert trend: opp er bra
            rep_arrow, rep_color = _trend_arrow_color(rep_delta, good_when_up=True, eps=0.05)

            rep_value = f"{rep_avg_last:.2f}"
            rep_sub = f"{rep_arrow} {rep_delta:+.2f} vs forrige 30"

            # KPI-kort (3 kolonner)
            k1, k2, k3 = st.columns(3)
            with k1:
                kpi(f"{brand} – Totalt inne", len(b_open))
            with k2:
                kpi("TAT nå (åpne saker)", tat_value, sub=tat_sub, sub_color=tat_color)
            with k3:
                kpi("Snitt reparert (30 arb.dager)", rep_value, sub=rep_sub, sub_color=rep_color)

            # --- Diagram 1: status (gruppert), sortert synkende ---
            status_df = (
                b_open["status_group"]
                .value_counts(dropna=False)
                .rename_axis("Status")
                .reset_index(name="Antall")
                .sort_values("Antall", ascending=False)
            )
            status_bar = px.bar(status_df, x="Status", y="Antall", text="Antall")
            status_bar.update_layout(xaxis_title="Status", yaxis_title="Antall")
            status_bar.update_traces(textposition="outside", cliponaxis=False)

            # --- Diagram 2: statusdato (kronologisk) ---
            date_df = (
                b_open.assign(date=pd.to_datetime(b_open["Service status date"], errors="coerce").dt.date)
                    .dropna(subset=["date"])
                    .groupby("date").size().reset_index(name="Antall")
                    .sort_values("date")
            )
            date_bar = px.bar(date_df, x="date", y="Antall", text="Antall")
            date_bar.update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
            date_bar.update_traces(textposition="outside", cliponaxis=False)

            two_cols(f"{brand} – Inhouse per status", status_bar,
                     f"{brand} – Inhouse per statusdato", date_bar)

            # Tabeller
            with st.expander("Åpne saker", expanded=False):
                # Bygg en enkel, sorterbar saksliste
                case_df = b_open.copy()

                # Sørg for nødvendige kolonner finnes
                for col in ["Service number", "Service status", "Service status date", "Service date product received"]:
                    if col not in case_df.columns:
                        case_df[col] = pd.NA
    
                # Parse datoer robust (hvis de allerede er datetime så går dette fint)
                case_df["Innlevert dato"] = pd.to_datetime(case_df["Service date product received"], errors="coerce")
                case_df["Statusdato"] = pd.to_datetime(case_df["Service status date"], errors="coerce")

                # Rens tekst
                case_df["Servicenr"] = (
                    pd.Series(case_df["Service number"], dtype=object).astype(str).str.strip()
                    .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})
                )
                case_df["Status"] = (
                pd.Series(case_df["Service status"], dtype=object).astype(str).str.strip()
                .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})
                )

                # Velg kolonner + sorter på eldste innlevert først
                out = case_df[["Servicenr", "Status", "Statusdato", "Innlevert dato"]].copy()
                out = out.sort_values(["Innlevert dato", "Statusdato"], ascending=[True, True], na_position="last")

                out["Innlevert dato"] = pd.to_datetime(out["Innlevert dato"], errors="coerce").dt.strftime("%d.%m.%Y")
                out["Statusdato"] = pd.to_datetime(out["Statusdato"], errors="coerce").dt.strftime("%d.%m.%Y")

                # Hvis du vil at tomme datoer skal vises tomt (ikke "NaT")
                out["Innlevert dato"] = out["Innlevert dato"].fillna("")
                out["Statusdato"] = out["Statusdato"].fillna("")

                st.dataframe(out, use_container_width=True, hide_index=True)


st.markdown("---")
