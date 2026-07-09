"""
Run once before deployment to clear all test data and seed the real team.
Usage:  python3 scripts/reset_deploy.py
Default password for all accounts: HF@2024
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db, hash_password, init_db

DEFAULT_PASSWORD = "HF@2024"

EMPLOYEES = [
    # (name, username, email_placeholder, role, hourly_rate, shift_type)
    ("Mark Angelo Samson", "mark",     "mark@hundredfold.ph",     "Admin",      0.0,    "Morning"),
    ("Pauline Samson",     "pauline",  "pauline@hundredfold.ph",  "Admin",      0.0,    "Morning"),
    ("Rochelle Llera",     "rochelle", "rochelle@hundredfold.ph", "HR Manager", 0.0,    "Morning"),
    ("Jaztine Canoza",     "jaztine",  "jaztine@hundredfold.ph",  "Employee",   250.0,  "Night"),
    ("Marie Evangelista",  "marie",    "marie@hundredfold.ph",    "Employee",   250.0,  "Night"),
    ("Maricar Evangelista","maricar",  "maricar@hundredfold.ph",  "Employee",   250.0,  "Night"),
    ("Paulyn Angeles",     "paulyn",   "paulyn@hundredfold.ph",   "Employee",   250.0,  "Night"),
    ("Mikaela Angeles",    "mikaela",  "mikaela@hundredfold.ph",  "Employee",   250.0,  "Night"),
    ("CJ Balsicas",        "cj",       "cj@hundredfold.ph",       "Employee",   250.0,  "Night"),
    ("Carlos Rodriguez",   "carlos",   "carlos@hundredfold.ph",   "Employee",   250.0,  "Night"),
    ("Joshua Victoria",    "joshua",   "joshua@hundredfold.ph",   "Employee",   250.0,  "Night"),
    ("Angelo Montesa",     "angelo",   "angelo@hundredfold.ph",   "Employee",   250.0,  "Night"),
    ("Dylan Juco",         "dylan",    "dylan@hundredfold.ph",    "Employee",   250.0,  "Night"),
]

def reset():
    # Ensure tables exist
    init_db()

    with get_db() as conn:
        print("Clearing all data tables…")
        conn.execute("DELETE FROM card_comments")
        conn.execute("DELETE FROM work_logs")
        conn.execute("DELETE FROM attendance")
        conn.execute("DELETE FROM payroll_runs")
        conn.execute("DELETE FROM employees")
        conn.execute("DELETE FROM client_notes")

        # Reset auto-increment sequences
        for tbl in ("employees", "attendance", "work_logs", "card_comments",
                    "payroll_runs", "client_notes"):
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{tbl}'")

        print(f"Inserting {len(EMPLOYEES)} employees (default password: {DEFAULT_PASSWORD})")
        hashed = hash_password(DEFAULT_PASSWORD)
        for name, username, email, role, rate, shift in EMPLOYEES:
            conn.execute(
                """INSERT INTO employees
                   (name, username, email, password, role, hourly_rate, shift_type, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (name, username, email, hashed, role, rate, shift),
            )
            print(f"  + {name:25s}  username={username:10s}  role={role}")

    print("\nDone. All accounts use password:", DEFAULT_PASSWORD)
    print("Log in at http://localhost:8000/login")
    print("IMPORTANT: Change passwords after first login.")

if __name__ == "__main__":
    confirm = input("This will DELETE all employees and data. Type YES to continue: ")
    if confirm.strip() == "YES":
        reset()
    else:
        print("Aborted.")
