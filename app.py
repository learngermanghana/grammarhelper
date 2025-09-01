# app.py
import os
import re
import json
from datetime import datetime
from typing import Dict, Any, List, Tuple

import pandas as pd
import requests
import streamlit as st

# ---------------- OpenAI (optional) ----------------
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY"))
try:
    from openai import OpenAI
    ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    ai_client = None

# ---------------- Firebase ----------------
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    fb_cfg = st.secrets.get("firebase")
    if fb_cfg:
        cred = credentials.Certificate(dict(fb_cfg))
        firebase_admin.initialize_app(cred)
db = firestore.client() if firebase_admin._apps else None

# ---------------- IDs / Config ----------------
# Students Google Sheet (tab now "Sheet1" unless you override in secrets)
STUDENTS_SHEET_ID   = "12NXf5FeVHr7JJT47mRHh7Jp-TC1yhPS7ZG6nzZVTt1U"
STUDENTS_SHEET_TAB  = st.secrets.get("STUDENTS_SHEET_TAB", "Sheet1")

# Reference Google Sheet (answers) and tab name (default Sheet1)
REF_ANSWERS_SHEET_ID = "1CtNlidMfmE836NBh5FmEF5tls9sLmMmkkhewMTQjkBo"
REF_ANSWERS_TAB      = st.secrets.get("REF_ANSWERS_TAB", "Sheet1")

# Apps Script webhook (fallbacks included)
WEBHOOK_URL   = st.secrets.get(
    "G_SHEETS_WEBHOOK_URL",
    "https://script.google.com/macros/s/AKfycbzKWo9IblWZEgD_d7sku6cGzKofis_XQj3NXGMYpf_uRqu9rGe4AvOcB15E3bb2e6O4/exec",
)
WEBHOOK_TOKEN = st.secrets.get("G_SHEETS_WEBHOOK_TOKEN", "Xenomexpress7727/")

# Answers dictionary JSON paths (first existing will be used)
ANSWERS_JSON_PATHS = [
    "answers_dictionary.json",
    "data/answers_dictionary.json",
    "assets/answers_dictionary.json",
]

# Default reference source: "json" or "sheet" (configurable via Streamlit
# secrets or environment variable "ANSWER_SOURCE")
ANSWER_SOURCE = (
    st.secrets.get("ANSWER_SOURCE")
    or os.environ.get("ANSWER_SOURCE", "")
).lower()
if ANSWER_SOURCE not in ("json", "sheet"):
    ANSWER_SOURCE = ""

# Criteria used for rubric-based AI feedback
RUBRIC_CRITERIA = ["overall"]


# =========================================================
# Helpers
# =========================================================
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.findall(r"\d+|\D+", str(s))]

