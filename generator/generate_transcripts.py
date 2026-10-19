import pandas as pd
from jinja2 import Environment, FileSystemLoader
import os
import sys
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from graph_generator import generate_tree_graph, filter_entries_for_semester
from weasyprint import HTML
import argparse
import multiprocessing

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "../Data/CTT Data Set - Master .csv")
EVENTS_DATA_PATH = os.path.join(BASE_DIR, "../Data/Unit Data  - Cleaned Events.csv")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
HOST_STATEMENTS_PATH = os.path.join(BASE_DIR, "../Data/Unit Data  - Activity Host Statements (1).csv")

# The capability treemap on the overview page should only reflect capabilities
# earned during the semester this transcript run is being issued for. Update
# this each time transcripts are generated for a new semester.
CURRENT_SEMESTER = "Spring 2026"

# Global Jinja2 Environment for Workers
template_env = None

def init_worker(template_dir):
    """
    Initializer for multiprocessing workers to load the Jinja2 environment once.
    """
    global template_env
    template_env = Environment(loader=FileSystemLoader(template_dir))

def load_data(file_path):
    print(f"Loading data from {file_path}...")
    try:
        df = pd.read_csv(file_path, encoding='utf-8') # Try utf-8 first
    except UnicodeDecodeError:
        df = pd.read_csv(file_path, encoding='latin1') # Fallback
    
    # normalize column names
    df.columns = [c.strip() for c in df.columns]
    return df

def get_level_score(level_str):
    if not isinstance(level_str, str):
        return 0
    level_str = level_str.strip().lower()
    if "developing" in level_str: return 1
    if "achieving" in level_str: return 2
    if "excelling" in level_str: return 3
    if "mastering" in level_str: return 3
    return 0

def get_level_label(score):
    if score == 1: return "Developing"
    if score == 2: return "Achieving"
    if score >= 3: return "Excelling"
    return ""

def find_column(df, target_name):
    """
    Case-insensitive lookup for a column name (columns are already
    whitespace-stripped by load_data). Returns None if not present,
    so callers can degrade gracefully when a column hasn't been added yet.
    """
    target_name = target_name.strip().lower()
    for col in df.columns:
        if col.strip().lower() == target_name:
            return col
    return None

def find_first_column(df, candidate_names):
    """
    Tries a list of candidate column names in order and returns the first
    match found (case-insensitive). Useful because the source export's
    column names have changed between data drops (e.g. 'ID' -> 'Student ID',
    'Transcript statment' -> 'Transcript Entry', 'Host' -> 'Unit').
    """
    for name in candidate_names:
        col = find_column(df, name)
        if col:
            return col
    return None

