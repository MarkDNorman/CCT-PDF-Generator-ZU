"""
Generates a small batch of sample transcripts for manual review.

Picks a mix of heavily-populated and sparse student records, injects
synthetic Semester values (including some intentional blanks) since the
real data doesn't have that column yet, and renders PDFs to a separate
output_samples/ directory so they don't mix with the real bulk output.
"""
import os
import random
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from generate_transcripts import (
    ASSETS_DIR,
    BASE_DIR,
    CURRENT_SEMESTER,
    DATA_PATH,
    EVENTS_DATA_PATH,
    HOST_STATEMENTS_PATH,
    TEMPLATE_DIR,
    find_first_column,
    load_data,
    process_student,
)
from graph_generator import generate_tree_graph

SAMPLE_OUTPUT_DIR = os.path.join(BASE_DIR, "output_samples")
NUM_POPULATED = 5
NUM_SPARSE = 5

SYNTHETIC_SEMESTERS = [
    "Fall 2025",
    "Spring 2026",
    "Summer 2026",
    "Fall 2026",
    "Spring 2027",
    "",  # intentional blank to verify graceful empty handling
]


def pick_sample_ids(df):
    counts = df.groupby("ID").size().sort_values(ascending=False)
    populated_ids = counts.head(NUM_POPULATED).index.tolist()
    sparse_ids = counts.tail(NUM_SPARSE).index.tolist()
    return populated_ids, sparse_ids


def add_synthetic_semesters(df, seed=42):
    """Assigns a semester to every row, cycling with some randomness so
    blanks and repeats both show up across the sample."""
    rng = random.Random(seed)
    df = df.copy()
    df["Semester"] = [rng.choice(SYNTHETIC_SEMESTERS) for _ in range(len(df))]
    return df


def render_pdf(student_data, template_env, output_dir):
    template = template_env.get_template("transcript.html")
    graph_base64 = generate_tree_graph(student_data, return_base64=True, current_semester=CURRENT_SEMESTER)

    context = {
        "student": student_data,
        "assets_path": Path(ASSETS_DIR).resolve().as_posix(),
        "tree_graph_base64": graph_base64,
        "tree_graph_path": "",
    }
    html_out = template.render(**context)

    filepath = os.path.join(output_dir, f"{student_data['id']}.pdf")
    base_url = f"file://{Path(BASE_DIR).resolve().as_posix()}/"
    HTML(string=html_out, base_url=base_url).write_pdf(filepath)
    return filepath


def main():
    os.makedirs(SAMPLE_OUTPUT_DIR, exist_ok=True)

    df = load_data(DATA_PATH)

    id_col = find_first_column(df, ["Student ID", "ID"])
    if id_col is None:
        raise ValueError("Could not find a student ID column in the data (expected 'Student ID' or 'ID').")
    df["ID"] = df[id_col].astype(str).str.strip().str.upper()

    if find_first_column(df, ["Semester"]) is None:
        print("No Semester column found in source data - injecting synthetic values for review.")
        df = add_synthetic_semesters(df)

    events_df = load_data(EVENTS_DATA_PATH) if os.path.exists(EVENTS_DATA_PATH) else None

    host_sentences_map = {}
    if os.path.exists(HOST_STATEMENTS_PATH):
        hosts_df = load_data(HOST_STATEMENTS_PATH)
        for _, row in hosts_df.iterrows():
            if row["Activity Host"] == row["Activity Host"] and row["Sentence"] == row["Sentence"]:
                host_sentences_map[str(row["Activity Host"]).strip()] = str(row["Sentence"]).strip()

    populated_ids, sparse_ids = pick_sample_ids(df)
    print(f"Populated sample IDs ({len(populated_ids)}): {populated_ids}")
    print(f"Sparse sample IDs ({len(sparse_ids)}): {sparse_ids}")

    template_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

    for student_id in populated_ids + sparse_ids:
        student_df = df[df["ID"] == student_id]
        s_data = process_student(student_id, student_df, events_df, host_sentences_map)
        filepath = render_pdf(s_data, template_env, SAMPLE_OUTPUT_DIR)
        print(f"Generated {filepath} ({len(student_df)} source rows)")

    print(f"\nDone. {len(populated_ids) + len(sparse_ids)} sample PDFs in {SAMPLE_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