@st.cache_data(show_spinner=False, ttl=300)
def load_sheet_csv(sheet_id: str, tab: str) -> pd.DataFrame:
    """Load a specific Google Sheet tab as CSV (no auth)."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={requests.utils.quote(tab)}"
        "&tq=select%20*%20limit%20100000"
    )
    df = pd.read_csv(url, dtype=str)
    df.columns = df.columns.str.strip().str.lower()
    return df

@st.cache_data(show_spinner=False)
def load_answers_dictionary() -> Dict[str, Any]:
    for p in ANSWERS_JSON_PATHS:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}

def find_col(df: pd.DataFrame, candidates: List[str], default: str = "") -> str:
    norm = {c: c.lower().strip().replace(" ", "").replace("_", "") for c in df.columns}
    want = [c.lower().strip().replace(" ", "").replace("_", "") for c in candidates]
    for raw, n in norm.items():
        if n in want:
            return raw
    if default and default not in df.columns:
        df[default] = ""
        return default
    raise KeyError(f"Missing columns: {candidates}")

def list_sheet_assignments(ref_df: pd.DataFrame, assignment_col: str) -> List[str]:
    vals = ref_df[assignment_col].astype(str).fillna("").str.strip()
    vals = [v for v in vals if v]
    return sorted(vals, key=natural_key)

def ordered_answer_cols(cols: List[str]) -> List[str]:
    pairs = []
    for c in cols:
        if c.lower().startswith("answer"):
            m = re.search(r"(\d+)", c)
            if m: pairs.append((int(m.group(1)), c))
    return [c for _, c in sorted(pairs, key=lambda x: x[0])]

def build_reference_text_from_sheet(
    ref_df: pd.DataFrame, assignment_col: str, assignment_value: str
) -> Tuple[str, str, str, Dict[int, str]]:
    """Return reference text, link, format and raw answers for a sheet row."""
    row = ref_df[ref_df[assignment_col] == assignment_value]
    if row.empty:
        return "No reference answers found.", "", "essay", {}
    row = row.iloc[0]
    ans_cols = ordered_answer_cols(list(ref_df.columns))
    chunks: List[str] = []
    answers_map: Dict[int, str] = {}
    for c in ans_cols:
        v = str(row.get(c, "")).strip()
        if v and v.lower() not in ("nan", "none"):
            m = re.search(r"(\d+)", c)
            n = int(m.group(1)) if m else 0
            chunks.append(f"{n}. {v}")
            answers_map[n] = v
    link = str(row.get("answer_url", "")).strip()  # ignore sheet_url by request
    fmt = str(row.get("format", "essay")).strip().lower() or "essay"
    return (
        "\n".join(chunks) if chunks else "No reference answers found.",
        link,
        fmt,
        answers_map,
    )

def list_json_assignments(ans_dict: Dict[str, Any]) -> List[str]:
    return sorted(list(ans_dict.keys()), key=natural_key)

def build_reference_text_from_json(
    row_obj: Dict[str, Any]
) -> Tuple[str, str, str, Dict[int, str]]:
    """Return reference text, link, format and raw answers from JSON row."""
    answers: Dict[str, str] = row_obj.get("answers") or {
        k: v for k, v in row_obj.items() if k.lower().startswith("answer")
    }

    def n_from(k: str) -> int:
        m = re.search(r"(\d+)", k)
        return int(m.group(1)) if m else 0

    ordered = sorted(answers.items(), key=lambda kv: n_from(kv[0]))
    chunks: List[str] = []
    answers_map: Dict[int, str] = {}
    for k, v in ordered:
        v = str(v).strip()
        if v and v.lower() not in ("nan", "none"):
            idx = n_from(k)
            chunks.append(f"{idx}. {v}")
            answers_map[idx] = v
    fmt = str(row_obj.get("format", "essay")).strip().lower() or "essay"
    return (
        "\n".join(chunks) if chunks else "No reference answers found.",
        str(row_obj.get("answer_url", "")).strip(),
        fmt,
        answers_map,
    )

def filter_any(df: pd.DataFrame, q: str) -> pd.DataFrame:
    if not q: return df
    mask = df.apply(lambda c: c.astype(str).str.contains(q, case=False, na=False))
    return df[mask.any(axis=1)]

def extract_text_from_doc(doc: Dict[str, Any]) -> str:
    preferred = ["content", "text", "answer", "body", "draft", "message"]
    for k in preferred:
        v = doc.get(k)
        if isinstance(v, str) and v.strip(): return v.strip()
        if isinstance(v, list):
            parts = []
            for item in v:
                if isinstance(item, str): parts.append(item)
                elif isinstance(item, dict):
                    for kk in ["text", "content", "value"]:
                        if kk in item and isinstance(item[kk], str): parts.append(item[kk])
            if parts: return "\n".join(parts).strip()
        if isinstance(v, dict):
            for kk in ["text", "content", "value"]:
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip(): return vv.strip()
    strings = [str(v).strip() for v in doc.values() if isinstance(v, str) and str(v).strip()]
    return "\n".join(strings).strip()

def fetch_submissions(student_code: str) -> List[Dict[str, Any]]:
    if not db or not student_code: return []
    items: List[Dict[str, Any]] = []
    def pull(coll: str):
        try:
            for snap in db.collection("drafts_v2").document(student_code).collection(coll).stream():
                d = snap.to_dict() or {}
                d["id"] = snap.id
                items.append(d)
        except Exception:
            pass
    pull("lessons")
    if not items: pull("lessens")
    return items

# ===================== AI MARKING (OBJECTIVES ONLY) =====================
def ai_mark(student_answer: str, ref_text: str, student_level: str) -> Tuple[int | None, str]:
    """
    Uses OpenAI to mark A1-style objective answers ONLY.
    Returns (score [0..100] | None, feedback).
    """
    if not ai_client:
        return None, ""

    system_prompt = """You are a precise but kind German tutor. Grade ONLY objective questions.