def process_student(student_id, student_df, events_df=None, host_sentences_map={}, current_semester=CURRENT_SEMESTER):
    first_row = student_df.iloc[0]
    name = first_row.get("Student Name", "Unknown")

    # Capabilities grouping
    capabilities = []
    cap_map = {} # Helper to access capability dict by name

    # Filter only relevant columns
    # Assuming 'Capability' and 'Transcript statment' exist
    cap_groups = student_df.groupby("Capability")

    semester_col = find_column(student_df, "Semester")
    text_col = find_first_column(student_df, ["Transcript Entry", "Transcript statment"])

    for cap_name, cap_rows in cap_groups:
        # Determine level for this capability
        # Logic: Max level of any activity in this capability
        max_score = 0
        for _, row in cap_rows.iterrows():
            lvl = row.get("Level", "")
            score = get_level_score(lvl)
            if score > max_score:
                max_score = score

        entries = []
        for _, row in cap_rows.iterrows():
            text = row.get(text_col, "") if text_col else ""
            # Ensure text is clean string
            if pd.isna(text): continue

            # Semester is optional data; leave blank until the column exists/is filled in
            semester = ""
            if semester_col:
                raw_semester = row.get(semester_col)
                if pd.notna(raw_semester):
                    semester = str(raw_semester).strip()

            entries.append({"text": text, "hidden": False, "semester": semester})
            
        cap_entry = {
            "name": cap_name,
            "level_score": max_score,
            "level_label": get_level_label(max_score),
            "entries": entries
        }
        capabilities.append(cap_entry)
        cap_map[cap_name] = cap_entry
    
    # --- Integrate Cleaned Events Data ---
    # Matched case-insensitively since the same student's ID casing is
    # inconsistent between the master data and the events export.
    additional_events_count = 0
    student_events = None
    if events_df is not None and 'Email address' in events_df.columns:
        student_events = events_df[
            events_df['Email address'].astype(str).str.strip().str.upper() == str(student_id).upper()
        ]
        additional_events_count = len(student_events)

        for _, event_row in student_events.iterrows():
            evt_cap = event_row.get('Event Capability', '')
            if pd.isna(evt_cap) or not isinstance(evt_cap, str) or not evt_cap.strip():
                continue

            evt_cap = evt_cap.strip()
            evt_name = event_row.get('Event name', 'Event')

            # Logic: Add to existing capability or create new
            if evt_cap in cap_map:
                # Add hidden entry
                cap_map[evt_cap]['entries'].append({"text": evt_name, "hidden": True})
            else:
                # New capability from event only
                new_cap = {
                    "name": evt_cap,
                    "level_score": 0, # Default to 0 so no dots appear
                    "level_label": "",
                    "entries": [{"text": evt_name, "hidden": True}]
                }
                capabilities.append(new_cap)
                cap_map[evt_cap] = new_cap

    # --- Flagship Events Logic ---
    attended_national_day = True
    attended_career_fair = True
    attended_campus_connect = False

    if student_events is not None:
        campus_connect_title = "UZU Campus Connect - Clubs & Student Groups Fair!"
        if any(student_events['Event name'] == campus_connect_title):
            attended_campus_connect = True

    flagship_count = 0
    if attended_national_day: flagship_count += 1
    if attended_career_fair: flagship_count += 1
    if attended_campus_connect: flagship_count += 1

    # --- Most Engaged Unit Logic ---
    top_host_name = "Student Life" # Default
    top_host_sentence = "Great way to stay close to the community." # Default

    host_col = find_first_column(student_df, ["Unit", "Host"])
    if host_col and not student_df.empty:
        valid_hosts = student_df[host_col].dropna().astype(str).str.strip()
        valid_hosts = valid_hosts[~valid_hosts.isin(["#N/A", "nan", "None", ""])]
        
        host_counts = valid_hosts.value_counts()
        
        if not host_counts.empty:
            top_host = host_counts.idxmax()
            top_host_name = top_host
            
            # Retrieve sentence from loaded map
            if top_host in host_sentences_map:
                top_host_sentence = host_sentences_map[top_host]
            else:
                for key, val in host_sentences_map.items():
                    if key.lower() == top_host.lower():
                        top_host_sentence = val
                        break
    
    # Overview page stats (activity count, capability count, treemap) all need
    # to agree with each other: capability-earned entries scoped to the
    # current semester, plus every hidden event entry that actually landed
    # under a capability (events with no Event Capability never do, so they
    # don't count here even though they're in additional_events_count above).
    semester_activities = 0
    semester_capabilities = 0
    for cap in capabilities:
        filtered = filter_entries_for_semester(cap.get('entries', []), current_semester)
        if filtered:
            semester_capabilities += 1
        semester_activities += len(filtered)

    return {
        "id": student_id,
        "name": name,
        "total_activities": semester_activities,
        "unique_capabilities": semester_capabilities,
        "total_hours": 0, # Placeholder
        "capabilities": capabilities,
        "flagship_events_joined": flagship_count,
        "flagship_events_total": 3,
        "attended_national_day": attended_national_day,
        "attended_career_fair": attended_career_fair,
        "attended_campus_connect": attended_campus_connect,
        "top_host_name": top_host_name,
        "top_host_sentence": top_host_sentence
    }

def generate_pdf_worker(student_data):
    """
    Worker function to generate PDF for a single student.
    Uses the global 'template_env' initialized by init_worker.
    """
    global template_env
    if template_env is None:
        raise RuntimeError("Template environment not initialized in worker.")

    template = template_env.get_template("transcript.html")
    
    # Generate Tree Graph in Memory (Base64)
    # Using return_base64=True to avoid disk I/O
    # Restricted to capabilities earned (not events) in CURRENT_SEMESTER only.
    graph_base64 = generate_tree_graph(student_data, return_base64=True, current_semester=CURRENT_SEMESTER)
    
    # Prepare template context
    context = {
        "student": student_data,
        "assets_path": Path(ASSETS_DIR).resolve().as_posix(),
        "tree_graph_base64": graph_base64,
        "tree_graph_path": "" # Fallback unused if base64 works
    }

    # Render template
    html_out = template.render(**context)

    # Save PDF
    filename = f"{student_data['id']}.pdf"
    filepath = os.path.join(OUTPUT_DIR, filename)

    # Base URL for resolving any relative paths
    # as_posix() keeps forward slashes so this file:// URL is valid on Windows too
    base_url = f"file://{Path(BASE_DIR).resolve().as_posix()}/"
    
    HTML(string=html_out, base_url=base_url).write_pdf(filepath)
    return filepath

