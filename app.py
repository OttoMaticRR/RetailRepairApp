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

    values = ws.get_all_values()  # <- henter “rått” fra Sheets
    if not values:
        return pd.DataFrame()

    def norm_header(x: str) -> str:
        x = str(x)
        x = x.replace("\u00A0", " ")   # NBSP
        x = x.replace("\u200b", "")    # zero-width
        x = x.replace("\ufeff", "")    # BOM
        x = " ".join(x.split())        # kollaps multiple spaces
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
        "Product brand",
        "Service technician",
    ]

    # Case-insensitive rename med normalisering
    cols_norm = {norm_header(c).casefold(): c for c in df.columns}
    for wanted in expected:
        key = wanted.casefold()
        if wanted not in df.columns and key in cols_norm:
            df.rename(columns={cols_norm[key]: wanted}, inplace=True)

    # Hvis fortsatt mangler (da er det faktisk feil header i arket)
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    def clean_date_series(s: pd.Series) -> pd.Series:
        # rens tekst før parsing
        txt = (
            s.astype(str)
             .str.replace("\u00A0", " ", regex=False)
             .str.replace("\u200b", "", regex=False)
             .str.replace("\ufeff", "", regex=False)
             .str.replace(",", " ", regex=False)
             .str.replace(r"\s+", " ", regex=True)
             .str.strip()
        )
        txt = txt.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NaN": pd.NA})

        # 1) vanlig parsing
        dt = pd.to_datetime(txt, errors="coerce", dayfirst=True)

        # 2) fallback: Excel/Sheets serial
        num = pd.to_numeric(txt, errors="coerce")
        serial_mask = dt.isna() & num.notna() & (num > 20000) & (num < 60000)
        if serial_mask.any():
            dt.loc[serial_mask] = pd.to_datetime(
                num.loc[serial_mask], unit="D", origin="1899-12-30"
            )

        return dt

    for dc in ["Service status date", "Service repair date"]:
        df[dc] = clean_date_series(df[dc])

    # trim tekstkolonner
    for sc in ["Service status", "Product brand", "Service technician"]:
        df[sc] = df[sc].astype(str).str.replace("\u00A0", " ", regex=False).str.strip()

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
    # 1) Tving til "object"-strenger (ikke Pandas StringDtype)
    s = pd.Series(series, dtype=object).astype(str).str.strip()

    # 2) Erstatt tomt/NA med "Ukjent"
    s = s.replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})

    # 3) Tell og bygg tabell
    out = (
        s.value_counts(dropna=False)
         .rename_axis(left_header)
         .reset_index(name=right_header)
    )

    # 4) Nummerkolonne og sikre enkle dtypes
    out.insert(0, "Nr", range(1, len(out) + 1))
    out = out.astype({"Nr": int, right_header: int})
    # Sørg for at tekstkolonnen faktisk er ren 'object'-str
    out[left_header] = out[left_header].astype(object)
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



# ---------------------
# Data
# ---------------------
df = fetch_data()

default_day = latest_date_in_data(df)

with st.sidebar:
    c_date, c_today = st.columns([4, 1])

    with c_date:
        selected_day = st.date_input("Vis dato", value=default_day, key="selected_day")

    with c_today:
        st.write("")
        st.write("")
        if st.button("I dag", use_container_width=True):
            st.session_state["selected_day"] = today_oslo()   # <-- HER: faktisk i dag
            st.rerun()

today = selected_day