There are numbered reference answers (e.g., "1. B", "2. Uhr", "3. Ja"). Student responses may be messy.

OUTPUT
Return ONLY JSON: {"score": <int 0-100>, "feedback": "<30–50 words>"}.
No markdown, no extra keys, no explanations.

TASK
1) Build the reference key:
   - Parse each line like "<number>.<space><answer>" from the reference block into a map {n -> token}.
   - A token can be a letter (A–D) or a short word/phrase.
2) Parse the student's selections into {n -> token}:
   - Accept formats like "1 A", "1: A", "1)A", "Q1=B", "1- a", "1. Uhr", "1) true", or multiple lines.
   - Ignore case, punctuation, extra spaces, and prefixes like "Q".
   - If the student gives multiple tokens for the same number, use the FIRST valid one.
3) Normalize when comparing:
   - For letters, compare A–D case-insensitively.
   - For words, lowercase and strip punctuation.
   - Treat umlauts/ß equivalently to ASCII: ä↔ae, ö↔oe, ü↔ue, ß↔ss.
   - Treat true/false equal to T/F and ja/nein.
4) Score:
   - total = count of reference items.
   - correct = matches after normalization. Unanswered or invalid = wrong.
   - score = round((correct / total) * 100).
5) Feedback (English, 30–50 words):
   - Start with an encouraging verdict for A1 level.
   - List wrong numbers as "2→B (you wrote C), 5→Uhr (you wrote Zeit)".
   - Give ONE concrete tip (e.g., re-check Artikel, read the choices carefully, or use umlauts like 'ö').

CONSTRAINTS
- If the student's answer is blank/unusable, return {"score": 0, "feedback": "No assessable answer provided. Try again with complete responses."}.
- Be slightly lenient if reference lines look inconsistent, but DO NOT invent keys.
"""

    user_prompt = f"""Level: {student_level}

REFERENCE ANSWERS (numbered lines):
{ref_text}

