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
  align-items: center;            /* horisontalt midtstilt */
  text-align: center;             /* midtstill tekst */
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

.kpi-sub {{
  font-size: 0.9rem;
  color: #10b981 !important;
  margin-top: 6px;
  font-weight: 600;
  text-align: center;
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
    records = ws.get_all_records()
    df = pd.DataFrame(records)

    # 🔧 VIKTIG: fjern skjulte mellomrom i kolonnenavn fra Sheets
    df.columns = df.columns.astype(str).str.strip()

    expected = [
        "Service status date",
        "Service status",
        "Service repair date",
        "Product brand",
        "Service technician",
    ]

    # Rename case-insensitive (etter stripping)
    cols_lower = {c.lower(): c for c in df.columns}
    for wanted in expected:
        if wanted not in df.columns and wanted.lower() in cols_lower:
            df.rename(columns={cols_lower[wanted.lower()]: wanted}, inplace=True)

    # Lag manglende kolonner hvis de faktisk ikke finnes
    for col in expected:
        if col not in df.columns:
            df[col] = pd.NA

    # Robust dato-parse (tåler både tekst-dato og tall/serial fra Sheets)
    def _parse_sheet_datetime(s: pd.Series) -> pd.Series:
        s_raw = pd.Series(s)

        # 1) vanlig parsing (dd.mm.yyyy osv)
        dt = pd.to_datetime(s_raw, errors="coerce", dayfirst=True)

        # 2) fallback: Google/Excel-serial (dager siden 1899-12-30)
        num = pd.to_numeric(s_raw, errors="coerce")
        serial_mask = dt.isna() & num.notna() & (num > 20000) & (num < 60000)
        if serial_mask.any():
            dt.loc[serial_mask] = pd.to_datetime(
                num.loc[serial_mask], unit="D", origin="1899-12-30"
            )

        return dt

    for dc in ["Service status date", "Service repair date"]:
        df[dc] = _parse_sheet_datetime(df[dc])

    # Trim tekstkolonner (men behold dato-kolonner som datetime)
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

    return out



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
        ["Reparert", "Innlevert", "Inhouse", "Arbeidet på", "Historikk", "Teknikere"],
        icons=["bag-fill", "box-seam", "house-door-fill", "hammer", "calendar3", "people-fill"],
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
    repaired_today = filter_today(df, "Service repair date")
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
    # Filtrer "Innlevert"
    delivered = df[df["Service status"].astype(str).str.strip().str.casefold() == "innlevert"].copy()
    delivered_today = delivered[delivered["Service status date"].dt.date == today]

    # Finn topp merke blant alle "Innlevert"
    if not delivered.empty:
        brand_counts = (
            delivered["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent"})
            .value_counts()
        )
        top_brand = brand_counts.index[0]
        top_brand_count = int(brand_counts.iloc[0])
    else:
        top_brand, top_brand_count = "-", 0

    # Tre KPI-kort
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Totalt innlevert", len(delivered))
    with k2:
        kpi("Innlevert i dag", len(delivered_today))
    with k3:
        kpi("Topp merke", top_brand,
            sub=(f"{top_brand_count} enheter" if top_brand != "-" else None))

    # Diagrammer
    if not delivered.empty:
        # Merke — sortert synkende på antall
        brand_df = (
            delivered["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent"})
            .value_counts()
            .rename_axis("Product brand")
            .reset_index(name="Antall")
            .sort_values("Antall", ascending=False)
        )
        brand_bar = px.bar(brand_df, x="Product brand", y="Antall", text="Antall")
        brand_bar.update_layout(
            xaxis_title="Merke",
            yaxis_title="Antall",
            xaxis={
                "categoryorder": "array",
                "categoryarray": brand_df["Product brand"].tolist(),
            },
        )
        brand_bar.update_traces(textposition="outside", cliponaxis=False)

        # Statusdato — behold kronologisk
        date_df = (
            delivered.assign(date=delivered["Service status date"].dt.date)
                     .groupby("date").size().reset_index(name="Antall")
                     .sort_values("date")
        )
        date_bar = px.bar(date_df, x="date", y="Antall", text="Antall")
        date_bar.update_layout(xaxis_title="Statusdato", yaxis_title="Antall")
        date_bar.update_traces(textposition="outside", cliponaxis=False)
    else:
        brand_bar = px.bar(pd.DataFrame({"Product brand": [], "Antall": []}),
                           x="Product brand", y="Antall")
        date_bar  = px.bar(pd.DataFrame({"date": [], "Antall": []}),
                           x="date", y="Antall")

    two_cols("Merke", brand_bar, "Dato", date_bar)



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
    # Dagens saker som IKKE er "Innlevert"
    wt = df[
        (df["Service status date"].dt.date == today)
        & (df["Service status"].str.lower() != "innlevert")
    ].copy()

    # KPI-beregninger + grouping av status
    if not wt.empty:
        raw_status = wt["Service status"].astype(str).str.strip()
        low = raw_status.str.casefold()

        # Samle alle som begynner med "Venter på ekstern part ..."
        mask = low.str.match(r"^venter på ekstern part\b")
        wt["status_group"] = raw_status.where(~mask, "Venter på ekstern part")

        # KPI: mest satte status
        status_counts = wt["status_group"].value_counts()
        top_status = status_counts.index[0] if not status_counts.empty else "-"
        top_status_count = int(status_counts.iloc[0]) if not status_counts.empty else 0

        # KPI: topp tekniker i dag
        tech_counts = wt["Service technician"].value_counts()
        top_tech2 = tech_counts.index[0] if not tech_counts.empty else "-"
        top_tech2_count = int(tech_counts.iloc[0]) if not tech_counts.empty else 0
    else:
        top_status, top_status_count = "-", 0
        top_tech2, top_tech2_count = "-", 0

    # KPI-kort
    k1, k2, k3 = st.columns(3)
    with k1:
        kpi("Totalt arbeidet på i dag", len(wt))
    with k2:
        kpi("Mest satte status i dag", top_status,
            sub=(f"{top_status_count} enheter" if top_status != "-" else None))
    with k3:
        kpi("Topp tekniker i dag", top_tech2,
            sub=(f"{top_tech2_count} enheter" if top_tech2 != "-" else None))

    # Diagrammer
    if not wt.empty:
        # --- VENSTRE: per merke (sortert synkende) ---
        brand_df2 = (
            wt["Product brand"]
            .astype(str).str.strip()
            .replace({"": "Ukjent", "nan": "Ukjent", "None": "Ukjent"})
            .value_counts()
            .rename_axis("Product brand")
            .reset_index(name="Antall")
            .sort_values("Antall", ascending=False)
        )
        brand_bar2 = px.bar(brand_df2, x="Product brand", y="Antall", text="Antall")
        brand_bar2.update_layout(
            xaxis_title="Merke",
            yaxis_title="Antall",
            xaxis={
                # Lås rekkefølgen slik vi har sortert dataframe
                "categoryorder": "array",
                "categoryarray": brand_df2["Product brand"].tolist(),
            },
        )
        brand_bar2.update_traces(textposition="outside", cliponaxis=False)

        # --- HØYRE: per status (gruppert + sortert synkende) ---
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
        brand_bar2 = px.bar(pd.DataFrame({"Product brand": [], "Antall": []}),
                            x="Product brand", y="Antall")
        status_bar2 = px.bar(pd.DataFrame({"Status": [], "Antall": []}),
                             x="Status", y="Antall")

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
        rep["rep_date"] = rep["Service repair date"].dt.date

        max_date = rep["rep_date"].max()
        max_ts = pd.to_datetime(max_date)

        days_7 = 7
        days_30 = 30
        cutoff_7 = (max_ts - pd.Timedelta(days=days_7 - 1)).date()
        cutoff_30 = (max_ts - pd.Timedelta(days=days_30 - 1)).date()

        rep_7 = rep[rep["rep_date"] >= cutoff_7]
        rep_30 = rep[rep["rep_date"] >= cutoff_30]

        total_7 = rep_7["Tekniker"].value_counts()
        total_30 = rep_30["Tekniker"].value_counts()

        # Snitt per dag (kalenderdager)
        avg_7 = (total_7 / days_7).to_frame("Snitt 7 dager")
        avg_30 = (total_30 / days_30).to_frame("Snitt 30 dager")

        tech_tbl = avg_7.join(avg_30, how="outer").fillna(0).reset_index()

        # Robust: uansett hva første kolonne heter, kall den "Tekniker"
        tech_tbl.rename(columns={tech_tbl.columns[0]: "Tekniker"}, inplace=True)

        tech_tbl["Trend (7d-30d)"] = tech_tbl["Snitt 7 dager"] - tech_tbl["Snitt 30 dager"]
        tech_tbl = tech_tbl.sort_values("Snitt 7 dager", ascending=False)

        # KPI
        if not tech_tbl.empty:
            top_name = str(tech_tbl.iloc[0]["Tekniker"])
            top_avg7 = float(tech_tbl.iloc[0]["Snitt 7 dager"])
        else:
            top_name, top_avg7 = "-", 0.0

        k1, k2, k3 = st.columns(3)
        with k1:
            kpi("Siste reparasjonsdato", f"{max_date:%d.%m.%Y}")
        with k2:
            kpi("Topp tekniker (snitt 7d)", top_name, sub=f"{top_avg7:.2f} pr dag")
        with k3:
            kpi("Antall teknikere", int(tech_tbl["Tekniker"].nunique()))

        # Graf 1: snitt 7 vs 30
        plot_df = tech_tbl.melt(
            id_vars=["Tekniker", "Trend (7d-30d)"],
            value_vars=["Snitt 7 dager", "Snitt 30 dager"],
            var_name="Periode",
            value_name="Snitt per dag",
        )

        bar = px.bar(plot_df, x="Tekniker", y="Snitt per dag", color="Periode", barmode="group", text="Snitt per dag")
        bar.update_traces(texttemplate="%{text:.2f}", textposition="outside", cliponaxis=False)
        bar.update_layout(xaxis_title="Tekniker", yaxis_title="Snitt reparert per dag", legend_title="Periode")
        st.subheader("Snitt reparert per dag – 7 vs 30 dager")
        st.plotly_chart(bar, use_container_width=True)

        # Graf 2: trend (positiv/negativ)
        trend_df = tech_tbl[["Tekniker", "Trend (7d-30d)"]].copy().sort_values("Trend (7d-30d)", ascending=False)
        trend_bar = px.bar(trend_df, x="Tekniker", y="Trend (7d-30d)", text="Trend (7d-30d)")
        trend_bar.update_traces(texttemplate="%{text:.2f}", textposition="outside", cliponaxis=False)
        trend_bar.update_layout(xaxis_title="Tekniker", yaxis_title="Trend (snitt 7d minus snitt 30d)")
        st.subheader("Trend – positiv eller negativ")
        st.plotly_chart(trend_bar, use_container_width=True)

        # Tabell (safe dtypes)
        tech_tbl_out = tech_tbl.copy()
        tech_tbl_out["Tekniker"] = tech_tbl_out["Tekniker"].astype(object)
        tech_tbl_out["Snitt 7 dager"] = tech_tbl_out["Snitt 7 dager"].astype(float).round(2)
        tech_tbl_out["Snitt 30 dager"] = tech_tbl_out["Snitt 30 dager"].astype(float).round(2)
        tech_tbl_out["Trend (7d-30d)"] = tech_tbl_out["Trend (7d-30d)"].astype(float).round(2)

        with st.expander("Vis tabell", expanded=False):
            st.dataframe(tech_tbl_out, use_container_width=True, hide_index=True)



st.markdown("---")
