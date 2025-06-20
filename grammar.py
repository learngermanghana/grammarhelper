# ====================================
# 1. IMPORTS, CONSTANTS, AND PAGE SETUP
# ====================================

import streamlit as st
from openai import OpenAI
import random
import pandas as pd
import difflib
import os
import sqlite3
from datetime import date, datetime, timedelta

import streamlit as st
import pandas as pd

# Load your student list once (only on first run)
@st.cache_data
def load_student_data():
    df = pd.read_csv("students.csv.csv")  # Use correct path
    df.columns = [c.strip() for c in df.columns]  # Remove any header whitespace
    return df

df_students = load_student_data()

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "student_row" not in st.session_state:
    st.session_state["student_row"] = None

if not st.session_state["logged_in"]:
    st.title("🔑 Student Login")
    login_input = st.text_input("Enter your Student Code or Email to begin:").strip().lower()
    if st.button("Login"):
        found = df_students[
            (df_students["StudentCode"].astype(str).str.lower().str.strip() == login_input) |
            (df_students["Email"].astype(str).str.lower().str.strip() == login_input)
        ]
        if not found.empty:
            st.session_state["logged_in"] = True
            st.session_state["student_row"] = found.iloc[0].to_dict()
            st.success(f"Welcome, {st.session_state['student_row']['Name']}! Login successful.")
            st.rerun()
        else:
            st.error("Login failed. Please check your Student Code or Email and try again.")
    st.stop()