STUDENT ANSWERS (free-form):
{student_answer}
"""

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    try:
        # Prefer the Responses API
        try:
            resp = ai_client.responses.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = getattr(resp, "output_text", None)
            if not raw:
                # Fallback extraction for older SDK shapes
                raw = json.dumps(resp) if isinstance(resp, dict) else ""
        except Exception:
            # Fallback to Chat Completions
            resp = ai_client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = resp.choices[0].message.content

        text = (raw or "").strip()
        # Be safe: extract the first JSON object
        m = re.search(r"\{.*\}", text, flags=re.S)
        text = m.group(0) if m else text
        data = json.loads(text) if isinstance(text, str) else dict(text)

        score = int(data.get("score", 0))
        fb_str = str(data.get("feedback", "")).strip()
        return max(0, min(100, score)), fb_str
    except Exception:
        # If anything goes wrong, don't crash the app — just skip AI marking.
        return None, ""

# ===================== LOCAL MARKING (OBJECTIVES ONLY) =====================
def objective_mark(student_answer: str, ref_answers: Dict[int, str]) -> Tuple[int, str]:
    """
    Robust objective marking without AI.
    - Parses messy "Qn -> answer" formats.
    - Normalizes umlauts/ß to ASCII equivalents for comparison.
    - Accepts synonyms for True/False and Ja/Nein.
    """

    def canonical_word(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        # Letter option? Keep A-D uppercase
        if re.fullmatch(r"[a-dA-D]", s):
            return s.upper()

        # Lowercase words, normalize umlauts to ASCII
        s = s.lower()
        s = (s
             .replace("ä", "ae")
             .replace("ö", "oe")
             .replace("ü", "ue")
             .replace("ß", "ss"))
        # Common boolean/YN synonyms
        if s in {"t", "true", "ja", "j", "y", "yes"}:
            return "true"
        if s in {"f", "false", "nein", "n", "no"}:
            return "false"

        # Remove surrounding punctuation/spaces inside small tokens
        s = re.sub(r"[^\w]+", "", s)
        return s

    def parse_pairs_freeform(text: str) -> Dict[int, str]:
        """
        Parse "1 A", "1: B", "1)C", "Q1=B", "1. Uhr", and also compact streams.
        Strategy: find each question number and capture the token until the next number.
        """
        res: Dict[int, str] = {}
        if not text:
            return res

        # Find all number anchors
        anchors = list(re.finditer(r"(?i)(?:q\s*)?(\d+)\s*[\.\):=\-]*\s*", text))
        for i, m in enumerate(anchors):
            qnum = int(m.group(1))
            start = m.end()
            end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
            chunk = text[start:end].strip()

            if not chunk:
                continue

            # First plausible token within the chunk (letter or short word)
            # Split by whitespace/commas/semicolons/pipes/newlines
            token = re.split(r"[,\|\n;/\t ]+", chunk, maxsplit=1)[0]
            token = token.strip().strip("()[]{}.:=").strip()
            if token and qnum not in res:
                res[qnum] = token
        # Also handle simple per-line "n. token" formats
        for line in text.splitlines():
            m = re.match(r"\s*(?:q\s*)?(\d+)\s*[\.\):=\-]?\s*(.+?)\s*$", line, flags=re.I)
            if m:
                qn = int(m.group(1))
                tok = m.group(2).strip()
                if qn not in res and tok:
                    res[qn] = tok
        return res

    # Build canonical reference map
    ref_canon: Dict[int, str] = {}
    for idx, ans in (ref_answers or {}).items():
        ref_canon[int(idx)] = canonical_word(str(ans))

    # Parse student's freeform text
    stu_raw = parse_pairs_freeform(student_answer or "")
    stu_canon: Dict[int, str] = {qn: canonical_word(tok) for qn, tok in stu_raw.items()}

    total = len(ref_canon) or 1
    correct = 0
    wrong_bits: List[str] = []

    for idx in sorted(ref_canon.keys()):
        ref_tok = ref_canon[idx]
        stu_tok = stu_canon.get(idx, "")

        if stu_tok and re.fullmatch(r"[A-D]", ref_tok):
            # choice letter expected; student may have given letter or word—compare canon
            is_ok = (stu_tok == ref_tok)
        else:
            is_ok = (stu_tok == ref_tok)

        if is_ok:
            correct += 1
        else:
            # For display, keep original (un-canon) when possible
            stu_disp = stu_raw.get(idx, "") or "—"
            wrong_bits.append(f"{idx}→{ref_answers.get(idx, '')} (you wrote {stu_disp})")

    score = int(round(100 * correct / total))
    feedback = "Great job — all correct!" if not wrong_bits else (
        "Keep going. Check these: " + ", ".join(wrong_bits) +
        ". Tip: read each item carefully and watch letters like A/B/C/D and umlauts (ä/ö/ü)."
    )
    return score, feedback

def save_row_to_scores(row: dict) -> dict:
    try:
        r = requests.post(
            WEBHOOK_URL,
            json={"token": WEBHOOK_TOKEN, "row": row},
            timeout=15,
        )

        raw = r.text  # keep a copy for troubleshooting

        # ---------------- Structured JSON ----------------
        if r.headers.get("content-type", "").startswith("application/json"):
            data: Dict[str, Any]
            try:
                data = r.json()
            except Exception:
                data = {}

            if isinstance(data, dict):
                # Apps Script may return structured error information
                field = data.get("field")
                if not data.get("ok") and field:
                    return {
                        "ok": False,
                        "why": "validation",
                        "field": field,
                        "raw": raw,
                    }

                # Ensure raw message is included for debugging
                data.setdefault("raw", raw)
                return data

        # ---------------- Fallback: plain text ----------------
        if "violates the data validation rules" in raw:
            return {"ok": False, "why": "validation", "raw": raw}
        return {"ok": False, "raw": raw}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title="📘 Marking Dashboard", page_icon="📘", layout="wide")
st.title("📘 Marking Dashboard")

if st.button("🔄 Refresh caches"):
    st.cache_data.clear()
    st.rerun()

# --- Load students
students_df = load_sheet_csv(STUDENTS_SHEET_ID, STUDENTS_SHEET_TAB)
code_col  = find_col(students_df, ["studentcode", "student_code", "code"], default="studentcode")
name_col  = find_col(students_df, ["name", "fullname"], default="name")
level_col = find_col(students_df, ["level"], default="level")

# Pick student
st.subheader("1) Pick Student")
q = st.text_input("Search student (code / name / any field)")
df_filtered = filter_any(students_df, q)
if df_filtered.empty:
    st.warning("No students match your search.")
    st.stop()

labels = [f"{r.get(code_col,'')} — {r.get(name_col,'')} ({r.get(level_col,'')})" for _, r in df_filtered.iterrows()]
choice = st.selectbox("Select student", labels)
srow = df_filtered.iloc[labels.index(choice)]
studentcode = str(srow.get(code_col,"")).strip()
student_name = str(srow.get(name_col,"")).strip()
student_level = str(srow.get(level_col,"")).strip()

c1, c2 = st.columns(2)
with c1: st.text_input("Name (auto)",  value=student_name,  disabled=True)
with c2: st.text_input("Level (auto)", value=student_level, disabled=True)

# ---------------- Reference chooser (Tabs) ----------------
st.subheader("2) Reference source")

# Session holder for the *chosen* reference
if "ref_source" not in st.session_state:
    st.session_state.ref_source = ANSWER_SOURCE or None
if "ref_assignment" not in st.session_state:
    st.session_state.ref_assignment = ""
if "ref_text" not in st.session_state:
    st.session_state.ref_text = ""
if "ref_link" not in st.session_state:
    st.session_state.ref_link = ""
if "ref_format" not in st.session_state:
    st.session_state.ref_format = "essay"
if "ref_answers" not in st.session_state:
    st.session_state.ref_answers = {}

tab_titles = ["📦 JSON dictionary", "🔗 Google Sheet"]
if st.session_state.ref_source == "sheet":
    tab_sheet, tab_json = st.tabs(tab_titles[::-1])
else:
    tab_json, tab_sheet = st.tabs(tab_titles)

# ---- JSON tab
with tab_json:
    ans_dict = load_answers_dictionary()
    if not ans_dict:
        st.info("answers_dictionary.json not found in repo.")
    else:
        all_assignments_json = list_json_assignments(ans_dict)
        st.caption(f"{len(all_assignments_json)} assignments in JSON")
        qj = st.text_input("Search assignment (JSON)", key="search_json")
        pool_json = [a for a in all_assignments_json if qj.lower() in a.lower()] if qj else all_assignments_json
        pick_json = st.selectbox("Select assignment (JSON)", pool_json, key="pick_json")
        ref_text_json, link_json, fmt_json, ans_map_json = build_reference_text_from_json(
            ans_dict.get(pick_json, {})
        )
        st.markdown("**Reference preview (JSON):**")
        st.code(ref_text_json or "(none)", language="markdown")
        st.caption(f"Format: {fmt_json}")
        if link_json:
            st.caption(f"Reference link: {link_json}")
        if st.button("✅ Use this JSON reference"):
            st.session_state.ref_source = "json"
            st.session_state.ref_assignment = pick_json
            st.session_state.ref_text = ref_text_json
            st.session_state.ref_link = link_json
            st.session_state.ref_format = fmt_json
            st.session_state.ref_answers = ans_map_json
            st.success("Using JSON reference")

# ---- Sheet tab
with tab_sheet:
    ref_df = load_sheet_csv(REF_ANSWERS_SHEET_ID, REF_ANSWERS_TAB)
    try:
        assign_col = find_col(ref_df, ["assignment"])
    except KeyError:
        st.error("The reference sheet must have an 'assignment' column.")
        assign_col = None
    if assign_col:
        all_assignments_sheet = list_sheet_assignments(ref_df, assign_col)
        st.caption(f"{len(all_assignments_sheet)} assignments in sheet tab “{REF_ANSWERS_TAB}”")
        qs = st.text_input("Search assignment (Sheet)", key="search_sheet")
        pool_sheet = [a for a in all_assignments_sheet if qs.lower() in a.lower()] if qs else all_assignments_sheet
        pick_sheet = st.selectbox("Select assignment (Sheet)", pool_sheet, key="pick_sheet")
        (
            ref_text_sheet,
            link_sheet,
            fmt_sheet,
            ans_map_sheet,
        ) = build_reference_text_from_sheet(ref_df, assign_col, pick_sheet)
        st.markdown("**Reference preview (Sheet):**")
        st.code(ref_text_sheet or "(none)", language="markdown")
        st.caption(f"Format: {fmt_sheet}")
        if link_sheet:
            st.caption(f"Reference link: {link_sheet}")
        if st.button("✅ Use this SHEET reference"):
            st.session_state.ref_source = "sheet"
            st.session_state.ref_assignment = pick_sheet
            st.session_state.ref_text = ref_text_sheet
            st.session_state.ref_link = link_sheet
            st.session_state.ref_format = fmt_sheet
            st.session_state.ref_answers = ans_map_sheet
            st.success("Using Sheet reference")

# Ensure default reference choice based on config/availability
if st.session_state.ref_source == "json" and not load_answers_dictionary():
    st.session_state.ref_source = None
if st.session_state.ref_source == "sheet":
    try:
        find_col(load_sheet_csv(REF_ANSWERS_SHEET_ID, REF_ANSWERS_TAB), ["assignment"])
    except Exception:
        st.session_state.ref_source = None

if not st.session_state.ref_source:
    if load_answers_dictionary():
        st.session_state.ref_source = "json"
    else:
        try:
            find_col(load_sheet_csv(REF_ANSWERS_SHEET_ID, REF_ANSWERS_TAB), ["assignment"])
            st.session_state.ref_source = "sheet"
        except Exception:
            pass

if st.session_state.ref_source == "json" and not st.session_state.ref_assignment:
    ans = load_answers_dictionary()
    if ans:
        first = list_json_assignments(ans)[0]
        txt, ln, fmt, ans_map = build_reference_text_from_json(ans[first])
        st.session_state.ref_assignment = first
        st.session_state.ref_text = txt
        st.session_state.ref_link = ln
        st.session_state.ref_format = fmt
        st.session_state.ref_answers = ans_map
elif st.session_state.ref_source == "sheet" and not st.session_state.ref_assignment:
    ref_df_tmp = load_sheet_csv(REF_ANSWERS_SHEET_ID, REF_ANSWERS_TAB)
    try:
        ac = find_col(ref_df_tmp, ["assignment"])
        first = list_sheet_assignments(ref_df_tmp, ac)[0]
        txt, ln, fmt, ans_map = build_reference_text_from_sheet(ref_df_tmp, ac, first)
        st.session_state.ref_assignment = first
        st.session_state.ref_text = txt
        st.session_state.ref_link = ln
        st.session_state.ref_format = fmt
        st.session_state.ref_answers = ans_map
    except Exception:
        pass

st.info(
    f"Currently using **{st.session_state.ref_source or '—'}** reference → **{st.session_state.ref_assignment or '—'}** (format: {st.session_state.ref_format})"
)

# ---------------- Submissions & Marking ----------------
st.subheader("3) Student submission (Firestore)")
subs = fetch_submissions(studentcode)
if not subs:
    st.warning("No submissions found under drafts_v2/{code}/lessons (or lessens).")
    student_text = ""
else:
    def label_for(i: int, d: Dict[str, Any]) -> str:
        txt = extract_text_from_doc(d)
        preview = (txt[:80] + "…") if len(txt) > 80 else txt
        return f"{i+1} • {d.get('id','(no-id)')} • {preview}"
    labels_sub = [label_for(i, d) for i, d in enumerate(subs)]
    pick = st.selectbox("Pick submission", labels_sub)
    student_text = extract_text_from_doc(subs[labels_sub.index(pick)])

st.markdown("**Student Submission**")
st.code(student_text or "(empty)", language="markdown")

st.markdown("**Reference Answer (chosen)**")
st.code(st.session_state.ref_text or "(not set)", language="markdown")
st.caption(f"Format: {st.session_state.ref_format}")
if st.session_state.ref_link:
    st.caption(f"Reference link: {st.session_state.ref_link}")

# Combined copy block
st.subheader("4) Combined (copyable)")
combined = f"""# Student Submission
{student_text}

