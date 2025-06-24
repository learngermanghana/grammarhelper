import os
import random
import difflib
import sqlite3
import atexit
from datetime import date, datetime
import pandas as pd
import streamlit as st
import requests
import io  # Needed for StringIO (robust CSV reading)
from openai import OpenAI
from fpdf import FPDF
from streamlit_cookies_manager import EncryptedCookieManager


# ---- OpenAI Client Setup ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    st.error(
        "Missing OpenAI API key. Please set OPENAI_API_KEY as an environment variable or in Streamlit secrets."
    )
    st.stop()
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY   # <- Set for OpenAI client!
client = OpenAI()  # <-- Do NOT pass api_key here for openai>=1.0

# ---- Paste the DB connection helper here ----

def get_connection():
    if "conn" not in st.session_state:
        st.session_state["conn"] = sqlite3.connect("vocab_progress.db", check_same_thread=False)
        atexit.register(st.session_state["conn"].close)
    return st.session_state["conn"]

conn = get_connection()
c = conn.cursor()

def get_student_stats(student_code):
    conn = get_connection()
    c = conn.cursor()
    # Group by level, count correct and attempted for each
    c.execute("""
        SELECT level, SUM(score >= 17), COUNT(*) 
        FROM schreiben_progress 
        WHERE student_code=?
        GROUP BY level
    """, (student_code,))
    stats = {}
    for level, correct, attempted in c.fetchall():
        stats[level] = {"correct": int(correct or 0), "attempted": int(attempted or 0)}
    return stats

def get_vocab_streak(student_code):
    """Return the number of consecutive days with vocab submissions."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT DISTINCT date FROM vocab_progress WHERE student_code=? ORDER BY date DESC",
        (student_code,),
    )
    rows = c.fetchall()
    if not rows:
        return 0

    dates = [date.fromisoformat(r[0]) for r in rows]

    # If the most recent submission wasn't today or yesterday, streak is lost
    if (date.today() - dates[0]).days > 1:
        return 0

    streak = 1
    prev = dates[0]
    for d in dates[1:]:
        if (prev - d).days == 1:
            streak += 1
            prev = d
        else:
            break

    return streak


# --- Create/verify tables if not exist (run once per app startup) ---
def init_db():
    conn = get_connection()
    c = conn.cursor()
    # Vocab Progress Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS vocab_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_code TEXT,
            name TEXT,
            level TEXT,
            word TEXT,
            student_answer TEXT,
            is_correct INTEGER,
            date TEXT
        )
    """)
    # Schreiben Progress Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS schreiben_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_code TEXT,
            name TEXT,
            level TEXT,
            essay TEXT,
            score INTEGER,
            feedback TEXT,
            date TEXT
        )
    """)
    conn.commit()

init_db()

def save_vocab_submission(student_code, name, level, word, student_answer, is_correct):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO vocab_progress (student_code, name, level, word, student_answer, is_correct, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (student_code, name, level, word, student_answer, int(is_correct), str(date.today()))
    )
    conn.commit()

def save_schreiben_submission(student_code, name, level, essay, score, feedback):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO schreiben_progress (student_code, name, level, essay, score, feedback, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (student_code, name, level, essay, score, feedback, str(date.today()))
    )
    conn.commit()

def get_writing_stats(student_code):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*), SUM(score>=17) FROM schreiben_progress WHERE student_code=?
    """, (student_code,))
    result = c.fetchone()
    attempted = result[0] or 0
    passed = result[1] if result[1] is not None else 0
    accuracy = round(100 * passed / attempted) if attempted > 0 else 0
    return attempted, passed, accuracy

def get_falowen_usage(student_code):
    today_str = str(date.today())
    key = f"{student_code}_falowen_{today_str}"
    if "falowen_usage" not in st.session_state:
        st.session_state["falowen_usage"] = {}
    st.session_state["falowen_usage"].setdefault(key, 0)
    return st.session_state["falowen_usage"][key]

def inc_falowen_usage(student_code):
    today_str = str(date.today())
    key = f"{student_code}_falowen_{today_str}"
    if "falowen_usage" not in st.session_state:
        st.session_state["falowen_usage"] = {}
    st.session_state["falowen_usage"].setdefault(key, 0)
    st.session_state["falowen_usage"][key] += 1

def has_falowen_quota(student_code):
    return get_falowen_usage(student_code) < FALOWEN_DAILY_LIMIT



# --- Streamlit page config ---
st.set_page_config(
    page_title="Falowen – Your German Conversation Partner",
    layout="centered",
    initial_sidebar_state="expanded"
)

# ---- Falowen Header ----