# --- Helper to load student data ---
def load_student_data():
    if not os.path.exists(STUDENTS_CSV):
        st.error("Students file not found!")
        return pd.DataFrame()
    df = pd.read_csv(STUDENTS_CSV)
    for col in ["StudentCode", "Email"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.lower()
    return df

# --- Student login logic ---
if "student_code" not in st.session_state:
    st.session_state["student_code"] = ""
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("🔑 Student Login")
    login_input = st.text_input("Enter your **Student Code** or **Email** to begin:")
    if st.button("Login"):
        login_input_clean = login_input.strip().lower()
        df_students = load_student_data()
        match = df_students[
            (df_students["StudentCode"].str.lower() == login_input_clean) | 
            (df_students["Email"].str.lower() == login_input_clean)
        ]
        if not match.empty:
            st.session_state["student_code"] = match.iloc[0]["StudentCode"].lower()
            st.session_state["logged_in"] = True
            st.session_state["student_info"] = match.iloc[0].to_dict()
            st.success("Welcome! Login successful.")
            st.rerun()
        else:
            st.error("Login failed. Code or Email not recognized.")
            st.stop()
    st.stop()



# --- After login, show dashboard at the top ---
if st.session_state["logged_in"]:
    st.header("🎓 Student Dashboard")
    student = st.session_state["student_row"]
    st.markdown(f"""
    <div style='background:#f9f9ff;padding:18px 24px;border-radius:15px;margin-bottom:18px;box-shadow:0 2px 10px #eef;'>
        <h3 style='margin:0;color:#17617a;'>{student['Name']}</h3>
        <ul style='list-style:none;padding:0;font-size:1.08rem;'>
            <li><b>Level:</b> {student['Level']}</li>
            <li><b>Student Code:</b> {student['StudentCode']}</li>
            <li><b>Email:</b> {student['Email']}</li>
            <li><b>Phone:</b> {student['Phone']}</li>
            <li><b>Location:</b> {student['Location']}</li>
            <li><b>Paid:</b> {student['Paid']}</li>
            <li><b>Balance:</b> {student['Balance']}</li>
            <li><b>Contract Start:</b> {student['ContractStart']}</li>
            <li><b>Contract End:</b> {student['ContractEnd']}</li>
            <li><b>Status:</b> {student.get('Status', '')}</li>
            <li><b>Enroll Date:</b> {student.get('EnrollDate', '')}</li>
            <li><b>Emergency Contact:</b> {student.get('Emergency Contact (Phone Number)', '')}</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)


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
        <img src='https://cdn-icons-png.flaticon.com/512/6815/6815043.png' width='54' style='border-radius:50%;border:2.5px solid #51a8d2;box-shadow:0 2px 8px #cbe7fb;'/>
        <div>
            <span style='font-size:2.1rem;font-weight:bold;color:#17617a;letter-spacing:2px;'>Falowen</span><br>
            <span style='font-size:1.08rem;color:#268049;'>Your personal German speaking coach (Herr Felix)</span>
        </div>
    </div>
    """, unsafe_allow_html=True
)

# ====================================
# 2. SQLITE SETUP & HELPER FUNCTIONS
# ====================================

conn = sqlite3.connect("vocab_progress.db", check_same_thread=False)
c = conn.cursor()
c.execute("""
    CREATE TABLE IF NOT EXISTS vocab_progress (
        student_code TEXT,
        date TEXT,
        level TEXT,
        word TEXT,
        correct INTEGER,
        PRIMARY KEY (student_code, date, level, word)
    )
""")
conn.commit()

def save_vocab_progress(student_code, level, word, correct):
    today = str(date.today())
    c.execute("""
        INSERT OR REPLACE INTO vocab_progress (student_code, date, level, word, correct)
        VALUES (?, ?, ?, ?, ?)
    """, (student_code, today, level, word, int(correct)))
    conn.commit()

def load_vocab_progress(student_code, level):
    today = str(date.today())
    c.execute("""
        SELECT word, correct FROM vocab_progress
        WHERE student_code=? AND date=? AND level=?
    """, (student_code, today, level))
    return dict(c.fetchall())

# --- Student Dashboard Helpers ---
def get_student_stats(student_code):
    today = str(date.today())
    c.execute("""
        SELECT level, COUNT(*), SUM(correct)
        FROM vocab_progress
        WHERE student_code=? AND date=?
        GROUP BY level
    """, (student_code, today))
    stats = {row[0]: {"attempted": row[1], "correct": row[2]} for row in c.fetchall()}
    return stats

def get_vocab_streak(student_code):
    c.execute("""
        SELECT DISTINCT date FROM vocab_progress WHERE student_code=? ORDER BY date DESC
    """, (student_code,))
    dates = [row[0] for row in c.fetchall()]
    if not dates:
        return 0
    streak = 1
    prev = datetime.strptime(dates[0], "%Y-%m-%d")
    for d in dates[1:]:
        next_day = prev - timedelta(days=1)
        if datetime.strptime(d, "%Y-%m-%d") == next_day:
            streak += 1
            prev = next_day
        else:
            break
    return streak

# ====================================
# 3. FLEXIBLE ANSWER CHECKERS
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

# ====================================
# 4. CONSTANTS & VOCAB LISTS
# ====================================

CODES_FILE = "student_codes.csv"
FALOWEN_DAILY_LIMIT = 25
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
# 5. STUDENT LOGIN AND MAIN MENU
# ====================================

def load_codes():
    if os.path.exists(CODES_FILE):
        df = pd.read_csv(CODES_FILE)
        if "code" not in df.columns:
            df = pd.DataFrame(columns=["code"])
        df["code"] = df["code"].astype(str).str.strip().str.lower()
    else:
        df = pd.DataFrame(columns=["code"])
    return df

if "student_code" not in st.session_state:
    st.session_state["student_code"] = ""
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("🔑 Student Login")
    code = st.text_input("Enter your student code to begin:")
    if st.button("Login"):
        code_clean = code.strip().lower()
        df_codes = load_codes()
        if code_clean in df_codes["code"].dropna().tolist():
            st.session_state["student_code"] = code_clean
            st.session_state["logged_in"] = True
            st.success("Welcome! Login successful.")
            st.rerun()
        else:
            st.error("This code is not recognized. Please check with your tutor.")
            st.stop()
    st.stop()

# ====================================
# 6. MAIN TAB SELECTOR (with Dashboard)
# ====================================

if st.session_state["logged_in"]:
    student_code = st.session_state.get("student_code", "")
    st.header("Choose Practice Mode")
    tab = st.radio(
        "How do you want to practice?",
        ["Dashboard", "Falowen Chat", "Vocab Trainer", "Schreiben Trainer"],
        key="main_tab_select"
    )
    st.markdown(
        f"<div style='background:#e0f2ff;border-radius:12px;padding:12px 18px;margin-bottom:12px;font-size:1.2rem;'>"
        f"🔹 <b>Active:</b> {tab}</div>",
        unsafe_allow_html=True
    )

    if tab == "Dashboard":
        st.header("📊 Student Dashboard")
        # --- Show main stats ---
        stats = get_student_stats(student_code)
        streak = get_vocab_streak(student_code)
        st.info(f"🔥 **Vocab Streak:** {streak} days")
        if stats:
            st.markdown("**Today's Vocab Progress:**")
            for lvl, d in stats.items():
                st.markdown(
                    f"- `{lvl}`: {d['correct'] or 0} / {d['attempted']} correct"
                )
        else:
            st.markdown("_No vocab activity today yet!_")


    # -----------------------------------
    #        FALOWEN CHAT TAB
    # -----------------------------------
    if tab == "Falowen Chat":
        st.header("🗣️ Falowen – Speaking & Exam Trainer")

        # --- Session state variable setup ---
        for key, default in [
            ("falowen_stage", 1),
            ("falowen_mode", None),
            ("falowen_level", None),
            ("falowen_teil", None),
            ("falowen_messages", []),
            ("custom_topic_intro_done", False),
            ("custom_chat_level", None),
        ]:
            if key not in st.session_state:
                st.session_state[key] = default

        # ---- Step 1: Practice Mode ----
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

        # ---- Step 2: Level Selection ----
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

        # ---- Step 3: Exam Teil (for Exam Mode) ----
        if st.session_state["falowen_stage"] == 3:
            teil_options = {
                "A1": [
                    "Teil 1 – Basic Introduction",
                    "Teil 2 – Question and Answer",
                    "Teil 3 – Making A Request"
                ],
                "A2": [
                    "Teil 1 – Fragen zu Schlüsselwörtern",
                    "Teil 2 – Bildbeschreibung & Diskussion",
                    "Teil 3 – Gemeinsam planen"
                ],
                "B1": [
                    "Teil 1 – Gemeinsam planen (Dialogue)",
                    "Teil 2 – Präsentation (Monologue)",
                    "Teil 3 – Feedback & Fragen stellen"
                ],
                "B2": [
                    "Teil 1 – Diskussion",
                    "Teil 2 – Präsentation",
                    "Teil 3 – Argumentation"
                ],
                "C1": [
                    "Teil 1 – Vortrag",
                    "Teil 2 – Diskussion",
                    "Teil 3 – Bewertung"
                ]
            }
            st.subheader("Step 3: Choose Exam Part")
            teil = st.radio(
                "Which exam part?",
                teil_options[st.session_state["falowen_level"]],
                key="falowen_teil_center"
            )
            if st.button("⬅️ Back", key="falowen_back2"):
                st.session_state["falowen_stage"] = 2
                st.stop()
            if st.button("Start Practice", key="falowen_start_practice"):
                st.session_state["falowen_teil"] = teil
                st.session_state["falowen_stage"] = 4
                st.session_state["falowen_messages"] = []
                st.session_state["custom_topic_intro_done"] = False
            st.stop()

        # ---- Step 4: Main Chat + User Input ----
        if st.session_state["falowen_stage"] == 4:
            falowen_usage_key = f"{st.session_state['student_code']}_falowen_{str(date.today())}"
            if "falowen_usage" not in st.session_state:
                st.session_state["falowen_usage"] = {}
            st.session_state["falowen_usage"].setdefault(falowen_usage_key, 0)

            # ---------- --AI ALWAYS STARTS THE CHAT IF EMPTY--- ---------------
            if not st.session_state["falowen_messages"]:
                mode  = st.session_state.get("falowen_mode", "")
                level = st.session_state.get("falowen_level", "A1")
                teil  = st.session_state.get("falowen_teil", "")

                # --------- EXAM MODE FIRST MESSAGE ---------
                if mode == "Geführte Prüfungssimulation (Exam Mode)":
                    if level == "A1" and teil.startswith("Teil 1"):
                        ai_first = (
                            "👋 For speaking part 1, you'll be asked to introduce yourself using these keywords: Name, Age, Place of Residence, Languages, Job, Hobby. "
                            "Afterwards, the examiner will pick your response and ask a few random questions. Let's practice: please type your introduction including all the keywords above."
                        )
                    elif level == "A1" and teil.startswith("Teil 2"):
                        ai_first = (
                            "Now we practice questions and answers! The topic is: 'Geschäft – schließen' (shop – to close). Please ask me a question about this in German."
                        )
                    elif level == "A1" and teil.startswith("Teil 3"):
                        ai_first = (
                            "Let's practice making polite requests! Please write a polite request, for example: 'Können Sie bitte das Fenster zumachen?'"
                        )
                    elif level == "A2" and teil.startswith("Teil 1"):
                        ai_first = (
                            "Let's start with your daily routine! Please tell me: What is the first thing you do in the morning?"
                        )
                    elif level == "A2" and teil.startswith("Teil 2"):
                        ai_first = (
                            "Describe the picture you see or answer my question about the topic 'Wetter' (weather)."
                        )
                    elif level == "A2" and teil.startswith("Teil 3"):
                        ai_first = (
                            "Let's make a plan together! What do you suggest?"
                        )
                    elif level == "B1" and teil.startswith("Teil 1"):
                        ai_first = (
                            "Welcome to the B1 exam – Planning together! Let's plan an activity together. What do you suggest?"
                        )
                    elif level == "B1" and teil.startswith("Teil 2"):
                        ai_first = (
                            "Now it's time for your presentation! Please introduce your topic. What would you like to talk about?"
                        )
                    elif level == "B1" and teil.startswith("Teil 3"):
                        ai_first = (
                            "You have just finished your presentation. Now I will ask you questions about it. Are you ready?"
                        )
                    elif level == "B2" and teil.startswith("Teil 1"):
                        topic = random.choice(b2_teil1_topics)
                        ai_first = f"Willkommen zur B2-Diskussion!\n\n**Thema:** {topic}\n\nWas denkst du dazu?"
                    elif level == "B2" and teil.startswith("Teil 2"):
                        presentation = random.choice(b2_teil2_presentations)
                        ai_first = f"Halte bitte deine Präsentation zum Thema:\n\n**{presentation}**\n\nTeile deine Meinung und Erfahrungen."
                    elif level == "B2" and teil.startswith("Teil 3"):
                        argument = random.choice(b2_teil3_arguments)
                        ai_first = f"Jetzt führen wir eine Argumentation.\n\n**Thema:** {argument}\n\nWas ist dein Standpunkt?"
                    elif level == "C1" and teil.startswith("Teil 1"):
                        lecture = random.choice(c1_teil1_lectures)
                        ai_first = f"Willkommen zur C1-Prüfung – Vortrag.\n\n**Vortragsthema:** {lecture}\n\nBitte halte einen kurzen Vortrag dazu."
                    elif level == "C1" and teil.startswith("Teil 2"):
                        discussion = random.choice(c1_teil2_discussions)
                        ai_first = f"Diskutiere bitte ausführlich mit mir über:\n\n**{discussion}**"
                    elif level == "C1" and teil.startswith("Teil 3"):
                        evaluation = random.choice(c1_teil3_evaluations)
                        ai_first = f"Jetzt kommt die Bewertung.\n\n**Thema:** {evaluation}\n\nWas ist deine abschließende Meinung?"
                    else:
                        ai_first = "Welcome to the exam! Let's begin. Please introduce yourself."
                # --------- CUSTOM CHAT FIRST MESSAGE ---------
                elif mode == "Eigenes Thema/Frage (Custom Chat)":
                    ai_first = (
                        "Hello! 👋 I'm Herr Felix, your AI examiner. Please give a topic or ask your first question, and I'll help you practice."
                    )
                else:
                    ai_first = "Hello! What would you like to practice today?"
                st.session_state["falowen_messages"].append({"role": "assistant", "content": ai_first})

            st.info(
                f"Today's practice: {st.session_state['falowen_usage'][falowen_usage_key]}/{FALOWEN_DAILY_LIMIT}"
            )

            # Show chat history
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

            # ------------- User input & daily limit enforcement -------------
            session_ended = st.session_state["falowen_usage"][falowen_usage_key] >= FALOWEN_DAILY_LIMIT

            if session_ended:
                st.warning("You have reached today's practice limit for Falowen Chat. Come back tomorrow!")
            else:
                user_input = st.chat_input("💬 Type your answer here...", key="falowen_input")
                if user_input:
                    st.session_state["falowen_messages"].append({"role": "user", "content": user_input})
                    if "falowen_turn_count" not in st.session_state:
                        st.session_state["falowen_turn_count"] = 0
                    st.session_state["falowen_turn_count"] += 1
                    st.session_state["falowen_usage"][falowen_usage_key] += 1
                    # <---- Insert your OpenAI reply logic here! ---->



                # ======== AI PROMPT/LOGIC FOR EXAM + CUSTOM CHAT ========
                level = st.session_state.get("falowen_level")
                teil = st.session_state.get("falowen_teil", "")
                mode = st.session_state.get("falowen_mode", "")
                is_exam_mode = mode == "Geführte Prüfungssimulation (Exam Mode)"
                is_custom_chat = mode == "Eigenes Thema/Frage (Custom Chat)"
                is_b1_teil3 = (
                    is_exam_mode and level == "B1" and teil.startswith("Teil 3") and "current_b1_teil3_topic" in st.session_state
                )

                # ---- A1 Exam Mode Prompts ----
                if is_exam_mode and level == "A1":
                    if teil.startswith("Teil 1"):
                        ai_system_prompt = (
                            "You are Herr Felix, an A1 German examiner. "
                            "Check the student's self-introduction (name, age, etc), correct errors, and give a grammar tip in English. "
                            "If the answer is perfect, praise them. Then, if ready, ask the next follow-up question from your internal list."
                        )
                    elif teil.startswith("Teil 2"):
                        ai_system_prompt = (
                            "You are Herr Felix, an A1 German examiner. "
                            "Check the student's question and answer for the topic and keyword, correct errors, and give a grammar tip in English. "
                            "If the answer is perfect, praise them. Then introduce the next Thema and keyword as the next prompt."
                        )
                    elif teil.startswith("Teil 3"):
                        ai_system_prompt = (
                            "You are Herr Felix, an A1 German examiner. "
                            "Check the student's polite request, correct errors, and give a grammar tip in English. "
                            "If the answer is perfect, praise them. Then give the next polite request prompt."
                        )
                # ---- CUSTOM CHAT AND ALL OTHER MODES ----
                else:
                    ai_system_prompt = (
                        "You are Herr Felix, a supportive and creative German examiner. "
                        "Continue the conversation, give simple corrections, and ask the next question."
                    )
                    if is_b1_teil3:
                        b1_topic = st.session_state['current_b1_teil3_topic']
                        ai_system_prompt = (
                            "You are Herr Felix, the examiner in a German B1 oral exam (Teil 3: Feedback & Questions). "
                            f"**IMPORTANT: Stay strictly on the topic:** {b1_topic}. "
                            "After student ask the question and you have given the student compliment, give another topic for the student ask the question. "
                            "The student is supposed to ask you one valid question about their presentation. "
                            "1. Read the student's message. "
                            "2. Praise if it's valid or politely indicate what's missing. "
                            "3. If valid, answer briefly in simple German. "
                            "4. End with clear exam tips in English. "
                            "Stay friendly, creative and exam-like."
                        )
                    elif is_custom_chat:
                        lvl = st.session_state.get('custom_chat_level', level)
                        if lvl == 'A2':
                            ai_system_prompt = (
                                "You are Herr Felix, a friendly but creative A2 German teacher and exam trainer. "
                                "Greet and give students ideas and examples about how to talk about the topic in English and ask only question. No correction or answer in the statement but only tip and possible phrases to use. This stage only when the student input their first question and not anyother input. "
                                "The first input from the student is their topic and not their reply or sentence or answer. It is always their presentation topic. Only the second and further repliers it their response to your question "
                                "Use simple English and German to correct the student's last answer. Tip and necessary suggestions should be explained in English with German supporting for student to understand. They are A2 beginners student. "
                                "You can also suggest keywords when needed. Ask one question only. Format your reply with answer, correction explanation in english, tip in english, and next question in German."
                            )
                        elif lvl == 'B1':
                            if not st.session_state.get('custom_topic_intro_done', False):
                                ai_system_prompt = (
                                    "You are Herr Felix, a supportive and creative B1 German teacher. "
                                    "The first input from the student is their topic and not their reply or sentence or answer. It is always their presentation topic. Only the second and further repliers it their response to your question "
                                    "Provide practical ideas/opinions/advantages/disadvantages/situation in their homeland for the topic in German and English, then ask one opinion question. No correction or answer in the statement but only tip and possible phrases to use. This stage only when the student input their first question and not anyother input. "
                                    "Support ideas and opinions explanation in English and German as these students are new B1 students. "
                                    "Ask creative question that helps student to learn how to answer opinions,advantages,disadvantages,situation in their country and so on. "
                                    "Always put the opinion question on a separate line so the student can notice the question from the ideas and examples"
                                )
                            else:
                                ai_system_prompt = (
                                    "You are Herr Felix, a supportive B1 German teacher. "
                                    "Reply in German and English, correct last answer, give a tip in English, and ask one question on the same topic."
                                )
                        elif lvl == 'A1':
                            ai_system_prompt = (
                                "You are Herr Felix, a supportive and creative A1 German teacher and exam trainer. "
                                "If the student's first input is an introduction, analyze it and give feedback in English with suggestions for improvement. "
                                "If the student asks a question with an answer, respond in the correct A1 exam format and ask a new question using the A1 keywords list. "
                                "For requests, reply as an examiner, give correction, and suggest other requests. "
                                "Offer to let the student decide how many practices/questions to do today. "
                                "Always be supportive and explain your feedback clearly in English."
                            )
                        elif lvl in ['B2', 'C1']:
                            ai_system_prompt = (
                                "You are Herr Felix, a supportive, creative, but strict B2/C1 German examiner. "
                                "Always correct the student's answer in both English and German. "
                                "Encourage deeper reasoning, advanced grammar, and real-world vocabulary in your feedback and questions."
                            )
                    # Mark intro done for B1/B2/C1 after first student reply
                    if is_custom_chat and lvl in ['B1', 'B2', 'C1'] and not st.session_state.get("custom_topic_intro_done", False):
                        st.session_state["custom_topic_intro_done"] = True

                conversation = [{"role": "system", "content": ai_system_prompt}] + st.session_state["falowen_messages"]
                with st.spinner("🧑‍🏫 Herr Felix is typing..."):
                    try:
                        client = OpenAI(api_key=st.secrets["general"]["OPENAI_API_KEY"])
                        resp = client.chat.completions.create(model="gpt-4o", messages=conversation)
                        ai_reply = resp.choices[0].message.content
                    except Exception as e:
                        ai_reply = "Sorry, there was a problem generating a response."
                        st.error(str(e))
                st.session_state["falowen_messages"].append({"role": "assistant", "content": ai_reply})

            # --- Show if session ended ---
            if session_ended:
                st.warning("You have reached today's practice limit for Falowen Chat. Come back tomorrow!")
            else:
                # main chat logic
                ...
       
            # ------------- Navigation Buttons --------------
            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("⬅️ Back", key="falowen_back"):
                    st.session_state.update({
                        "falowen_stage": 1,
                        "falowen_messages": [],
                        "falowen_turn_count": 0,
                        "custom_chat_level": None,
                        "custom_topic_intro_done": False
                    })
                    st.rerun()
                    st.stop
            with col2:
                if st.button("🔄 Restart Chat", key="falowen_restart"):
                    st.session_state.update({
                        "falowen_messages": [],
                        "falowen_turn_count": 0,
                        "custom_chat_level": None,
                        "custom_topic_intro_done": False
                    })
                    st.rerun()
            with col3:
                if st.button("Next ➡️ (Summary)", key="falowen_summary"):
                    st.success("Summary not implemented yet (placeholder).")


# =========================================
# VOCAB TRAINER TAB (A1–C1, with Progress)
# =========================================

import difflib
import random

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

if tab == "Vocab Trainer":
    st.header("🧠 Vocab Trainer")

    vocab_usage_key = f"{st.session_state['student_code']}_vocab_{str(date.today())}"
    if "vocab_usage" not in st.session_state:
        st.session_state["vocab_usage"] = {}
    st.session_state["vocab_usage"].setdefault(vocab_usage_key, 0)

    # --- Stats/progress session vars ---
    if "vocab_today_history" not in st.session_state:
        st.session_state["vocab_today_history"] = []
    if "vocab_correct_today" not in st.session_state:
        st.session_state["vocab_correct_today"] = 0

    vocab_level = st.selectbox(
        "Choose your level:",
        ["A1", "A2", "B1", "B2", "C1"],
        key="vocab_level_select"
    )
    vocab_list = VOCAB_LISTS.get(vocab_level, [])

    # --- STATS & PROGRESS ALWAYS ON TOP ---
    correct_count = st.session_state["vocab_correct_today"]
    attempted = len(st.session_state["vocab_today_history"])
    st.info(f"Today's correct answers: {correct_count}/{VOCAB_DAILY_LIMIT}")
    st.progress(min(1, correct_count / VOCAB_DAILY_LIMIT))

    # Show all attempted today
    if attempted:
        st.caption("**Today's attempts:**")
        for idx, item in enumerate(st.session_state["vocab_today_history"], 1):
            word, answer, correct, eng = item
            symbol = "✅" if correct else "❌"
            st.markdown(f"{idx}. **{word}** → _{answer}_ {symbol} <span style='color:#888'>({eng})</span>", unsafe_allow_html=True)

    # --- SESSION END CHECK ---
    session_ended = st.session_state["vocab_usage"][vocab_usage_key] >= VOCAB_DAILY_LIMIT
    if session_ended:
        st.success("You've reached your vocab limit for today. Come back tomorrow!")
        st.stop()

    # --- Avoid repeating words already attempted today ---
    already_asked = [item[0] for item in st.session_state["vocab_today_history"]]
    pool = [item for item in vocab_list if (item[0] if isinstance(item, tuple) else item) not in already_asked]
    if not pool:
        st.success("Super! You've tried all words at this level today.")
        st.stop()

    # --- Pick the next word (repeatable after check) ---
    current_vocab = random.choice(pool)
    if isinstance(current_vocab, tuple):
        current_word, correct_eng = current_vocab
    else:
        current_word = current_vocab
        correct_eng = ""  # For B1/C1

    st.subheader(f"🔤 Translate this German word to English: **{current_word}**")
    vocab_answer = st.text_input("Your English translation", key=f"vocab_answer_{current_word}")

    # --- "Check Answer" Button ---
    check_clicked = st.button("Check Answer")
    show_feedback = False
    feedback_msg = ""
    example_sentence = ""

    if check_clicked:
        if vocab_level in ["B1", "B2", "C1"] and not correct_eng:
            st.session_state["vocab_today_history"].append((current_word, vocab_answer, True, "-"))
            st.session_state["vocab_usage"][vocab_usage_key] += 1
            st.session_state["vocab_correct_today"] += 1
            feedback_msg = "Good! B1/B2/C1 vocab is for exposure. Try to learn the meaning."
        else:
            is_correct = is_close_answer(vocab_answer, correct_eng)
            is_nearly = is_almost(vocab_answer, correct_eng) and not is_correct
            st.session_state["vocab_today_history"].append(
                (current_word, vocab_answer, is_correct, correct_eng)
            )
            st.session_state["vocab_usage"][vocab_usage_key] += 1
            if is_correct:
                st.session_state["vocab_correct_today"] += 1
                feedback_msg = f"✅ Correct! '{current_word}' means **{correct_eng}**."
            elif is_nearly:
                feedback_msg = f"🟡 Almost! The correct answer is **{correct_eng}**."
            else:
                feedback_msg = f"❌ Not quite. The correct answer is **{correct_eng}**."

        # Try to get an example phrase from AI (only if correct_eng exists)
        if correct_eng:
            try:
                client = OpenAI(api_key=st.secrets["general"]["OPENAI_API_KEY"])
                sys_prompt = (
                    f"Give a very short, simple German example sentence using the word '{current_word}'. "
                    "Use A1/A2-level vocabulary and no translations."
                )
                completion = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": sys_prompt}]
                )
                example_sentence = completion.choices[0].message.content.strip()
            except Exception:
                example_sentence = ""

        show_feedback = True

    # Show feedback after "Check Answer" (not on rerun)
    if show_feedback or check_clicked:
        st.markdown(f"**Feedback:** {feedback_msg}")
        if example_sentence:
            st.info(f"**Example:** {example_sentence}")
        st.stop()