def main():
    parser = argparse.ArgumentParser(description="Generate Uniquely ZU Transcripts")
    parser.add_argument("--test", action="store_true", help="Run only for test students")
    args = parser.parse_args()

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    df = load_data(DATA_PATH)
    
    # Load Events Data
    if os.path.exists(EVENTS_DATA_PATH):
        events_df = load_data(EVENTS_DATA_PATH)
    else:
        print(f"Warning: Events data file not found at {EVENTS_DATA_PATH}")
        events_df = None

    # Load Host Statements
    host_sentences_map = {}
    if os.path.exists(HOST_STATEMENTS_PATH):
        try:
            hosts_df = load_data(HOST_STATEMENTS_PATH)
            for _, row in hosts_df.iterrows():
                if pd.notna(row['Activity Host']) and pd.notna(row['Sentence']):
                    key = str(row['Activity Host']).strip()
                    val = str(row['Sentence']).strip()
                    host_sentences_map[key] = val
        except Exception as e:
            print(f"Error loading host statements: {e}")
    
    # Clean ID column (source export has used both 'ID' and 'Student ID').
    # Normalize to uppercase: the same student sometimes appears with
    # inconsistent ID casing (e.g. "M80009119" vs "m80009119") across rows,
    # which would otherwise split one student into two partial records and,
    # on case-insensitive filesystems (default on macOS), silently overwrite
    # one PDF with the other.
    id_col = find_first_column(df, ["Student ID", "ID"])
    if id_col is None:
        raise ValueError("Could not find a student ID column in the data (expected 'Student ID' or 'ID').")
    df['ID'] = df[id_col].astype(str).str.strip().str.upper()
    
    if args.test:
        test_ids = ["201409999","M80009119","202504746","202323789","202230805","201815138","202324353","202522507","202323789"]
        unique_ids = [uid for uid in test_ids if uid in df['ID'].unique()]
        print(f"TEST MODE: Found {len(unique_ids)} matching test students out of {len(test_ids)} requested.")
    else:
        unique_ids = df['ID'].unique()
        print(f"BULK MODE: Found {len(unique_ids)} unique students to process.")
    
    # Pre-calculate student data for all targeted students
    # processing student data is relatively fast compared to PDF generation
    # so we can do it in the main thread or chunk it. For simplicity, main thread here.
    # Note: If passing huge lists to pool.map, memory might spike. 
    # But 10,000 dicts is manageable given likely student record size.
    
    print("Preparing student data...")
    all_student_data = []
    
    for student_id in unique_ids:
        try:
            student_df = df[df['ID'] == student_id]
            s_data = process_student(student_id, student_df, events_df, host_sentences_map)
            all_student_data.append(s_data)
        except Exception as e:
            print(f"Error preparing data for {student_id}: {e}")

    print(f"Starting PDF generation for {len(all_student_data)} transcripts using {multiprocessing.cpu_count()} cores...")
    
    # Use multiprocessing Pool
    with multiprocessing.Pool(processes=multiprocessing.cpu_count(), initializer=init_worker, initargs=(TEMPLATE_DIR,)) as pool:
        # map returns results in order
        results = pool.map(generate_pdf_worker, all_student_data)
        
    print(f"Parallel generation complete. Initial count: {len(results)}")

    # --- Verification & Retry Logic ---
    print("\n--- Verifying Output ---")
    
    # 1. Check what actually exists on disk
    if not os.path.exists(OUTPUT_DIR):
        print("Error: Output directory missing completely!")
        return

    generated_files = set(os.listdir(OUTPUT_DIR))
    missing_data = [] # List of student_data dicts to retry
    
    for s_data in all_student_data:
        expected_filename = f"{s_data['id']}.pdf"
        if expected_filename not in generated_files:
            missing_data.append(s_data)
    
    if missing_data:
        print(f"WARNING: Found {len(missing_data)} missing PDFs after parallel processing.")
        print("Starting serial retry for missing files...")
        
        # Re-init worker context for main thread if needed, though main thread uses global scope differently.
        # However, generate_pdf_worker uses 'template_env' global.
        # We need to ensure 'template_env' is set in the main process effectively.
        if template_env is None:
             init_worker(TEMPLATE_DIR)
        
        recovered_count = 0
        for s_data in missing_data:
            try:
                print(f"Retrying {s_data['id']}...", end="")
                generate_pdf_worker(s_data)
                print(" OK")
                recovered_count += 1
            except Exception as e:
                print(f" FAILED: {e}")
        
        print(f"Retry complete. Recovered {recovered_count}/{len(missing_data)} files.")
    else:
        print("Verification successful: No missing files found.")

    final_count = len([f for f in os.listdir(OUTPUT_DIR) if f.endswith(".pdf")])
    print(f"Done. Final PDF count: {final_count}/{len(all_student_data)}")

if __name__ == "__main__":
    multiprocessing.freeze_support() # Good practice for some OSs
    main()