st.markdown(
    """
    <div style='display:flex;align-items:center;gap:18px;margin-bottom:22px;'>
        <img src='https://cdn-icons-png.flaticon.com/512/323/323329.png' width='50' style='border-radius:50%;border:2.5px solid #d2b431;box-shadow:0 2px 8px #e4c08d;'/>
        <div>
            <span style='font-size:2.0rem;font-weight:bold;color:#17617a;letter-spacing:2px;'>Falowen App</span>
            <span style='font-size:1.6rem;margin-left:12px;'>🇩🇪</span>
            <br>
            <span style='font-size:1.02rem;color:#ff9900;font-weight:600;'>Learn Language Education Academy</span><br>
            <span style='font-size:1.01rem;color:#268049;font-weight:400;'>
                Your All-in-One German Learning Platform for Speaking, Writing, Exams, and Vocabulary
            </span>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)


# ====================================
# 2. STUDENT DATA LOADING
# ====================================

STUDENTS_CSV = "students.csv"
CODES_FILE = "student_codes.csv"

@st.cache_data
def load_student_data():
    """Load student data from STUDENTS_CSV.
    If missing or empty, return empty DataFrame so app still runs."""
    path = globals().get("STUDENTS_CSV", "students.csv")
    if not os.path.exists(path):
        st.warning("Students file not found. Using empty data.")
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        st.warning("Students file is empty. Using empty data.")
        return pd.DataFrame()

    df.columns = [c.strip() for c in df.columns]
    for col in ["StudentCode", "Email"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.lower()
    return df


# ====================================
# 3. STUDENT LOGIN LOGIC (single, clean block!)
# ====================================

# Use a secret from env or .streamlit/secrets.toml (RECOMMENDED, DO NOT HARD-CODE)
COOKIE_SECRET = os.getenv("COOKIE_SECRET") or st.secrets.get("COOKIE_SECRET")
if not COOKIE_SECRET:
    raise ValueError("COOKIE_SECRET environment variable not set")

cookie_manager = EncryptedCookieManager(
    prefix="falowen_",
    password=COOKIE_SECRET
)
cookie_manager.ready()

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "student_row" not in st.session_state:
    st.session_state["student_row"] = None
if "student_code" not in st.session_state:
    st.session_state["student_code"] = ""
if "student_name" not in st.session_state:
    st.session_state["student_name"] = ""

# --- 1. Check for cookie before showing login ---
code_from_cookie = cookie_manager.get("student_code")
if not st.session_state.get("logged_in", False) and code_from_cookie:
    st.session_state["student_code"] = code_from_cookie
    st.session_state["logged_in"] = True
    # Optional: Fill in other fields
    df_students = load_student_data()
    found = df_students[
        (df_students["StudentCode"].astype(str).str.lower().str.strip() == code_from_cookie)
    ]
    if not found.empty:
        st.session_state["student_row"] = found.iloc[0].to_dict()
        st.session_state["student_name"] = found.iloc[0]["Name"]
# --- 2. Show login if not logged in ---
if not st.session_state["logged_in"]:
    st.title("🔑 Student Login")
    login_input = st.text_input(
        "Enter your Student Code or Email to begin:",
        value=code_from_cookie if code_from_cookie else ""
    ).strip().lower()
    if st.button("Login"):
        df_students = load_student_data()
        found = df_students[
            (df_students["StudentCode"].astype(str).str.lower().str.strip() == login_input) |
            (df_students["Email"].astype(str).str.lower().str.strip() == login_input)
        ]
        if not found.empty:
            st.session_state["logged_in"] = True
            st.session_state["student_row"] = found.iloc[0].to_dict()
            st.session_state["student_code"] = found.iloc[0]["StudentCode"].lower()
            st.session_state["student_name"] = found.iloc[0]["Name"]
            # ← Replace .set() with dict assignment and save()
            cookie_manager["student_code"] = st.session_state["student_code"]
            cookie_manager.save()
            st.success(f"Welcome, {st.session_state['student_name']}! Login successful.")
            st.rerun()
        else:
            st.error("Login failed. Please check your Student Code or Email and try again.")
    st.stop()

# --- 1. Always check if cookie manager is ready ---
if not cookie_manager.ready():
    st.warning("Cookies are not ready. Please refresh this page.")
    st.stop()

# --- 2. Try to load student code from cookie safely ---
code_from_cookie = cookie_manager.get("student_code") or ""

# --- 3. Check if user is logged in (via session) ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "student_row" not in st.session_state:
    st.session_state["student_row"] = None
if "student_code" not in st.session_state:
    st.session_state["student_code"] = ""
if "student_name" not in st.session_state:
    st.session_state["student_name"] = ""

# --- 4. Try auto-login if cookie exists ---
if not st.session_state["logged_in"] and code_from_cookie:
    df_students = load_student_data()
    if not df_students.empty and "StudentCode" in df_students.columns:
        found = df_students[df_students["StudentCode"].str.lower().str.strip() == code_from_cookie]
        if not found.empty:
            st.session_state["student_code"] = code_from_cookie
            st.session_state["logged_in"] = True
            st.session_state["student_row"] = found.iloc[0].to_dict()
            st.session_state["student_name"] = found.iloc[0]["Name"]

# --- 5. If not logged in, show login UI ---
if not st.session_state["logged_in"]:
    st.title("🔑 Student Login")
    login_input = st.text_input(
        "Enter your Student Code or Email to begin:",
        value=code_from_cookie
    ).strip().lower()
    if st.button("Login"):
        df_students = load_student_data()
        if not df_students.empty:
            found = df_students[
                (df_students["StudentCode"].str.lower().str.strip() == login_input) |
                (df_students["Email"].str.lower().str.strip() == login_input)
            ]
            if not found.empty:
                st.session_state["logged_in"] = True
                st.session_state["student_row"] = found.iloc[0].to_dict()
                st.session_state["student_code"] = found.iloc[0]["StudentCode"].lower()
                st.session_state["student_name"] = found.iloc[0]["Name"]
                cookie_manager["student_code"] = st.session_state["student_code"]
                cookie_manager.save()
                st.success(f"Welcome, {st.session_state['student_name']}! Login successful.")
                st.rerun()
            else:
                st.error("Login failed. Please check your Student Code or Email and try again.")
        else:
            st.error("Student list is not available.")
    st.stop()

# ====================================
# 4. FLEXIBLE ANSWER CHECKERS
# ====================================

def is_close_answer(student, correct):
    student = student.strip().lower()
    correct = correct.strip().lower()
    if correct.startswith("to "):
        correct = correct[3:]
    if len(student) < 3 or len(student) < 0.6 * len(correct):
        return False
    similarity = difflib.SequenceMatcher(None, student, correct).ratio()
    return similarity > 0.80

def is_almost(student, correct):
    student = student.strip().lower()
    correct = correct.strip().lower()
    if correct.startswith("to "):
        correct = correct[3:]
    similarity = difflib.SequenceMatcher(None, student, correct).ratio()
    return 0.60 < similarity <= 0.80

def validate_translation_openai(word, student_answer):
    """Use OpenAI to verify if the student's answer is a valid translation."""
    prompt = (
        f"Is '{student_answer.strip()}' an accurate English translation of the German word '{word}'? "
        "Reply with 'True' or 'False' only."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1,
            temperature=0,
        )
        reply = resp.choices[0].message.content.strip().lower()
        return reply.startswith("true")
    except Exception:
        return False


# ====================================
# 5. CONSTANTS & VOCAB LISTS
# ====================================

FALOWEN_DAILY_LIMIT = 20
VOCAB_DAILY_LIMIT = 20
SCHREIBEN_DAILY_LIMIT = 5
max_turns = 25


# --- Vocab lists for all levels ---

a1_vocab = [
    ("Südseite", "south side"), ("3. Stock", "third floor"), ("Geschenk", "present/gift"),
    ("Buslinie", "bus line"), ("Ruhetag", "rest day (closed)"), ("Heizung", "heating"),
    ("Hälfte", "half"), ("die Wohnung", "apartment"), ("das Zimmer", "room"), ("die Miete", "rent"),
    ("der Balkon", "balcony"), ("der Garten", "garden"), ("das Schlafzimmer", "bedroom"),
    ("das Wohnzimmer", "living room"), ("das Badezimmer", "bathroom"), ("die Garage", "garage"),
    ("der Tisch", "table"), ("der Stuhl", "chair"), ("der Schrank", "cupboard"), ("die Tür", "door"),
    ("das Fenster", "window"), ("der Boden", "floor"), ("die Wand", "wall"), ("die Lampe", "lamp"),
    ("der Fernseher", "television"), ("das Bett", "bed"), ("die Küche", "kitchen"), ("die Toilette", "toilet"),
    ("die Dusche", "shower"), ("das Waschbecken", "sink"), ("der Ofen", "oven"),
    ("der Kühlschrank", "refrigerator"), ("die Mikrowelle", "microwave"), ("die Waschmaschine", "washing machine"),
    ("die Spülmaschine", "dishwasher"), ("das Haus", "house"), ("die Stadt", "city"), ("das Land", "country"),
    ("die Straße", "street"), ("der Weg", "way"), ("der Park", "park"), ("die Ecke", "corner"),
    ("die Bank", "bank"), ("der Supermarkt", "supermarket"), ("die Apotheke", "pharmacy"),
    ("die Schule", "school"), ("die Universität", "university"), ("das Geschäft", "store"),
    ("der Markt", "market"), ("der Flughafen", "airport"), ("der Bahnhof", "train station"),
    ("die Haltestelle", "bus stop"), ("die Fahrt", "ride"), ("das Ticket", "ticket"), ("der Zug", "train"),
    ("der Bus", "bus"), ("das Taxi", "taxi"), ("das Auto", "car"), ("die Ampel", "traffic light"),
    ("die Kreuzung", "intersection"), ("der Parkplatz", "parking lot"), ("der Fahrplan", "schedule"),
    ("zumachen", "to close"), ("aufmachen", "to open"), ("ausmachen", "to turn off"),
    ("übernachten", "to stay overnight"), ("anfangen", "to begin"), ("vereinbaren", "to arrange"),
    ("einsteigen", "to get in / board"), ("umsteigen", "to change (trains)"), ("aussteigen", "to get out / exit"),
    ("anschalten", "to switch on"), ("ausschalten", "to switch off"), ("Anreisen", "to arrive"), ("Ankommen", "to arrive"),
    ("Abreisen", "to depart"), ("Absagen", "to cancel"), ("Zusagen", "to agree"), ("günstig", "cheap"),
    ("billig", "inexpensive")
]

a2_vocab = [
    ("die Verantwortung", "responsibility"), ("die Besprechung", "meeting"), ("die Überstunden", "overtime"),
    ("laufen", "to run"), ("das Fitnessstudio", "gym"), ("die Entspannung", "relaxation"),
    ("der Müll", "waste, garbage"), ("trennen", "to separate"), ("der Umweltschutz", "environmental protection"),
    ("der Abfall", "waste, rubbish"), ("der Restmüll", "residual waste"), ("die Anweisung", "instruction"),
    ("die Gemeinschaft", "community"), ("der Anzug", "suit"), ("die Beförderung", "promotion"),
    ("die Abteilung", "department"), ("drinnen", "indoors"), ("die Vorsorgeuntersuchung", "preventive examination"),
    ("die Mahlzeit", "meal"), ("behandeln", "to treat"), ("Hausmittel", "home remedies"),
    ("Salbe", "ointment"), ("Tropfen", "drops"), ("nachhaltig", "sustainable"),
    ("berühmt / bekannt", "famous / well-known"), ("einleben", "to settle in"), ("sich stören", "to be bothered"),
    ("liefern", "to deliver"), ("zum Mitnehmen", "to take away"), ("erreichbar", "reachable"),
    ("bedecken", "to cover"), ("schwanger", "pregnant"), ("die Impfung", "vaccination"),
    ("am Fluss", "by the river"), ("das Guthaben", "balance / credit"), ("kostenlos", "free of charge"),
    ("kündigen", "to cancel / to terminate"), ("der Anbieter", "provider"), ("die Bescheinigung", "certificate / confirmation"),
    ("retten", "rescue"), ("die Falle", "trap"), ("die Feuerwehr", "fire department"),
    ("der Schreck", "shock, fright"), ("schwach", "weak"), ("verletzt", "injured"),
    ("der Wildpark", "wildlife park"), ("die Akrobatik", "acrobatics"), ("bauen", "to build"),
    ("extra", "especially"), ("der Feriengruß", "holiday greeting"), ("die Pyramide", "pyramid"),
    ("regnen", "to rain"), ("schicken", "to send"), ("das Souvenir", "souvenir"),
    ("wahrscheinlich", "probably"), ("das Chaos", "chaos"), ("deutlich", "clearly"),
    ("der Ohrring", "earring"), ("verlieren", "to lose"), ("der Ärger", "trouble"),
    ("besorgt", "worried"), ("deprimiert", "depressed"), ("der Streit", "argument"),
    ("sich streiten", "to argue"), ("dagegen sein", "to be against"), ("egal", "doesn't matter"),
    ("egoistisch", "selfish"), ("kennenlernen", "to get to know"), ("nicht leiden können", "to dislike"),
    ("der Mädchentag", "girls' day"), ("der Ratschlag", "advice"), ("tun", "to do"),
    ("zufällig", "by chance"), ("ansprechen", "to approach"), ("plötzlich", "suddenly"),
    ("untrennbar", "inseparable"), ("sich verabreden", "to make an appointment"),
    ("versprechen", "to promise"), ("weglaufen", "to run away"), ("ab (+ Dativ)", "from, starting from"),
    ("das Aquarium", "aquarium"), ("der Flohmarkt", "flea market"), ("der Jungentag", "boys' day"),
    ("kaputt", "broken"), ("kostenlos", "free"), ("präsentieren", "to present"),
    ("das Quiz", "quiz"), ("schwitzen", "to sweat"), ("das Straßenfest", "street festival"),
    ("täglich", "daily"), ("vorschlagen", "to suggest"), ("wenn", "if, when"),
    ("die Bühne", "stage"), ("dringend", "urgently"), ("die Reaktion", "reaction"),
    ("unterwegs", "on the way"), ("vorbei", "over, past"), ("die Bauchschmerzen", "stomach ache"),
    ("der Busfahrer", "bus driver"), ("die Busfahrerin", "female bus driver"),
    ("der Fahrplan", "schedule"), ("der Platten", "flat tire"), ("die Straßenbahn", "tram"),
    ("streiken", "to strike"), ("der Unfall", "accident"), ("die Ausrede", "excuse"),
    ("baden", "to bathe"), ("die Grillwurst", "grilled sausage"), ("klingeln", "to ring"),
    ("die Mitternacht", "midnight"), ("der Nachbarhund", "neighbor's dog"),
    ("verbieten", "to forbid"), ("wach", "awake"), ("der Wecker", "alarm clock"),
    ("die Wirklichkeit", "reality"), ("zuletzt", "lastly, finally"), ("das Bandmitglied", "band member"),
    ("loslassen", "to let go"), ("der Strumpf", "stocking"), ("anprobieren", "to try on"),
    ("aufdecken", "to uncover / flip over"), ("behalten", "to keep"), ("der Wettbewerb", "competition"),
    ("schmutzig", "dirty"), ("die Absperrung", "barricade"), ("böse", "angry, evil"),
    ("trocken", "dry"), ("aufbleiben", "to stay up"), ("hässlich", "ugly"),
    ("ausweisen", "to identify"), ("erfahren", "to learn, find out"), ("entdecken", "to discover"),
    ("verbessern", "to improve"), ("aufstellen", "to set up"), ("die Notaufnahme", "emergency department"),
    ("das Arzneimittel", "medication"), ("die Diagnose", "diagnosis"), ("die Therapie", "therapy"),
    ("die Rehabilitation", "rehabilitation"), ("der Chirurg", "surgeon"), ("die Anästhesie", "anesthesia"),
    ("die Infektion", "infection"), ("die Entzündung", "inflammation"), ("die Unterkunft", "accommodation"),
    ("die Sehenswürdigkeit", "tourist attraction"), ("die Ermäßigung", "discount"), ("die Verspätung", "delay"),
    ("die Quittung", "receipt"), ("die Veranstaltung", "event"), ("die Bewerbung", "application")
]

# --- Short starter lists for B1/B2/C1 (add more later as you wish) ---
b1_vocab = [
    "Fortschritt", "Eindruck", "Unterschied", "Vorschlag", "Erfahrung", "Ansicht", "Abschluss", "Entscheidung"
]

b2_vocab = [
    "Umwelt", "Entwicklung", "Auswirkung", "Verhalten", "Verhältnis", "Struktur", "Einfluss", "Kritik"
]

c1_vocab = [
    "Ausdruck", "Beziehung", "Erkenntnis", "Verfügbarkeit", "Bereich", "Perspektive", "Relevanz", "Effizienz"
]

# --- Vocab list dictionary for your app ---
VOCAB_LISTS = {
    "A1": a1_vocab,
    "A2": a2_vocab,
    "B1": b1_vocab,
    "B2": b2_vocab,
    "C1": c1_vocab
}

# Exam topic lists
# --- A1 Exam Topic Lists (Teil 1, 2, 3) ---

A1_TEIL1 = [
    "Name", "Alter", "Wohnort", "Land", "Sprache", "Familie", "Beruf", "Hobby"
]

A1_TEIL2 = [
    ("Geschäft", "schließen"),
    ("Uhr", "Uhrzeit"),
    ("Arbeit", "Kollege"),
    ("Hausaufgabe", "machen"),
    ("Küche", "kochen"),
    ("Freizeit", "lesen"),
    ("Telefon", "anrufen"),
    ("Reise", "Hotel"),
    ("Auto", "fahren"),
    ("Einkaufen", "Obst"),
    ("Schule", "Lehrer"),
    ("Geburtstag", "Geschenk"),
    ("Essen", "Frühstück"),
    ("Arzt", "Termin"),
    ("Zug", "Abfahrt"),
    ("Wetter", "Regen"),
    ("Buch", "lesen"),
    ("Computer", "E-Mail"),
    ("Kind", "spielen"),
    ("Wochenende", "Plan"),
    ("Bank", "Geld"),
    ("Sport", "laufen"),
    ("Abend", "Fernsehen"),
    ("Freunde", "Besuch"),
    ("Bahn", "Fahrkarte"),
    ("Straße", "Stau"),
    ("Essen gehen", "Restaurant"),
    ("Hund", "Futter"),
    ("Familie", "Kinder"),
    ("Post", "Brief"),
    ("Nachbarn", "laut"),
    ("Kleid", "kaufen"),
    ("Büro", "Chef"),
    ("Urlaub", "Strand"),
    ("Kino", "Film"),
    ("Internet", "Seite"),
    ("Bus", "Abfahrt"),
    ("Arztpraxis", "Wartezeit"),
    ("Kuchen", "backen"),
    ("Park", "spazieren"),
    ("Bäckerei", "Brötchen"),
    ("Geldautomat", "Karte"),
    ("Buchladen", "Roman"),
    ("Fernseher", "Programm"),
    ("Tasche", "vergessen"),
    ("Stadtplan", "finden"),
    ("Ticket", "bezahlen"),
    ("Zahnarzt", "Schmerzen"),
    ("Museum", "Öffnungszeiten"),
    ("Handy", "Akku leer"),
]

A1_TEIL3 = [
    "Radio anmachen",
    "Fenster zumachen",
    "Licht anschalten",
    "Tür aufmachen",
    "Tisch sauber machen",
    "Hausaufgaben schicken",
    "Buch bringen",
    "Handy ausmachen",
    "Stuhl nehmen",
    "Wasser holen",
    "Fenster öffnen",
    "Musik leiser machen",
    "Tafel sauber wischen",
    "Kaffee kochen",
    "Deutsch üben",
    "Auto waschen",
    "Kind abholen",
    "Tisch decken",
    "Termin machen",
    "Nachricht schreiben",
]

A2_TEIL1 = [
    "Wohnort", "Tagesablauf", "Freizeit", "Sprachen", "Essen & Trinken", "Haustiere",
    "Lieblingsmonat", "Jahreszeit", "Sport", "Kleidung (Sommer)", "Familie", "Beruf",
    "Hobbys", "Feiertage", "Reisen", "Lieblingsessen", "Schule", "Wetter", "Auto oder Fahrrad", "Perfekter Tag"
]
A2_TEIL2 = [
    "Was machen Sie mit Ihrem Geld?",
    "Was machen Sie am Wochenende?",
    "Wie verbringen Sie Ihren Urlaub?",
    "Wie oft gehen Sie einkaufen und was kaufen Sie?",
    "Was für Musik hören Sie gern?",
    "Wie feiern Sie Ihren Geburtstag?",
    "Welche Verkehrsmittel nutzen Sie?",
    "Wie bleiben Sie gesund?",
    "Was machen Sie gern mit Ihrer Familie?",
    "Wie sieht Ihr Traumhaus aus?",
    "Welche Filme oder Serien mögen Sie?",
    "Wie oft gehen Sie ins Restaurant?",
    "Was ist Ihr Lieblingsfeiertag?",
    "Was machen Sie morgens als Erstes?",
    "Wie lange schlafen Sie normalerweise?",
    "Welche Hobbys hatten Sie als Kind?",
    "Machen Sie lieber Urlaub am Meer oder in den Bergen?",
    "Wie sieht Ihr Lieblingszimmer aus?",
    "Was ist Ihr Lieblingsgeschäft?",
    "Wie sieht ein perfekter Tag für Sie aus?"
]
A2_TEIL3 = [
    "Zusammen ins Kino gehen", "Ein Café besuchen", "Gemeinsam einkaufen gehen",
    "Ein Picknick im Park organisieren", "Eine Fahrradtour planen",
    "Zusammen in die Stadt gehen", "Einen Ausflug ins Schwimmbad machen",
    "Eine Party organisieren", "Zusammen Abendessen gehen",
    "Gemeinsam einen Freund/eine Freundin besuchen", "Zusammen ins Museum gehen",
    "Einen Spaziergang im Park machen", "Ein Konzert besuchen",
    "Zusammen eine Ausstellung besuchen", "Einen Wochenendausflug planen",
    "Ein Theaterstück ansehen", "Ein neues Restaurant ausprobieren",
    "Einen Kochabend organisieren", "Einen Sportevent besuchen", "Eine Wanderung machen"
]

B1_TEIL1 = [
    "Mithilfe beim Sommerfest", "Eine Reise nach Köln planen",
    "Überraschungsparty organisieren", "Kulturelles Ereignis (Konzert, Ausstellung) planen",
    "Museumsbesuch organisieren"
]
B1_TEIL2 = [
    "Ausbildung", "Auslandsaufenthalt", "Behinderten-Sport", "Berufstätige Eltern",
    "Berufswahl", "Bio-Essen", "Chatten", "Computer für jeden Kursraum", "Das Internet",
    "Einkaufen in Einkaufszentren", "Einkaufen im Internet", "Extremsport", "Facebook",
    "Fertigessen", "Freiwillige Arbeit", "Freundschaft", "Gebrauchte Kleidung",
    "Getrennter Unterricht für Jungen und Mädchen", "Haushalt", "Haustiere", "Heiraten",
    "Hotel Mama", "Ich bin reich genug", "Informationen im Internet", "Kinder und Fernsehen",
    "Kinder und Handys", "Kinos sterben", "Kreditkarten", "Leben auf dem Land oder in der Stadt",
    "Makeup für Kinder", "Marken-Kleidung", "Mode", "Musikinstrument lernen",
    "Musik im Zeitalter des Internets", "Rauchen", "Reisen", "Schokolade macht glücklich",
    "Sport treiben", "Sprachenlernen", "Sprachenlernen mit dem Internet",
    "Stadtzentrum ohne Autos", "Studenten und Arbeit in den Ferien", "Studium", "Tattoos",
    "Teilzeitarbeit", "Unsere Idole", "Umweltschutz", "Vegetarische Ernährung", "Zeitungslesen"
]
B1_TEIL3 = [
    "Fragen stellen zu einer Präsentation", "Positives Feedback geben",
    "Etwas überraschend finden oder planen", "Weitere Details erfragen"
]
b2_teil1_topics = [
    "Sollten Smartphones in der Schule erlaubt sein?",
    "Wie wichtig ist Umweltschutz in unserem Alltag?",
    "Wie beeinflusst Social Media unser Leben?",
    "Welche Rolle spielt Sport für die Gesundheit?",
]

b2_teil2_presentations = [
    "Die Bedeutung von Ehrenamt",
    "Vorteile und Nachteile von Homeoffice",
    "Auswirkungen der Digitalisierung auf die Arbeitswelt",
    "Mein schönstes Reiseerlebnis",
]

b2_teil3_arguments = [
    "Sollte man in der Stadt oder auf dem Land leben?",
    "Sind E-Autos die Zukunft?",
    "Brauchen wir mehr Urlaubstage?",
    "Muss Schule mehr praktische Fächer anbieten?",
]

c1_teil1_lectures = [
    "Die Zukunft der künstlichen Intelligenz",
    "Internationale Migration: Herausforderungen und Chancen",
    "Wandel der Arbeitswelt im 21. Jahrhundert",
    "Digitalisierung und Datenschutz",
]

c1_teil2_discussions = [
    "Sollten Universitäten Studiengebühren verlangen?",
    "Welchen Einfluss haben soziale Medien auf die Demokratie?",
    "Ist lebenslanges Lernen notwendig?",
    "Die Bedeutung von Nachhaltigkeit in der Wirtschaft",
]

c1_teil3_evaluations = [
    "Die wichtigsten Kompetenzen für die Zukunft",
    "Vor- und Nachteile globaler Zusammenarbeit",
    "Welchen Einfluss hat Technik auf unser Leben?",
    "Wie verändert sich die Familie?",
]


# ====================================
# 6. MAIN TAB SELECTOR (with Dashboard)
# ====================================

if st.session_state["logged_in"]:
    student_code = st.session_state.get("student_code", "")

    st.header("Choose Practice Mode")
    tab = st.radio(
        "How do you want to practice?",
        ["Dashboard", "Exams Mode & Custom Chat", "Vocab Trainer", "Schreiben Trainer","My Results and Resources", "Admin"],
        key="main_tab_select"
    )

    # --- Mobile-friendly Active Tab Indicator ---
    st.markdown(
        f"""
        <div style='
            display: flex; 
            justify-content: center; 
            align-items: center;
            margin-bottom: 10px;
        '>
            <span style='
                background: #3498db;
                color: #fff;
                padding: 6px 18px;
                border-radius: 22px;
                font-size: 1.1rem;
                font-weight: 600;
                letter-spacing: 1px;
                box-shadow: 0 1px 4px #bbc;
                white-space: nowrap;
            '>
                {tab}
            </span>
        </div>
        """,
        unsafe_allow_html=True
    )


    # --- DASHBOARD TAB, MOBILE-FRIENDLY ---
    if tab == "Dashboard":
        st.header("📊 Student Dashboard")

        student_row = st.session_state.get("student_row") or {}
        streak = get_vocab_streak(student_code)
        total_attempted, total_passed, accuracy = get_writing_stats(student_code)

        # --- Compute today's writing usage for Dashboard ---
        from datetime import date
        today_str = str(date.today())
        limit_key = f"{student_code}_schreiben_{today_str}"
        if "schreiben_usage" not in st.session_state:
            st.session_state["schreiben_usage"] = {}
        st.session_state["schreiben_usage"].setdefault(limit_key, 0)
        daily_so_far = st.session_state["schreiben_usage"][limit_key]

        # Student name and essentials
        st.markdown(f"### 👤 {student_row.get('Name', '')}")
        st.markdown(
            f"**Level:** {student_row.get('Level', '')}\n\n"
            f"**Code:** `{student_row.get('StudentCode', '')}`\n\n"
            f"**Email:** {student_row.get('Email', '')}\n\n"
            f"**Phone:** {student_row.get('Phone', '')}\n\n"
            f"**Location:** {student_row.get('Location', '')}\n\n"
            f"**Contract:** {student_row.get('ContractStart', '')} ➔ {student_row.get('ContractEnd', '')}\n\n"
            f"**Enroll Date:** {student_row.get('EnrollDate', '')}\n\n"
            f"**Status:** {student_row.get('Status', '')}"
        )

        # --- Payment info, clear message ---
        balance = student_row.get('Balance', '0.0')
        try:
            balance_float = float(balance)
        except Exception:
            balance_float = 0.0
        if balance_float > 0:
            st.warning(
                f"💸 Balance to pay: **₵{balance_float:.2f}** (update when paid)"
            )

        # --- Contract End reminder ---
        from datetime import datetime
        contract_end = student_row.get('ContractEnd')
        if contract_end:
            try:
                contract_end_date = datetime.strptime(str(contract_end), "%Y-%m-%d")
                days_left = (contract_end_date - datetime.now()).days
                if 0 < days_left <= 30:
                    st.info(f"⚠️ Contract ends in {days_left} days. Please renew soon.")
                elif days_left < 0:
                    st.error("⏰ Contract expired. Contact the office to renew.")
            except Exception:
                pass

        # --- Vocab streak ---
        st.markdown(f"🔥 **Vocab Streak:** {streak} days")

        # --- Writing goal tracker ---
        goal_remain = max(0, 2 - (total_attempted or 0))
        if goal_remain > 0:
            st.success(f"🎯 Your next goal: Write {goal_remain} more letter(s) this week!")
        else:
            st.success("🎉 Weekly goal reached! Keep practicing!")

        # --- Writing stats, big and clear ---
        st.markdown(
            f"**📝 Letters submitted:** {total_attempted}\n\n"
            f"**✅ Passed (score ≥17):** {total_passed}\n\n"
            f"**🏅 Pass rate:** {accuracy}%\n\n"
            f"**Today:** {daily_so_far} / {SCHREIBEN_DAILY_LIMIT} used"
        )

# ================================
# 5a. EXAMS MODE & CUSTOM CHAT TAB (block start, pdf helper, prompt builders)
# ================================

if tab == "Exams Mode & Custom Chat":
    # --- Daily Limit Check ---
    # You can use a helper like: has_falowen_quota(student_code) or get_falowen_remaining(student_code)
    if not has_falowen_quota(student_code):
        st.header("🗣️ Falowen – Speaking & Exam Trainer")
        st.warning("You have reached your daily practice limit for this section. Please come back tomorrow.")
        st.stop()


    # ---- PDF Helper ----
    def falowen_download_pdf(messages, filename):
        from fpdf import FPDF
        import os
        def safe_latin1(text):
            return text.encode("latin1", "replace").decode("latin1")
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        chat_text = ""
        for m in messages:
            role = "Herr Felix" if m["role"] == "assistant" else "Student"
            safe_msg = safe_latin1(m["content"])
            chat_text += f"{role}: {safe_msg}\n\n"
        pdf.multi_cell(0, 10, chat_text)
        pdf_output = f"{filename}.pdf"
        pdf.output(pdf_output)
        with open(pdf_output, "rb") as f:
            pdf_bytes = f.read()
        os.remove(pdf_output)
        return pdf_bytes

    # ---- PROMPT BUILDERS (ALL LOGIC) ----
    def build_a1_exam_intro():
        return (
            "**A1 – Teil 1: Basic Introduction**\n\n"
            "In the A1 exam's first part, you will be asked to introduce yourself. "
            "Typical information includes: your **Name, Land, Wohnort, Sprachen, Beruf, Hobby**.\n\n"
            "After your introduction, you will be asked 3 basic questions such as:\n"
            "- Haben Sie Geschwister?\n"
            "- Wie alt ist deine Mutter?\n"
            "- Bist du verheiratet?\n\n"
            "You might also be asked to spell your name (**Buchstabieren**). "
            "Please introduce yourself now using all the keywords above."
        )

    def build_exam_instruction(level, teil):
        if level == "A1":
            if "Teil 1" in teil:
                return build_a1_exam_intro()
            elif "Teil 2" in teil:
                return (
                    "**A1 – Teil 2: Question and Answer**\n\n"
                    "You will get a topic and a keyword. Your job: ask a question using the keyword, "
                    "then answer it yourself. Example: Thema: Geschäft – Keyword: schließen → "
                    "Wann schließt das Geschäft?\nLet's try one. Ready?"
                )
            elif "Teil 3" in teil:
                return (
                    "**A1 – Teil 3: Making a Request**\n\n"
                    "You'll receive a prompt (e.g. 'Radio anmachen'). Write a polite request or imperative. "
                    "Example: Können Sie bitte das Radio anmachen?\nReady?"
                )
        if level == "A2":
            if "Teil 1" in teil:
                return (
                    "**A2 – Teil 1: Fragen zu Schlüsselwörtern**\n\n"
                    "You'll get a topic (e.g. 'Wohnort'). Ask a question, then answer it yourself. "
                    "When you're ready, type 'Begin'."
                )
            elif "Teil 2" in teil:
                return (
                    "**A2 – Teil 2: Über das Thema sprechen**\n\n"
                    "Talk about the topic in 3–4 sentences. I'll correct and give tips. Start when ready."
                )
            elif "Teil 3" in teil:
                return (
                    "**A2 – Teil 3: Gemeinsam planen**\n\n"
                    "Let's plan something together. Respond and make suggestions. Start when ready."
                )
        if level == "B1":
            if "Teil 1" in teil:
                return (
                    "**B1 – Teil 1: Gemeinsam planen**\n\n"
                    "We'll plan an activity together (e.g., a trip or party). Give your ideas and answer questions."
                )
            elif "Teil 2" in teil:
                return (
                    "**B1 – Teil 2: Präsentation**\n\n"
                    "Give a short presentation on the topic (about 2 minutes). I'll ask follow-up questions."
                )
            elif "Teil 3" in teil:
                return (
                    "**B1 – Teil 3: Feedback & Fragen stellen**\n\n"
                    "Answer questions about your presentation. I'll give you feedback on your language and structure."
                )
        if level == "B2":
            if "Teil 1" in teil:
                return (
                    "**B2 – Teil 1: Diskussion**\n\n"
                    "We'll discuss a topic. Express your opinion and justify it."
                )
            elif "Teil 2" in teil:
                return (
                    "**B2 – Teil 2: Präsentation**\n\n"
                    "Present a topic in detail. I'll challenge your points and help you improve."
                )
            elif "Teil 3" in teil:
                return (
                    "**B2 – Teil 3: Argumentation**\n\n"
                    "Argue your perspective. I'll give feedback and counterpoints."
                )
        if level == "C1":
            if "Teil 1" in teil:
                return (
                    "**C1 – Teil 1: Vortrag**\n\n"
                    "Bitte halte einen kurzen Vortrag zum Thema. Ich werde anschließend Fragen stellen und deine Sprache bewerten."
                )
            elif "Teil 2" in teil:
                return (
                    "**C1 – Teil 2: Diskussion**\n\n"
                    "Diskutiere mit mir über das gewählte Thema. Ich werde kritische Nachfragen stellen."
                )
            elif "Teil 3" in teil:
                return (
                    "**C1 – Teil 3: Bewertung**\n\n"
                    "Bewerte deine eigene Präsentation. Was würdest du beim nächsten Mal besser machen?"
                )
        return ""

    def build_exam_system_prompt(level, teil):
        if level == "A1":
            if "Teil 1" in teil:
                return (
                    "You are Herr Felix, a supportive A1 German examiner. "
                    "Ask the student to introduce themselves using the keywords (Name, Land, Wohnort, Sprachen, Beruf, Hobby). "
                    "Check if all info is given, correct any errors (explain in English), and give the right way to say things in German. "
                    "After their intro, ask these three questions one by one: "
                    "'Haben Sie Geschwister?', 'Wie alt ist deine Mutter?', 'Bist du verheiratet?'. "
                    "Correct their answers (explain in English). At the end, mention they may be asked to spell their name ('Buchstabieren') and wish them luck."
                )
            elif "Teil 2" in teil:
                return (
                    "You are Herr Felix, an A1 examiner. Randomly give the student a Thema and Keyword from the official list. "
                    "Tell them to ask a question with the keyword and answer it themselves, then correct their German (explain errors in English, show the correct version), and move to the next topic."
                )
            elif "Teil 3" in teil:
                return (
                    "You are Herr Felix, an A1 examiner. Give the student a prompt (e.g. 'Radio anmachen'). "
                    "Ask them to write a polite request or imperative. Check if it's correct and polite, explain errors in English, and provide the right German version. Then give the next prompt."
                )
        if level == "A2":
            if "Teil 1" in teil:
                return (
                    "You are Herr Felix, a Goethe A2 examiner. Give a topic from the A2 list. "
                    "Ask the student to ask and answer a question on it. Always correct their German (explain errors in English), show the correct version, and encourage."
                )
            elif "Teil 2" in teil:
                return (
                    "You are Herr Felix, an A2 examiner. Give a topic. Student gives a short monologue. Correct errors (in English), give suggestions, and follow up with one question."
                )
            elif "Teil 3" in teil:
                return (
                    "You are Herr Felix, an A2 examiner. Plan something together (e.g., going to the cinema). Check student's suggestions, correct errors, and keep the conversation going."
                )
        if level == "B1":
            if "Teil 1" in teil:
                return (
                    "You are Herr Felix, a Goethe B1 examiner. You and the student plan an activity together. "
                    "Always give feedback in both German and English, correct mistakes, suggest improvements, and keep it realistic."
                )
            elif "Teil 2" in teil:
                return (
                    "You are Herr Felix, a Goethe B1 examiner. Student gives a presentation. Give constructive feedback in German and English, ask for more details, and highlight strengths and weaknesses."
                )
            elif "Teil 3" in teil:
                return (
                    "You are Herr Felix, a Goethe B1 examiner. Student answers questions about their presentation. "
                    "Give exam-style feedback (in German and English), correct language, and motivate."
                )
        if level == "B2":
            if "Teil 1" in teil:
                return (
                    "You are Herr Felix, a B2 examiner. Discuss a topic with the student. Challenge their points. Correct errors (mostly in German, but use English if it's a big mistake), and always provide the correct form."
                )
            elif "Teil 2" in teil:
                return (
                    "You are Herr Felix, a B2 examiner. Listen to the student's presentation. Give high-level feedback (mostly in German), ask probing questions, and always highlight advanced vocabulary and connectors."
                )
            elif "Teil 3" in teil:
                return (
                    "You are Herr Felix, a B2 examiner. Argue your perspective. Give detailed, advanced corrections (mostly German, use English if truly needed). Encourage native-like answers."
                )
        if level == "C1":
            if "Teil 1" in teil or "Teil 2" in teil or "Teil 3" in teil:
                return (
                    "Du bist Herr Felix, ein C1-Prüfer. Sprich nur Deutsch. "
                    "Stelle herausfordernde Fragen, gib ausschließlich auf Deutsch Feedback, und fordere den Studenten zu komplexen Strukturen auf."
                )
        return ""

    def build_custom_chat_prompt(level):
        if level == "C1":
            return (
                "Du bist Herr Felix, ein C1-Prüfer. Sprich nur Deutsch. "
                "Gib konstruktives Feedback, stelle schwierige Fragen, und hilf dem Studenten, auf C1-Niveau zu sprechen."
            )
        if level in ["A1", "A2", "B1", "B2"]:
            correction_lang = "in English" if level in ["A1", "A2"] else "half in English and half in German"
            return (
                f"You are Herr Felix, a supportive and innovative German teacher. "
                f"The student's first input is their chosen topic. Only give suggestions, phrases, tips and ideas at first in English, no corrections. "
                f"Pick 4 useful keywords related to the student's topic and use them as the focus for conversation. Give students ideas and how to build their points for the conversation in English. "
                f"For each keyword, ask the student up to 3 creative, diverse and interesting questions in German only based on student language level, one at a time, not all at once. Just ask the question and don't let student know this is the keyword you are using. "
                f"After each student answer, give feedback and a suggestion to extend their answer if it's too short. Feedback in English and suggestion in German. "
                f"After keyword questions, continue with other random follow-up questions that reflect student selected level about the topic in German (until you reach 20 questions in total). "
                f"Never ask more than 3 questions about the same keyword. "
                f"After the student answers 20 questions, write a summary of their performance: what they did well, mistakes, and what to improve in English. "
                f"All feedback and corrections should be {correction_lang}. "
                f"Encourage the student and keep the chat motivating. "
            )
        return ""

    # ---- USAGE LIMIT CHECK ----
    if not has_falowen_quota(student_code):
        st.warning("You have reached your daily practice limit for this section. Please come back tomorrow.")
        st.stop()

    # ---- SESSION STATE DEFAULTS ----
    default_state = {
        "falowen_stage": 1,
        "falowen_mode": None,
        "falowen_level": None,
        "falowen_teil": None,
        "falowen_messages": [],
        "falowen_turn_count": 0,
        "custom_topic_intro_done": False,
        "custom_chat_level": None,
        "falowen_exam_topic": None,
        "falowen_exam_keyword": None,
    }
    for key, val in default_state.items():
        if key not in st.session_state:
            st.session_state[key] = val

    # ---- STAGE 1: Mode Selection ----
    if st.session_state["falowen_stage"] == 1:
        st.subheader("Step 1: Choose Practice Mode")
        mode = st.radio(
            "How would you like to practice?",
            ["Geführte Prüfungssimulation (Exam Mode)", "Eigenes Thema/Frage (Custom Chat)"],
            key="falowen_mode_center"
        )
        if st.button("Next ➡️", key="falowen_next_mode"):
            st.session_state["falowen_mode"] = mode
            st.session_state["falowen_stage"] = 2
            st.session_state["falowen_level"] = None
            st.session_state["falowen_teil"] = None
            st.session_state["falowen_messages"] = []
            st.session_state["custom_topic_intro_done"] = False
        st.stop()

    # ---- STAGE 2: Level Selection ----
    if st.session_state["falowen_stage"] == 2:
        st.subheader("Step 2: Choose Your Level")
        level = st.radio(
            "Select your level:",
            ["A1", "A2", "B1", "B2", "C1"],
            key="falowen_level_center"
        )
        if st.button("⬅️ Back", key="falowen_back1"):
            st.session_state["falowen_stage"] = 1
            st.stop()
        if st.button("Next ➡️", key="falowen_next_level"):
            st.session_state["falowen_level"] = level
            if st.session_state["falowen_mode"] == "Geführte Prüfungssimulation (Exam Mode)":
                st.session_state["falowen_stage"] = 3
            else:
                st.session_state["falowen_stage"] = 4
            st.session_state["falowen_teil"] = None
            st.session_state["falowen_messages"] = []
            st.session_state["custom_topic_intro_done"] = False
        st.stop()

    # ---- STAGE 3: Exam Part & Topic (Exam Mode Only) ----
    if st.session_state["falowen_stage"] == 3:
        level = st.session_state["falowen_level"]
        teil_options = {
            "A1": [
                "Teil 1 – Basic Introduction", "Teil 2 – Question and Answer", "Teil 3 – Making A Request"
            ],
            "A2": [
                "Teil 1 – Fragen zu Schlüsselwörtern", "Teil 2 – Über das Thema sprechen", "Teil 3 – Gemeinsam planen"
            ],
            "B1": [
                "Teil 1 – Gemeinsam planen (Dialogue)", "Teil 2 – Präsentation (Monologue)", "Teil 3 – Feedback & Fragen stellen"
            ],
            "B2": [
                "Teil 1 – Diskussion", "Teil 2 – Präsentation", "Teil 3 – Argumentation"
            ],
            "C1": [
                "Teil 1 – Vortrag", "Teil 2 – Diskussion", "Teil 3 – Bewertung"
            ]
        }

        # Exam topics for dropdown (optional, can be expanded)
        exam_topics = []
        if level == "A2":
            exam_topics = A2_TEIL1 + A2_TEIL2 + A2_TEIL3
        elif level == "B1":
            exam_topics = B1_TEIL1 + B1_TEIL2 + B1_TEIL3
        elif level == "B2":
            exam_topics = b2_teil1_topics + b2_teil2_presentations + b2_teil3_arguments
        elif level == "C1":
            exam_topics = c1_teil1_lectures + c1_teil2_discussions + c1_teil3_evaluations

        st.subheader("Step 3: Choose Exam Part")
        teil = st.radio(
            "Which exam part?",
            teil_options[st.session_state["falowen_level"]],
            key="falowen_teil_center"
        )

        # Optional: topic picker (for Teil 2/3, not A1)
        picked_topic = None
        if st.session_state["falowen_level"] != "A1" and exam_topics:
            picked_topic = st.selectbox("Choose a topic (optional):", ["(random)"] + exam_topics)
            if picked_topic != "(random)":
                st.session_state["falowen_exam_topic"] = picked_topic
        else:
            st.session_state["falowen_exam_topic"] = None

        if st.button("⬅️ Back", key="falowen_back2"):
            st.session_state["falowen_stage"] = 2
            st.stop()
        if st.button("Start Practice", key="falowen_start_practice"):
            st.session_state["falowen_teil"] = teil
            st.session_state["falowen_stage"] = 4
            st.session_state["falowen_messages"] = []
            st.session_state["custom_topic_intro_done"] = False
        st.stop()

    # ---- STAGE 4: MAIN CHAT ----
    if st.session_state["falowen_stage"] == 4:
        level = st.session_state["falowen_level"]
        teil = st.session_state.get("falowen_teil", "")
        mode = st.session_state.get("falowen_mode", "")
        is_exam = mode == "Geführte Prüfungssimulation (Exam Mode)"
        is_custom_chat = mode == "Eigenes Thema/Frage (Custom Chat)"

        # ---- Show daily usage ----
        used_today = get_falowen_usage(student_code)
        st.info(f"Today: {used_today} / {FALOWEN_DAILY_LIMIT} Falowen chat messages used.")
        if used_today >= FALOWEN_DAILY_LIMIT:
            st.warning("You have reached your daily practice limit for Falowen today. Please come back tomorrow.")
            st.stop()

        # -- Controls: reset, back, change level
        def reset_chat():
            st.session_state["falowen_stage"] = 1
            st.session_state["falowen_messages"] = []
            st.session_state["falowen_teil"] = None
            st.session_state["falowen_mode"] = None
            st.session_state["custom_topic_intro_done"] = False
            st.session_state["falowen_turn_count"] = 0
            st.session_state["falowen_exam_topic"] = None
            st.rerun()

        def back_step():
            if st.session_state["falowen_stage"] > 1:
                st.session_state["falowen_stage"] -= 1
                st.session_state["falowen_messages"] = []
                st.rerun()

        def change_level():
            st.session_state["falowen_stage"] = 2
            st.session_state["falowen_messages"] = []
            st.rerun()

        # ---- Show chat history ----
        for msg in st.session_state["falowen_messages"]:
            if msg["role"] == "assistant":
                with st.chat_message("assistant", avatar="🧑‍🏫"):
                    st.markdown(
                        "<span style='color:#33691e;font-weight:bold'>🧑‍🏫 Herr Felix:</span>",
                        unsafe_allow_html=True
                    )
                    st.markdown(msg["content"])
            else:
                with st.chat_message("user"):
                    st.markdown(f"🗣️ {msg['content']}")

        # ---- PDF Download Button ----
        if st.session_state["falowen_messages"]:
            pdf_bytes = falowen_download_pdf(
                st.session_state["falowen_messages"],
                f"Falowen_Chat_{level}_{teil.replace(' ', '_') if teil else 'chat'}"
            )
            st.download_button(
                "⬇️ Download Chat as PDF",
                pdf_bytes,
                file_name=f"Falowen_Chat_{level}_{teil.replace(' ', '_') if teil else 'chat'}.pdf",
                mime="application/pdf"
            )

        # ---- Session Controls
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Restart Chat"):
                reset_chat()
        with col2:
            if st.button("Back"):
                back_step()
        with col3:
            if st.button("Change Level"):
                change_level()

        # ---- Initial instruction (only if chat is empty) ----
        if not st.session_state["falowen_messages"]:
            instruction = ""
            if is_exam:
                instruction = build_exam_instruction(level, teil)
            elif is_custom_chat:
                instruction = (
                    "Hallo! 👋 What would you like to talk about? Give me details of what you want so I can understand. "
                    "You can enter a topic, a question, or a keyword. I'll help you prepare for your class presentation."
                )
            if instruction:
                st.session_state["falowen_messages"].append({"role": "assistant", "content": instruction})
            # Do NOT call st.stop() here so input box is always visible

        # ---- Chat Input Box & OpenAI Response ----
        user_input = st.chat_input("Type your answer or message here...", key="falowen_user_input")

        if user_input:
            st.session_state["falowen_messages"].append({"role": "user", "content": user_input})
            inc_falowen_usage(student_code)  # increment daily usage

            # Spinner and OpenAI call
            with st.chat_message("assistant", avatar="🧑‍🏫"):
                with st.spinner("🧑‍🏫 Herr Felix is typing..."):
                    # System prompt logic
                    if is_exam:
                        system_prompt = build_exam_system_prompt(level, teil)
                    else:
                        system_prompt = build_custom_chat_prompt(level)

                    # Compose full history for OpenAI
                    messages = [{"role": "system", "content": system_prompt}]
                    messages += [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state["falowen_messages"]
                    ]

                    try:
                        completion = client.chat.completions.create(
                            model="gpt-4o",
                            messages=messages,
                            temperature=0.15,
                            max_tokens=600,
                        )
                        ai_reply = completion.choices[0].message.content.strip()
                    except Exception as e:
                        ai_reply = f"Sorry, an error occurred: {e}"

                    st.markdown(
                        "<span style='color:#33691e;font-weight:bold'>🧑‍🏫 Herr Felix:</span>",
                        unsafe_allow_html=True
                    )
                    st.markdown(ai_reply)
            # Save AI reply to session for next turn
            st.session_state["falowen_messages"].append({"role": "assistant", "content": ai_reply})


# =========================================
# VOCAB TRAINER TAB (A1–C1, with Progress, Streak, Goal, Gamification)
# =========================================

if tab == "Vocab Trainer":
    st.header("🧠 Vocab Trainer")

    student_code = st.session_state.get("student_code", "demo")
    student_name = st.session_state.get("student_name", "Demo")
    today_str = str(date.today())

    # --- Daily Streak (fetch from your helper/db) ---
    streak = get_vocab_streak(student_code)
    if streak >= 1:
        st.success(f"🔥 {streak}-day streak! Keep it up!")
    else:
        st.warning("You lost your streak. Start practicing today to get it back!")

    # --- Daily usage tracking ---
    vocab_usage_key = f"{student_code}_vocab_{today_str}"
    if "vocab_usage" not in st.session_state:
        st.session_state["vocab_usage"] = {}
    st.session_state["vocab_usage"].setdefault(vocab_usage_key, 0)
    used_today = st.session_state["vocab_usage"][vocab_usage_key]

    # --- Level selection ---
    if "vocab_level" not in st.session_state:
        st.session_state["vocab_level"] = "A1"
    vocab_level = st.selectbox("Choose level", ["A1", "A2", "B1", "B2", "C1"], key="vocab_level_select")
    if vocab_level != st.session_state["vocab_level"]:
        st.session_state["vocab_level"] = vocab_level
        st.session_state["vocab_feedback"] = ""
        st.session_state["show_next_button"] = False
        st.session_state["vocab_completed"] = set()

    # --- Track completed words (fetch from DB if you want to persist) ---
    if "vocab_completed" not in st.session_state:
        st.session_state["vocab_completed"] = set()
    completed_words = st.session_state["vocab_completed"]

    vocab_list = VOCAB_LISTS.get(vocab_level, [])
    is_tuple = isinstance(vocab_list[0], tuple) if vocab_list else False

    # --- List of words not yet completed ---
    new_words = [i for i in range(len(vocab_list)) if i not in completed_words]
    random.shuffle(new_words)

    # --- Visual progress bar for today's goal ---
    st.progress(
        min(used_today, VOCAB_DAILY_LIMIT) / VOCAB_DAILY_LIMIT,
        text=f"{used_today} / {VOCAB_DAILY_LIMIT} words practiced today"
    )

    # --- Badge if daily goal reached ---
    if used_today >= VOCAB_DAILY_LIMIT:
        st.balloons()
        st.success("✅ Daily Goal Complete! You’ve finished your vocab goal for today.")
        st.stop()

    # --- Main vocab practice ---
    if new_words:
        idx = new_words[0]
        word = vocab_list[idx][0] if is_tuple else vocab_list[idx]
        correct_answer = vocab_list[idx][1] if is_tuple else None

        st.markdown(f"🔤 **Translate this German word to English:** <b>{word}</b>", unsafe_allow_html=True)
        user_answer = st.text_input("Your English translation", key=f"vocab_answer_{idx}")

        if st.button("Check", key=f"vocab_check_{idx}"):
            # --- New answer logic ---
            if is_tuple:
                is_correct = is_close_answer(user_answer, correct_answer)
                almost = is_almost(user_answer, correct_answer)
            else:
                # For single-word vocab (e.g., advanced levels), use OpenAI for validation
                is_correct = validate_translation_openai(word, user_answer)
                almost = False

            # --- Show feedback ---
            if is_correct:
                st.success("✅ Correct!")
                completed_words.add(idx)
            elif almost:
                st.warning(
                    f"Almost! The correct answer is: <b>{correct_answer}</b>",
                    icon="⚠️",
                )
            else:
                st.error(
                    f"❌ Not quite. The correct answer is: <b>{correct_answer}</b>" if is_tuple else "❌ Not quite.",
                    icon="❗️",
                )

            # --- Save to DB ---
            save_vocab_submission(
                student_code=student_code,
                name=student_name,
                level=vocab_level,
                word=word,
                student_answer=user_answer,
                is_correct=is_correct,
            )
            st.session_state["vocab_usage"][vocab_usage_key] += 1
            st.rerun()
    else:
        st.success("🎉 You've finished all new words for this level today!")

    # --- Optionally: show summary of all words completed so far for this level ---
    if completed_words:
        st.info(f"You have completed {len(completed_words)} words in {vocab_level} so far. Try another level or come back tomorrow!")

# ====================================
# SCHREIBEN TRAINER TAB (with Daily Limit and Mobile UI)
# ====================================
import urllib.parse
from fpdf import FPDF

if tab == "Schreiben Trainer":
    st.header("✍️ Schreiben Trainer (Writing Practice)")

    # 1. Choose Level (remember previous)
    schreiben_levels = ["A1", "A2", "B1", "B2"]
    prev_level = st.session_state.get("schreiben_level", "A1")
    schreiben_level = st.selectbox(
        "Choose your writing level:",
        schreiben_levels,
        index=schreiben_levels.index(prev_level) if prev_level in schreiben_levels else 0,
        key="schreiben_level_selector"
    )
    st.session_state["schreiben_level"] = schreiben_level

    # 2. Daily limit tracking (by student & date)
    student_code = st.session_state.get("student_code", "demo")
    student_name = st.session_state.get("student_name", "")
    today_str = str(date.today())
    limit_key = f"{student_code}_schreiben_{today_str}"
    if "schreiben_usage" not in st.session_state:
        st.session_state["schreiben_usage"] = {}
    st.session_state["schreiben_usage"].setdefault(limit_key, 0)
    daily_so_far = st.session_state["schreiben_usage"][limit_key]

    # 3. Show overall writing performance (DB-driven, mobile-first)
    attempted, passed, accuracy = get_writing_stats(student_code)
    st.markdown(f"""**📝 Your Overall Writing Performance**
- 📨 **Submitted:** {attempted}
- ✅ **Passed (≥17):** {passed}
- 📊 **Pass Rate:** {accuracy}%
- 📅 **Today:** {daily_so_far} / {SCHREIBEN_DAILY_LIMIT}
""")

    # 4. Level-Specific Stats (optional)
    stats = get_student_stats(student_code)
    lvl_stats = stats.get(schreiben_level, {}) if stats else {}
    if lvl_stats and lvl_stats["attempted"]:
        correct = lvl_stats.get("correct", 0)
        attempted_lvl = lvl_stats.get("attempted", 0)
        st.info(f"Level `{schreiben_level}`: {correct} / {attempted_lvl} passed")
    else:
        st.info("_No previous writing activity for this level yet._")

    st.divider()

    # 5. Input Box (disabled if limit reached)
    user_letter = st.text_area(
        "Paste or type your German letter/essay here.",
        key="schreiben_input",
        disabled=(daily_so_far >= SCHREIBEN_DAILY_LIMIT),
        height=180,
        placeholder="Write your German letter here..."
    )

    # 6. AI prompt (always define before calling the API)
    ai_prompt = (
        f"You are Herr Felix, a supportive and innovative German letter writing trainer. "
        f"The student has submitted a {schreiben_level} German letter or essay. "
        "Write a brief comment in English about what the student did well and what they should improve while highlighting their points so they understand. "
        "Check if the letter matches their level. Talk as Herr Felix talking to a student and highlight the phrases with errors so they see it. "
        "Don't just say errors—show exactly where the mistakes are. "
        "1. Give a score out of 25 marks and always display the score clearly. "
        "2. If the score is 17 or more (17, 18, ..., 25), write: '**Passed: You may submit to your tutor!**'. "
        "3. If the score is 16 or less (16, 15, ..., 0), write: '**Keep improving before you submit.**'. "
        "4. Only write one of these two sentences, never both, and place it on a separate bolded line at the end of your feedback. "
        "5. Always explain why you gave the student that score based on grammar, spelling, vocabulary, coherence, and so on. "
        "6. Also check for AI usage or if the student wrote with their own effort. "
        "7. List and show the phrases to improve on with tips, suggestions, and what they should do. Let the student use your suggestions to correct the letter, but don't write the full corrected letter for them. "
        "Give scores by analyzing grammar, structure, vocabulary, etc. Explain to the student why you gave that score."
    )

    # 7. Submit & AI Feedback
    feedback = ""
    submit_disabled = daily_so_far >= SCHREIBEN_DAILY_LIMIT or not user_letter.strip()
    if submit_disabled and daily_so_far >= SCHREIBEN_DAILY_LIMIT:
        st.warning("You have reached today's writing practice limit. Please come back tomorrow.")

    if st.button("Get Feedback", type="primary", disabled=submit_disabled):
        with st.spinner("🧑‍🏫 Herr Felix is typing..."):
            try:
                completion = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": ai_prompt},
                        {"role": "user", "content": user_letter},
                    ],
                    temperature=0.6,
                )
                feedback = completion.choices[0].message.content
            except Exception as e:
                st.error("AI feedback failed. Please check your OpenAI setup.")
                feedback = None

        if feedback:
            # === Extract score and check if passed ===
            import re
            # Robust regex for score detection
            score_match = re.search(
                r"score\s*(?:[:=]|is)?\s*(\d+)\s*/\s*25",
                feedback,
                re.IGNORECASE,
            )
            if not score_match:
                score_match = re.search(r"Score[:\s]+(\d+)\s*/\s*25", feedback, re.IGNORECASE)
            if score_match:
                score = int(score_match.group(1))
            else:
                st.warning("Could not detect a score in the AI feedback.")
                score = 0

            # === Update usage and save to DB ===
            st.session_state["schreiben_usage"][limit_key] += 1
            save_schreiben_submission(
                student_code, student_name, schreiben_level, user_letter, score, feedback
            )

            # --- Show Feedback ---
            st.markdown("---")
            st.markdown("#### 📝 Feedback from Herr Felix")
            st.markdown(feedback)

            # === Download as PDF ===
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            pdf.multi_cell(0, 10, f"Your Letter:\n\n{user_letter}\n\nFeedback from Herr Felix:\n\n{feedback}")
            pdf_output = f"Feedback_{student_code}_{schreiben_level}.pdf"
            pdf.output(pdf_output)
            with open(pdf_output, "rb") as f:
                pdf_bytes = f.read()
            st.download_button(
                "⬇️ Download Feedback as PDF",
                pdf_bytes,
                file_name=pdf_output,
                mime="application/pdf"
            )
            import os
            os.remove(pdf_output)

            # === WhatsApp Share ===
            wa_message = f"Hi, here is my German letter and AI feedback:\n\n{user_letter}\n\nFeedback:\n{feedback}"
            wa_url = (
                "https://api.whatsapp.com/send"
                "?phone=233205706589"
                f"&text={urllib.parse.quote(wa_message)}"
            )
            st.markdown(
                f"[📲 Send to Tutor on WhatsApp]({wa_url})",
                unsafe_allow_html=True
            )