# ---------------------
# Sidebar menu
# ---------------------
with st.sidebar:
    selected = option_menu(
        None,
        ["Reparert", "Innlevert", "Inhouse", "Arbeidet på", "Historikk", "Teknikere", "Kunder"],
        icons=["bag-fill", "box-seam", "house-door-fill", "hammer", "calendar3", "people-fill", "people"],
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
if selected == "Reparert":
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

    two_cols("Innlevert per merke (alle)", brand_bar, "Innlevert per statusdato (alle)", date_bar)




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
    # --- Finn tilgjengelige datoer i datasettet ---
    status_dates = df["Service status date"]

    # Hvis dato-kolonnen er helt tom/NaT, si ifra tydelig
    if status_dates.isna().all():
        st.error("Fant ingen gyldige datoer i kolonnen 'Service status date'. "
                 "Da blir alle 'i dag'-faner tomme. Sjekk at datoene faktisk finnes i arket, "
                 "og at kolonnenavnet er korrekt.")
        st.stop()

    available_dates = (
        pd.Series(status_dates.dropna().dt.date.unique())
        .dropna()
        .sort_values()
        .tolist()
    )

    if not available_dates:
        st.error("Fant ingen datoer å vise (available_dates er tom).")
        st.stop()

    # Default: bruk i dag hvis den finnes i data, ellers siste dato med data
    default_date = today if today in available_dates else available_dates[-1]

    # Velg dato (så du kan se “i dag” eller bla bakover)
    chosen_date = st.select_slider(
        "Velg dato for 'Arbeidet på'",
        options=available_dates,
        value=default_date,
        format_func=lambda d: d.strftime("%d.%m.%Y"),
    )

    if chosen_date != today:
        st.info(f"Ingen/ikke valgt data for {today:%d.%m.%Y}. Viser {chosen_date:%d.%m.%Y} i stedet.")

    # --- Filtrer saker denne datoen som IKKE er 'Innlevert' ---
    wt = df[
        (df["Service status date"].dt.date == chosen_date)
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
        tech_counts = wt["Service technician"].value_counts(dropna=False)
        top_tech2 = tech_counts.index[0] if not tech_counts.empty else "-"
        top_tech2_count = int(tech_counts.iloc[0]) if not tech_counts.empty else 0
    else:
        top_status, top_status_count = "-", 0
        top_tech2, top_tech2_count = "-", 0

    # KPI-kort
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Totalt arbeidet på (valgt dato)", len(wt))
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
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent", "NaN": "Ukjent"})
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

    two_cols("Arbeidet på (valgt dato) per merke", brand_bar2,
             "Arbeidet på (valgt dato) per status", status_bar2)

    # Tabeller
    with st.expander("Vis tabell", expanded=False):
        c1, c2 = st.columns(2)

        tbl_brand2 = _counts_table(wt["Product brand"], "Brand", "Enheter")
        c1.markdown("#### Arbeidet på (valgt dato) per merke")
        c1.dataframe(tbl_brand2, use_container_width=True, hide_index=True)

        tbl_tech2 = _counts_table(wt["Service technician"], "Technician", "Enheter")
        c2.markdown("#### Arbeidet på (valgt dato) per tekniker")
        c2.dataframe(tbl_tech2, use_container_width=True, hide_index=True)




elif selected == "Historikk":
    rep_series = df["Service repair date"]

    # Robust konvertering til dato (tz-aware eller ikke)
    if pd.api.types.is_datetime64tz_dtype(rep_series):
        date_only = rep_series.dt.tz_convert(TZ_NAME).dt.date
    else:
        date_only = rep_series.dt.date

    # Antall reparerte per dato
    hist = (
        pd.DataFrame({"date": date_only})
        .dropna()
        .value_counts("date")
        .reset_index(name="Repairs")
        .sort_values("date")
    )

    if hist.empty:
        st.info("Ingen reparasjoner i datasettet.")
    else:
        # --- KUN linjediagrammet filtreres ---
        min_repairs = 2  # vis bare dager med >= 2 i grafen
        hist_for_chart = hist[hist["Repairs"] >= min_repairs]

        if hist_for_chart.empty:
            st.info(f"Ingen dager med {min_repairs} eller flere reparasjoner å vise i grafen.")
        else:
            fig_hist = px.line(hist_for_chart, x="date", y="Repairs", markers=True)
            fig_hist.update_layout(xaxis_title="Dato", yaxis_title="Antall reparert", hovermode="x unified")
            st.plotly_chart(fig_hist, use_container_width=True)

        # --- Slider og tabeller bruker ALLE datoene (ufiltrert) ---
        all_dates = hist["date"].tolist()
        selected_date = st.select_slider(
            "Velg dato",
            options=all_dates,
            value=all_dates[-1],
            format_func=lambda d: d.strftime("%d.%m.%Y"),
        )

        day_df = df[date_only == selected_date]

        with st.expander("Tabeller for valgt dato", expanded=True):
            c1, c2 = st.columns(2)
            c1.markdown(f"#### Reparert per merke ({selected_date:%d.%m.%Y})")
            c1.dataframe(_counts_table(day_df["Product brand"], "Brand", "Repairs"),
                         use_container_width=True, hide_index=True)
            c2.markdown(f"#### Reparert per tekniker ({selected_date:%d.%m.%Y})")
            c2.dataframe(_counts_table(day_df["Service technician"], "Technician", "Repairs"),
                         use_container_width=True, hide_index=True)


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
    base = df[df["Service repair date"].isna()].copy()

    if base.empty:
        st.info("Ingen enheter i inhouse.")
        st.stop()

    brands = (
        base["Product brand"].astype(str).str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NaN": pd.NA})
        .dropna()
        .unique()
        .tolist()
    )
    brands = sorted(brands, key=lambda x: x.casefold())

    if not brands:
        st.info("Fant ingen merker i inhouse-data.")
        st.stop()

    tabs = st.tabs(brands)

    def _last_business_days(end_day, n=30):
        end_ts = pd.Timestamp(end_day)
        bdays = pd.bdate_range(end=end_ts, periods=n)
        return [d.date() for d in bdays]

    for tab, brand in zip(tabs, brands):
        with tab:
            bdf = base[
                base["Product brand"].astype(str).str.strip().str.casefold() == brand.casefold()
            ].copy()

            if bdf.empty:
                st.warning(f"Ingen enheter inne for {brand}.")
                continue

            # Gruppér status: slå sammen "Venter på ekstern part ..."
            raw_status = bdf["Service status"].astype(str).str.strip()
            mask = raw_status.str.casefold().str.match(r"^venter på ekstern part\b")
            bdf["status_group"] = raw_status.where(~mask, "Venter på ekstern part")

            # -----------------------------
            # KPI (midt): Snitt TAT 30 dager + trend vs forrige 30
            # -----------------------------
            DEL_COL = "Service date product received"
            end_day = pd.Timestamp(today)
            start_30 = end_day - pd.Timedelta(days=30)
            start_prev30 = end_day - pd.Timedelta(days=60)

            tat_df = df[
                df["Product brand"].astype(str).str.strip().str.casefold() == brand.casefold()
            ].copy()

            tat_df["delivered_dt"] = pd.to_datetime(tat_df[DEL_COL], errors="coerce")
            tat_df["repaired_dt"] = pd.to_datetime(tat_df["Service repair date"], errors="coerce")

            last30_tat = tat_df[(tat_df["delivered_dt"] >= start_30) & (tat_df["delivered_dt"] <= end_day)].copy()
            prev30_tat = tat_df[(tat_df["delivered_dt"] >= start_prev30) & (tat_df["delivered_dt"] < start_30)].copy()

            def calc_avg_tat_days(d):
                if d.empty:
                    return 0.0
                d = d.dropna(subset=["delivered_dt"]).copy()
                if d.empty:
                    return 0.0
                end_dt = d["repaired_dt"].fillna(end_day)
                tat_days = (end_dt - d["delivered_dt"]).dt.total_seconds() / 86400.0
                tat_days = tat_days[(tat_days >= 0) & tat_days.notna()]
                return float(tat_days.mean()) if not tat_days.empty else 0.0

            tat_avg_last = calc_avg_tat_days(last30_tat)
            tat_avg_prev = calc_avg_tat_days(prev30_tat)
            tat_delta = tat_avg_last - tat_avg_prev

            if tat_delta > 0.05:
                tat_arrow = "↑"
                tat_color = "red"    # TAT opp = dårlig
            elif tat_delta < -0.05:
                tat_arrow = "↓"
                tat_color = "green"  # TAT ned = bra
            else:
                tat_arrow = "→"
                tat_color = "gray"

            tat_value = f"{tat_avg_last:.1f} dager"
            tat_sub = f"{tat_arrow} {tat_delta:+.1f} dager vs forrige 30"

            # -----------------------------
            # KPI (høyre): Snitt reparert pr arbeidsdag (30 arb.dager) + trend vs forrige 30
            # -----------------------------
            last_30_bd = _last_business_days(today, 30)
            prev_30_bd = _last_business_days(pd.Timestamp(last_30_bd[0]) - pd.Timedelta(days=1), 30)

            rep_brand = df.dropna(subset=["Service repair date"]).copy()
            rep_brand = rep_brand[
                rep_brand["Product brand"].astype(str).str.strip().str.casefold() == brand.casefold()
            ].copy()

            rep_brand["rep_date"] = pd.to_datetime(rep_brand["Service repair date"], errors="coerce").dt.date
            rep_brand = rep_brand.dropna(subset=["rep_date"])

            rep_count_last = int(rep_brand[rep_brand["rep_date"].isin(last_30_bd)].shape[0])
            rep_avg_last = rep_count_last / 30.0

            rep_count_prev = int(rep_brand[rep_brand["rep_date"].isin(prev_30_bd)].shape[0])
            rep_avg_prev = rep_count_prev / 30.0

            rep_delta = rep_avg_last - rep_avg_prev

            if rep_delta > 0.001:
                rep_arrow = "↑"
                rep_color = "green"  # reparert opp = bra
            elif rep_delta < -0.001:
                rep_arrow = "↓"
                rep_color = "red"    # reparert ned = dårlig
            else:
                rep_arrow = "→"
                rep_color = "gray"

            rep_value = f"{rep_avg_last:.2f}"
            rep_sub = f"{rep_arrow} {rep_delta:+.2f} vs forrige 30"

            # -----------------------------
            # KPI-kort
            # -----------------------------
            k1, k2, k3 = st.columns(3)
            with k1:
                kpi(f"{brand} – Totalt inne", len(bdf))
            with k2:
                kpi("Snitt TAT (30 dager)", tat_value, sub=tat_sub, sub_color=tat_color)
            with k3:
                kpi("Snitt reparert (30 arb.dager)", rep_value, sub=rep_sub, sub_color=rep_color)

            # -----------------------------
            # Diagrammer
            # -----------------------------
            status_df = (
                bdf["status_group"]
                .value_counts(dropna=False)
                .rename_axis("Status")
                .reset_index(name="Antall")
                .sort_values("Antall", ascending=False)
            )
            status_bar = px.bar(status_df, x="Status", y="Antall", text="Antall")
            status_bar.update_layout(xaxis_title="Status", yaxis_title="Antall")
            status_bar.update_traces(textposition="outside", cliponaxis=False)

            date_df = (
                bdf.assign(date=pd.to_datetime(bdf["Service status date"], errors="coerce").dt.date)
                   .dropna(subset=["date"])
                   .groupby("date").size().reset_index(name="Antall")
                   .sort_values("date")
            )
            date_bar = px.bar(date_df, x="date", y="Antall", text="Antall")
            date_bar.update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
            date_bar.update_traces(textposition="outside", cliponaxis=False)

            two_cols(f"{brand} – Inhouse per status", status_bar,
                     f"{brand} – Inhouse per statusdato", date_bar)

            with st.expander("Vis tabell", expanded=False):
                c1, c2 = st.columns(2)

                c1.markdown("#### Status (antall)")
                c1.dataframe(
                    _counts_table(bdf["status_group"], "Status", "Antall"),
                    use_container_width=True,
                    hide_index=True,
                )

                c2.markdown("#### Tekniker (antall)")
                c2.dataframe(
                    _counts_table(bdf["Service technician"], "Tekniker", "Antall"),
                    use_container_width=True,
                    hide_index=True,
                )


st.markdown("---")