# Reference Answer
{st.session_state.ref_text}
"""
st.text_area("Combined", value=combined, height=200)

# AI generate (override allowed)
if "ai_score" not in st.session_state:
    st.session_state.ai_score = 0
if "feedback" not in st.session_state:
    st.session_state.feedback = ""

cur_key = f"{studentcode}|{st.session_state.ref_assignment}|{student_text[:60]}|{st.session_state.ref_format}"
if student_text.strip() and st.session_state.ref_text.strip() and st.session_state.get("ai_key") != cur_key:
    if st.session_state.ref_format == "objective":
        s, fb = objective_mark(student_text, st.session_state.ref_answers)
    elif ai_client:
        s, fb = ai_mark(student_text, st.session_state.ref_text, student_level)
    else:
        s, fb = (None, "")
    if s is not None:
        st.session_state.ai_score = s
    st.session_state.feedback = fb if isinstance(fb, str) else json.dumps(fb, indent=2)
    st.session_state.ai_key = cur_key

colA, colB = st.columns(2)
with colA:
    btn_label = "🔁 Recalculate" if st.session_state.ref_format == "objective" else "🔁 Regenerate AI"
    if st.button(btn_label):
        if st.session_state.ref_format == "objective":
            s, fb = objective_mark(student_text, st.session_state.ref_answers)
        else:
            s, fb = ai_mark(student_text, st.session_state.ref_text, student_level)
        if s is not None:
            st.session_state.ai_score = s
        st.session_state.feedback = fb if isinstance(fb, str) else json.dumps(fb, indent=2)

score = st.number_input("Score", 0, 100, value=int(st.session_state.ai_score))

feedback = st.text_area("Feedback", key="feedback", height=80)

# Save to Scores
st.subheader("5) Save to Scores sheet")
if st.button("💾 Save", type="primary", use_container_width=True):
    if not studentcode:
        st.error("Pick a student first.")
    elif not st.session_state.ref_assignment:
        st.error("Pick a reference (JSON or Sheet) and click its 'Use this … reference' button.")
    elif not feedback.strip():
        st.error("Feedback is required.")
    else:
        try:
            studentcode_val = int(studentcode)
        except ValueError:
            studentcode_val = studentcode

        row = {
            "studentcode": studentcode_val,
            "name":        student_name,
            "assignment":  st.session_state.ref_assignment,
            "score":       int(score),
            "comments":    feedback.strip(),
            "date":        datetime.now().strftime("%Y-%m-%d"),
            "level":       student_level,
            "link":        st.session_state.ref_link,  # uses answer_url only
        }

        result = save_row_to_scores(row)
        if result.get("ok"):
            st.success("✅ Saved to Scores sheet.")
        elif result.get("why") == "validation":
            field = result.get("field")
            if field:
                st.error(f"❌ Sheet blocked the write due to data validation ({field}).")
            else:
                st.error("❌ Sheet blocked the write due to data validation.")
                if result.get("raw"):
                    st.caption(result["raw"])
        else:
            st.error(f"❌ Failed to save: {result}")
