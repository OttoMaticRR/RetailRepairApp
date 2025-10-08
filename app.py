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
KPI_HEIGHT = 140  # endre ved behov (px)

st.markdown(f"""
<style>
:root {{
  --kpi-height: {KPI_HEIGHT}px;
}}

.kpi-card {{
  height: var(--kpi-height);          /* ← lik høyde på alle kort */
  display: flex;
  flex-direction: column;
  justify-content: center;            /* vertikal sentrering */
  background: #ffffff;                /* hvit bakgrunn i begge tema */
  color: #111827 !important;          /* mørk tekst på kortet */
  border-radius: 16px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.08);
  border: 1px solid rgba(0,0,0,0.04);
  padding: 18px 22px;
}}

.kpi-label {{
  font-size: 0.95rem;
  color: #6b7280 !important;
  margin-bottom: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}

.kpi-value {{
  font-size: 2.4rem;
  font-weight: 700;
  line-height: 1.1;
  color: #111827 !important;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}

.kpi-sub {{
  font-size: 0.9rem;
  color: #10b981 !important;
  margin-top: 6px;
  font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}

/* litt lavere kort på veldig smale skjermer */
@media (max-width: 640px) {{
  :root {{ --kpi-height: {max(100, KPI_HEIGHT-30)}px; }}
}}
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
            u = st.text_input("Brukernavn", value="", placeholder="RRDashboard")
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
    records = ws.get_all_records()
    df = pd.DataFrame(records)

    expected = [
        "Service status date",
        "Service status",
        "Service repair date",
        "Product brand",
        "Service technician",
    ]
    cols_lower = {c.lower(): c for c in df.columns}
    for wanted in expected:
        if wanted not in df.columns and wanted.lower() in cols_lower:
            df.rename(columns={cols_lower[wanted.lower()]: wanted}, inplace=True)
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    for dc in ["Service status date", "Service repair date"]:
        df[dc] = pd.to_datetime(df[dc], errors="coerce", dayfirst=True)

    for sc in ["Service status", "Product brand", "Service technician"]:
        df[sc] = df[sc].astype(str).str.strip()

    return df

# ---------------------
# Helpers
# ---------------------
def today_oslo():
    return datetime.now(TZ).date()

def filter_today(df, date_col):
    return df[df[date_col].dt.date == today_oslo()]

def kpi(label, value, sub=None):
    st.markdown(f"""
    <div class="kpi-card">
      <div class="kpi-label">{label}</div>
      <div class="kpi-value">{value if value is not None else 0}</div>
      {f'<div class="kpi-sub">{sub}</div>' if sub else ''}
    </div>
    """, unsafe_allow_html=True)

def two_cols(fig_left_title, fig_left, fig_right_title, fig_right):
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(fig_left_title)
        st.plotly_chart(fig_left, use_container_width=True)
    with c2:
        st.subheader(fig_right_title)
        st.plotly_chart(fig_right, use_container_width=True)

# ---------------------
# Data
# ---------------------
df = fetch_data()
today = today_oslo()

# ---------------------
# Sidebar menu
# ---------------------
with st.sidebar:
    selected = option_menu(
        None,
        ["Reparert", "Innlevert", "Inhouse", "Arbeidet på"],
        icons=["bag-fill", "box-seam", "house-door-fill", "hammer"],
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
st.title(selected)
st.caption(f"Dato: {today} • Tidssone: {TZ_NAME} • Oppdateres hver 5. min (cache)")

# ---------------------
# Views
# ---------------------
if selected == "Reparert":
    repaired_today = filter_today(df, "Service repair date")
    total_repaired = len(repaired_today)
    n_distinct_brands = repaired_today["Product brand"].replace(["", "nan"], pd.NA).dropna().nunique()
    tech_counts = repaired_today["Service technician"].value_counts()
    top_tech = tech_counts.index[0] if not tech_counts.empty else "-"
    top_tech_count = int(tech_counts.iloc[0]) if not tech_counts.empty else 0

    k1, k2, k3 = st.columns(3)
    with k1: kpi("Total Repairs", total_repaired)
    with k2: kpi("Brands", n_distinct_brands)
    with k3: kpi("Top Technician", top_tech, sub=(f"↑ {top_tech_count} repairs" if top_tech != "-" else None))

    if not repaired_today.empty:
        brand_bar = px.bar(
            repaired_today.groupby("Product brand").size().reset_index(name="Repairs"),
            x="Product brand", y="Repairs", text="Repairs"
        ).update_layout(xaxis_title="Brand", yaxis_title="Repairs")
        tech_pie = px.pie(
            repaired_today.groupby("Service technician").size().reset_index(name="Repairs"),
            names="Service technician", values="Repairs", hole=0.35
        )
    else:
        brand_bar = px.bar(pd.DataFrame({"Product brand": [], "Repairs": []}), x="Product brand", y="Repairs")
        tech_pie = px.pie(pd.DataFrame({"Service technician": [], "Repairs": []}), names="Service technician", values="Repairs")

    two_cols("Repairs by Brand", brand_bar, "Repairs by Technician", tech_pie)

elif selected == "Innlevert":
    delivered = df[df["Service status"].str.lower() == "innlevert"]
    delivered_today = delivered[delivered["Service status date"].dt.date == today]

    k1, k2 = st.columns(2)
    with k1: kpi("Totalt innlevert", len(delivered))
    with k2: kpi("Innlevert i dag", len(delivered_today))

    if not delivered.empty:
        brand_bar = px.bar(
            delivered.groupby("Product brand").size().reset_index(name="Antall"),
            x="Product brand", y="Antall", text="Antall"
        ).update_layout(xaxis_title="Merke", yaxis_title="Antall")
        date_bar = px.bar(
            delivered.assign(date=delivered["Service status date"].dt.date).groupby("date").size().reset_index(name="Antall"),
            x="date", y="Antall", text="Antall"
        ).update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
    else:
        brand_bar = px.bar(pd.DataFrame({"Product brand": [], "Antall": []}), x="Product brand", y="Antall")
        date_bar = px.bar(pd.DataFrame({"date": [], "Antall": []}), x="date", y="Antall")

    two_cols("Innlevert per merke (alle)", brand_bar, "Innlevert per statusdato (alle)", date_bar)

elif selected == "Inhouse":
    inhouse = df[df["Service repair date"].isna()]
    if not inhouse.empty:
        tb = inhouse["Product brand"].value_counts()
        top_brand, top_brand_count = tb.index[0], int(tb.iloc[0])
    else:
        top_brand, top_brand_count = "-", 0

    k1, k2 = st.columns(2)
    with k1: kpi("Totalt inhouse (ingen reparasjonsdato)", len(inhouse))
    with k2: kpi("Topp merke (inhouse)", top_brand, sub=(f"{top_brand_count} enheter" if top_brand != "-" else None))

    if not inhouse.empty:
        status_bar = px.bar(
            inhouse.groupby("Service status").size().reset_index(name="Antall"),
            x="Service status", y="Antall", text="Antall"
        ).update_layout(xaxis_title="Status", yaxis_title="Antall")
        date_bar = px.bar(
            inhouse.assign(date=inhouse["Service status date"].dt.date).groupby("date").size().reset_index(name="Antall"),
            x="date", y="Antall", text="Antall"
        ).update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
    else:
        status_bar = px.bar(pd.DataFrame({"Service status": [], "Antall": []}), x="Service status", y="Antall")
        date_bar = px.bar(pd.DataFrame({"date": [], "Antall": []}), x="date", y="Antall")

    two_cols("Inhouse per status", status_bar, "Inhouse per statusdato", date_bar)

elif selected == "Arbeidet på":
    worked_today = df[(df["Service status date"].dt.date == today) & (df["Service status"].str.lower() != "innlevert")]

    if not worked_today.empty:
        ts = worked_today["Service status"].value_counts()
        top_status, top_status_count = ts.index[0], int(ts.iloc[0])
        tt = worked_today["Service technician"].value_counts()
        top_tech2, top_tech2_count = tt.index[0], int(tt.iloc[0])
    else:
        top_status, top_status_count = "-", 0
        top_tech2, top_tech2_count = "-", 0

    k1, k2, k3 = st.columns(3)
    with k1: kpi("Totalt arbeidet på i dag", len(worked_today))
    with k2: kpi("Mest satte status (i dag)", top_status, sub=(f"{top_status_count} enheter" if top_status != "-" else None))
    with k3: kpi("Topp tekniker (i dag)", top_tech2, sub=(f"{top_tech2_count} enheter" if top_tech2 != "-" else None))

    if not worked_today.empty:
        brand_bar2 = px.bar(
            worked_today.groupby("Product brand").size().reset_index(name="Antall"),
            x="Product brand", y="Antall", text="Antall"
        ).update_layout(xaxis_title="Merke", yaxis_title="Antall")
        status_bar2 = px.bar(
            worked_today.groupby("Service status").size().reset_index(name="Antall"),
            x="Service status", y="Antall", text="Antall"
        ).update_layout(xaxis_title="Status", yaxis_title="Antall")
    else:
        brand_bar2 = px.bar(pd.DataFrame({"Product brand": [], "Antall": []}), x="Product brand", y="Antall")
        status_bar2 = px.bar(pd.DataFrame({"Service status": [], "Antall": []}), x="Service status", y="Antall")

    two_cols("Arbeidet på (i dag) per merke", brand_bar2, "Arbeidet på (i dag) per status", status_bar2)

st.markdown("---")
st.caption("Bygget med Streamlit • Sikker innlogging • Data fra Google Sheets • Cache 5 min")
