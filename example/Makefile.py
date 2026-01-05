"""Data processing pipeline with pymake.

Run with: pymake
List tasks: pymake list
"""

from pathlib import Path

from pymake import sh, task

# Configuration
OUTPUT_DIR = Path("output")
DATA_DIR = Path("data")

# Output files
RAW_DATA = OUTPUT_DIR / "raw.json"
PROCESSED = OUTPUT_DIR / "processed.json"
STATS = OUTPUT_DIR / "stats.json"
REPORT = OUTPUT_DIR / "report.html"
DATABASE = OUTPUT_DIR / "data.db"


# Task with outputs only: runs if output is missing
@task(outputs=[RAW_DATA])
def fetch():
    """Download raw data from API."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sh(f"echo '{{\"data\": []}}' > {RAW_DATA}")


# Multiple outputs: both files are produced together
@task(inputs=[RAW_DATA], outputs=[PROCESSED, STATS])
def process():
    """Transform raw data and compute statistics."""
    sh(f"echo '{{\"processed\": true}}' > {PROCESSED}")
    sh(f"echo '{{\"count\": 0}}' > {STATS}")


# Depend on one output: still runs process, which produces both PROCESSED and STATS
@task(inputs=[PROCESSED], outputs=[DATABASE])
def load_db():
    """Load processed data into SQLite database."""
    sh(f"touch {DATABASE}")


# Mix file and task inputs: STATS is a file, load_db is a task
@task(inputs=[STATS, load_db], outputs=[REPORT])
def report():
    """Generate HTML report with statistics."""
    sh(f"echo '<html>report</html>' > {REPORT}")


# Meta task: no body, just ensures dependencies run
@task(inputs=[report])
def pipeline():
    """Run full pipeline: fetch → process → load → report."""
    pass


# Phony task: no outputs, so it always runs when invoked
@task()
def lint():
    """Run code linting."""
    sh("echo 'linting...'")


@task()
def test():
    """Run tests."""
    sh("echo 'testing...'")


@task(inputs=[lint, test])
def check():
    """Run all checks (lint + test)."""
    pass


@task()
def clean():
    """Remove all generated files."""
    sh(f"rm -rf {OUTPUT_DIR}")


# Default task: runs when pymake is invoked without arguments
task.default("pipeline")