def safe_latin1(text):
    return text.encode("latin1", "replace").decode("latin1")

def fetch_scores_from_github():
    url = "https://raw.githubusercontent.com/learngermanghana/grammarhelper/main/scores_backup.csv"
    try:
        response = requests.get(url)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        if "StudentCode" in df.columns:
            df = df.rename(columns={"StudentCode": "student_code"})
        if "Level" not in df.columns:
            df["Level"] = ""
        df["student_code"] = df["student_code"].astype(str).str.strip().str.lower()
        df["Level"] = df["Level"].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.error(f"Failed to fetch scores from GitHub: {e}")
        return pd.DataFrame()

if tab == "My Results and Resources":
    import io  # Make sure this is at the top of your file!
    st.header("📈 My Results and Resources Hub")
    st.markdown("View and download your assignment history. All results are private and only visible to you.")

    GITHUB_RAW_URL = "https://raw.githubusercontent.com/learngermanghana/grammarhelper/main/scores_backup.csv"

    # -- Fetch and prepare data --
    def fetch_scores_from_github():
        try:
            r = requests.get(GITHUB_RAW_URL, timeout=7)
            r.raise_for_status()
            # Use io.StringIO (not StringIO) for Streamlit/Colab compatibility
            df = pd.read_csv(io.StringIO(r.text), sep=None, engine='python')
            # Standardize columns
            col_map = {}
            for c in df.columns:
                base = c.strip().lower()
                if base == "studentcode": base = "student_code"
                col_map[c] = base
            df = df.rename(columns=col_map)
            # Now ensure required columns exist
            if "student_code" not in df.columns:
                st.error("CSV is missing 'student_code' column. Please check your upload or contact support.")
                st.stop()
            return df
        except Exception as e:
            st.error(f"Failed to fetch scores from GitHub: {e}")
            return pd.DataFrame()

    df_scores = fetch_scores_from_github()
    required_cols = {"student_code", "assignment", "score", "comments", "date", "level", "name"}
    missing_cols = required_cols - set(df_scores.columns)
    if missing_cols:
        st.error(f"CSV is missing required columns: {', '.join(missing_cols)}")
        st.stop()

    student_code = st.session_state.get("student_code", "").strip().lower()
    df_scores["student_code"] = df_scores["student_code"].astype(str).str.lower().str.strip()
    df_student = df_scores[df_scores["student_code"] == student_code]

    if df_student.empty:
        st.info("No results available yet. Complete an assignment to see your scores!")
    else:
        # Level Filter (A1, A2, B1, B2)
        level_options = sorted(df_student["level"].dropna().unique())
        level = st.selectbox("Choose your level to view results:", level_options, index=0)

        df_this_level = df_student[df_student["level"] == level]

        # Key stats (change these if your assignment counts ever change)
        total_assignments_dict = {
            "A1": 18, "A2": 28, "B1": 26, "B2": 24  # Update as needed
        }
        total_assignments = total_assignments_dict.get(level, 0)
        completed = df_this_level["assignment"].nunique()
        not_completed = max(0, total_assignments - completed)
        avg_score = df_this_level["score"].mean() if completed else 0
        best_score = df_this_level["score"].max() if completed else 0

        st.markdown(f"""
        - **Assignments for {level}:** {total_assignments}
        - **Completed:** {completed}
        - **Not Completed:** {not_completed}
        - **Average Score:** {avg_score:.1f}
        - **Best Score:** {best_score}
        """)

        # Table: highlight best per assignment
        df_this_level = df_this_level.copy()
        # Mark best score per assignment
        df_this_level['is_best'] = df_this_level.groupby('assignment')['score'].transform('max') == df_this_level['score']

        def highlight_best(val, is_best):
            return 'background-color: #d4edda' if is_best else ''

        # We have to drop the 'is_best' for display but use for styling
        styled = df_this_level[["assignment", "score", "comments", "date", "is_best"]].sort_values(
            ["assignment", "score"], ascending=[True, False]
        ).style.apply(
            lambda row: [
                highlight_best(v, row["is_best"]) if col != "is_best" else ''
                for col, v in zip(row.index, row.values)
            ],
            axis=1
        )
        st.dataframe(
            styled.hide_columns(['is_best']),
            use_container_width=True,
            hide_index=True
        )


        # PDF Download: summary + full table
        import tempfile
        import os

        if st.button("Download My Full Results as PDF"):
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_pdf import PdfPages

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                pdf_file = tmp_pdf.name
                # Simple text PDF (matplotlib workaround)
                with PdfPages(pdf_file) as pdf:
                    # First page: summary
                    plt.figure(figsize=(8, 4))
                    plt.axis('off')
                    plt.title(f"My Results for {level}", fontsize=16, fontweight='bold')
                    txt = (
                        f"Student: {df_this_level['Name'].iloc[0]}\n"
                        f"Level: {level}\n"
                        f"Assignments: {total_assignments}\n"
                        f"Completed: {completed}\n"
                        f"Not Completed: {not_completed}\n"
                        f"Average Score: {avg_score:.1f}\n"
                        f"Best Score: {best_score}\n"
                        f"Official use only. Signed: Felix Asadu\n"
                        f"School: Learn Language Education Academy\n"
                        f"Email: learngermanghana@gmail.com\n"
                        f"Website: www.learngermanghana.com"
                    )
                    plt.text(0, 1, txt, fontsize=12, va='top')
                    pdf.savefig()
                    plt.close()
                    # Second page: Table
                    plt.figure(figsize=(10, 0.5 + 0.4 * len(df_this_level)))
                    plt.axis('off')
                    tbl = plt.table(
                        cellText=df_this_level[["Assignment", "Score", "Comments", "Date"]].values,
                        colLabels=["Assignment", "Score", "Comments", "Date"],
                        loc='center'
                    )
                    tbl.auto_set_font_size(False)
                    tbl.set_fontsize(10)
                    tbl.scale(1.2, 1.2)
                    pdf.savefig()
                    plt.close()

                # Download
                with open(pdf_file, "rb") as f:
                    st.download_button(
                        label="⬇️ Download PDF",
                        data=f.read(),
                        file_name=f"My_Results_{level}.pdf",
                        mime="application/pdf"
                    )
                os.remove(pdf_file)
        
        # Resource downloads section
        st.divider()
        st.subheader("📚 Resources & Downloads")
        st.markdown(
            "- [Sample Letter Format (PDF)](https://www.goethe.de/resources/files/pdf222/schreiben_teil2_a2_modellsatz.pdf)"
            "\n- [German Grammar Topics A1–B1 (PDF)](https://learngermanghana.com/german_grammar.pdf)"
            "\n- [Official Exam Info (Website)](https://www.goethe.de/en/spr/kup/prf/prf/gzsd1/ueb.html)"
        )
