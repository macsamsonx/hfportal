import sqlite3
import os
import bcrypt
from contextlib import contextmanager
from datetime import datetime, timedelta, date, timezone

DB_PATH = os.path.abspath("enterprise_ledger.db")
UPLOAD_DIR = os.path.abspath("secure_vault")

# Philippine Time = UTC+8
PHT = timezone(timedelta(hours=8))

KANBAN_STATUSES = ["Todo", "In Progress", "For Review", "Done"]
CLIENT_LIST = ["Byron", "Pej", "CHD", "Waren Digital", "Syllabi", "MBQ", "Internal", "Other"]

# Philippine government contribution tables (2024 rates)
# SSS: employee share based on MSC brackets — simplified flat % for now
SSS_EMPLOYEE_RATE   = 0.045   # 4.5% of monthly salary credit
PHILHEALTH_RATE     = 0.025   # 2.5% of basic salary (employee share)
PAGIBIG_RATE        = 0.02    # 2% of monthly compensation (employee share, max ₱100)
PAGIBIG_MAX         = 100.0

OT_MULTIPLIER_REGULAR = 1.0   # Standard OT rate (same as regular hourly rate)

BIBLE_VERSES = [
    {"verse": "Commit your work to the Lord, and your plans will be established.", "ref": "Proverbs 16:3"},
    {"verse": "Whatever you do, work heartily, as for the Lord and not for men.", "ref": "Colossians 3:23"},
    {"verse": "Let the favor of the Lord our God be upon us, and establish the work of our hands.", "ref": "Psalm 90:17"},
    {"verse": "I can do all things through him who strengthens me.", "ref": "Philippians 4:13"},
    {"verse": "The Lord will open to you his good treasury to bless all the work of your hands.", "ref": "Deuteronomy 28:12"},
    {"verse": "Blessed is everyone who fears the Lord, who walks in his ways!", "ref": "Psalm 128:1"},
    {"verse": "Do not be slothful in zeal, be fervent in spirit, serve the Lord.", "ref": "Romans 12:11"},
    {"verse": "Trust in the Lord with all your heart, and do not lean on your own understanding.", "ref": "Proverbs 3:5"},
    {"verse": "For we are God's handiwork, created in Christ Jesus to do good works.", "ref": "Ephesians 2:10"},
    {"verse": "The plans of the diligent lead surely to abundance.", "ref": "Proverbs 21:5"},
    {"verse": "But seek first the kingdom of God and his righteousness, and all these things will be added to you.", "ref": "Matthew 6:33"},
    {"verse": "For I know the plans I have for you, declares the Lord, plans for welfare and not for evil, to give you a future and a hope.", "ref": "Jeremiah 29:11"},
    {"verse": "The Lord is my shepherd; I shall not want.", "ref": "Psalm 23:1"},
    {"verse": "Be strong and courageous. Do not be afraid; do not be discouraged, for the Lord your God will be with you wherever you go.", "ref": "Joshua 1:9"},
    {"verse": "And my God will supply every need of yours according to his riches in glory in Christ Jesus.", "ref": "Philippians 4:19"},
    {"verse": "Cast your burden on the Lord, and he will sustain you; he will never permit the righteous to be moved.", "ref": "Psalm 55:22"},
    {"verse": "The Lord bless you and keep you; the Lord make his face shine on you and be gracious to you.", "ref": "Numbers 6:24-25"},
    {"verse": "Come to me, all who labor and are heavy laden, and I will give you rest.", "ref": "Matthew 11:28"},
    {"verse": "He gives power to the faint, and to him who has no might he increases strength.", "ref": "Isaiah 40:29"},
    {"verse": "No weapon formed against you shall prosper.", "ref": "Isaiah 54:17"},
    {"verse": "For God gave us a spirit not of fear but of power and love and self-control.", "ref": "2 Timothy 1:7"},
    {"verse": "Delight yourself in the Lord, and he will give you the desires of your heart.", "ref": "Psalm 37:4"},
    {"verse": "The Lord is my light and my salvation; whom shall I fear?", "ref": "Psalm 27:1"},
    {"verse": "If you abide in me, and my words abide in you, ask whatever you wish, and it will be done for you.", "ref": "John 15:7"},
    {"verse": "Blessed are the pure in heart, for they shall see God.", "ref": "Matthew 5:8"},
    {"verse": "The name of the Lord is a strong tower; the righteous man runs into it and is safe.", "ref": "Proverbs 18:10"},
    {"verse": "Do not be anxious about anything, but in everything by prayer and supplication with thanksgiving let your requests be made known to God.", "ref": "Philippians 4:6"},
    {"verse": "And the peace of God, which surpasses all understanding, will guard your hearts and your minds in Christ Jesus.", "ref": "Philippians 4:7"},
    {"verse": "I will instruct you and teach you in the way you should go; I will counsel you with my eye upon you.", "ref": "Psalm 32:8"},
    {"verse": "Have I not commanded you? Be strong and courageous.", "ref": "Joshua 1:9"},
    {"verse": "For everyone who asks receives, and the one who seeks finds, and to the one who knocks it will be opened.", "ref": "Matthew 7:8"},
    {"verse": "He who began a good work in you will bring it to completion at the day of Jesus Christ.", "ref": "Philippians 1:6"},
    {"verse": "The Lord is near to all who call on him, to all who call on him in truth.", "ref": "Psalm 145:18"},
    {"verse": "But those who hope in the Lord will renew their strength. They will soar on wings like eagles.", "ref": "Isaiah 40:31"},
    {"verse": "When you pass through the waters, I will be with you; and through the rivers, they shall not overwhelm you.", "ref": "Isaiah 43:2"},
    {"verse": "Fear not, for I am with you; be not dismayed, for I am your God; I will strengthen you.", "ref": "Isaiah 41:10"},
    {"verse": "The righteous person may have many troubles, but the Lord delivers him from them all.", "ref": "Psalm 34:19"},
    {"verse": "Give, and it will be given to you. Good measure, pressed down, shaken together, running over.", "ref": "Luke 6:38"},
    {"verse": "With God all things are possible.", "ref": "Matthew 19:26"},
    {"verse": "Ask, and it will be given to you; seek, and you will find; knock, and it will be opened to you.", "ref": "Matthew 7:7"},
    {"verse": "The Lord is faithful, and he will strengthen you and protect you from the evil one.", "ref": "2 Thessalonians 3:3"},
    {"verse": "For with God nothing will be impossible.", "ref": "Luke 1:37"},
    {"verse": "Beloved, I pray that all may go well with you and that you may be in good health, as it goes well with your soul.", "ref": "3 John 1:2"},
    {"verse": "The steps of a man are established by the Lord, when he delights in his way.", "ref": "Psalm 37:23"},
    {"verse": "Every good gift and every perfect gift is from above, coming down from the Father of lights.", "ref": "James 1:17"},
    {"verse": "And we know that for those who love God all things work together for good.", "ref": "Romans 8:28"},
    {"verse": "But seek first the kingdom of God and his righteousness, and all these things will be added to you.", "ref": "Matthew 6:33"},
    {"verse": "The blessing of the Lord makes rich, and he adds no sorrow with it.", "ref": "Proverbs 10:22"},
    {"verse": "A generous person will prosper; whoever refreshes others will be refreshed.", "ref": "Proverbs 11:25"},
    {"verse": "Bring the whole tithe into the storehouse... and see if I will not throw open the floodgates of heaven and pour out so much blessing.", "ref": "Malachi 3:10"},
    {"verse": "For the Lord God is a sun and shield; the Lord bestows favor and honor. No good thing does he withhold from those who walk uprightly.", "ref": "Psalm 84:11"},
    {"verse": "Peace I leave with you; my peace I give to you. Not as the world gives do I give to you.", "ref": "John 14:27"},
    {"verse": "In all your ways acknowledge him, and he will make straight your paths.", "ref": "Proverbs 3:6"},
    {"verse": "The Lord will fight for you; you need only to be still.", "ref": "Exodus 14:14"},
    {"verse": "My grace is sufficient for you, for my power is made perfect in weakness.", "ref": "2 Corinthians 12:9"},
]


def get_pht_now() -> datetime:
    return datetime.now(PHT).replace(tzinfo=None)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed.startswith("$2"):
        return False  # plaintext passwords are migrated on startup; reject any that remain
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_DIR, "avatars"), exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_DIR, "docs"), exist_ok=True)
    os.makedirs(os.path.join(UPLOAD_DIR, "posters"), exist_ok=True)

    with get_db() as conn:
        conn.executescript("""
            -- ── Core tables ──────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                username TEXT UNIQUE,
                email TEXT,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Employee',
                hourly_rate REAL DEFAULT 0.0,
                shift_type TEXT DEFAULT 'Morning',
                capabilities TEXT,
                profile_pic_path TEXT,
                phone TEXT,
                address TEXT,
                birthday TEXT,
                marital_status TEXT,
                gender TEXT,
                emergency_contact TEXT,
                emergency_phone TEXT,
                -- Philippine government IDs
                sss_no TEXT,
                philhealth_no TEXT,
                tin_no TEXT,
                pagibig_no TEXT,
                -- Bank for payroll transfer
                bank_name TEXT,
                bank_account TEXT,
                -- Government deduction enrollment (1=enrolled, 0=not enrolled)
                sss_enrolled INTEGER DEFAULT 1,
                philhealth_enrolled INTEGER DEFAULT 1,
                pagibig_enrolled INTEGER DEFAULT 1,
                tax_enrolled INTEGER DEFAULT 1,
                -- Documents
                doc_resume TEXT,
                doc_nbi TEXT,
                doc_sss TEXT,
                doc_tin TEXT,
                doc_philhealth TEXT,
                doc_pagibig TEXT,
                -- HR notes
                admin_notes TEXT,
                hr_feedback TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now', '+8 hours'))
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                clock_in TEXT,
                clock_out TEXT,
                date_logged TEXT NOT NULL,
                is_on_break INTEGER DEFAULT 0,
                late_flag INTEGER DEFAULT 0,
                ip_address TEXT,
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS attendance_breaks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                att_id INTEGER NOT NULL,
                break_start TEXT NOT NULL,
                break_end TEXT,
                FOREIGN KEY(att_id) REFERENCES attendance(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS work_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                client TEXT,
                task_title TEXT NOT NULL,
                hours_worked REAL DEFAULT 0,
                notes TEXT,
                file_path TEXT,
                output_files TEXT,
                status TEXT DEFAULT 'Todo',
                priority TEXT DEFAULT 'Medium',
                due_date TEXT,
                hr_reviewed_by TEXT,
                admin_approved_by TEXT,
                reviewer_name TEXT,
                started_at TEXT,
                is_running INTEGER DEFAULT 0,
                timestamp TEXT DEFAULT (datetime('now', '+8 hours')),
                date_logged TEXT DEFAULT (date('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            -- ── OT requests ───────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS overtime_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                ot_date TEXT NOT NULL,
                ot_start TEXT NOT NULL,
                ot_end TEXT NOT NULL,
                ot_type TEXT DEFAULT 'Regular',
                reason TEXT,
                status TEXT DEFAULT 'Pending',
                approved_by TEXT,
                approved_at TEXT,
                denied_reason TEXT,
                hours_computed REAL DEFAULT 0,
                filed_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            -- ── Leave requests ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS leave_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                leave_type TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                days_count REAL DEFAULT 1,
                reason TEXT,
                status TEXT DEFAULT 'Pending',
                approved_by TEXT,
                approved_at TEXT,
                denied_reason TEXT,
                filed_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            -- ── Announcements ─────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                posted_by INTEGER NOT NULL,
                posted_by_name TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                is_pinned INTEGER DEFAULT 0,
                audience TEXT DEFAULT 'All',
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(posted_by) REFERENCES employees(id)
            );

            -- ── Notifications ─────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                link TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(user_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            -- ── Audit log ─────────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                user_name TEXT,
                action TEXT NOT NULL,
                target_table TEXT,
                target_id INTEGER,
                old_value TEXT,
                new_value TEXT,
                ip_address TEXT,
                created_at TEXT DEFAULT (datetime('now', '+8 hours'))
            );

            -- ── Login history ─────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS login_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                success INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );

            -- ── TV poster loop ────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS tv_posters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                caption TEXT,
                display_order INTEGER DEFAULT 0,
                duration_secs INTEGER DEFAULT 8,
                is_active INTEGER DEFAULT 1,
                uploaded_by INTEGER,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(uploaded_by) REFERENCES employees(id)
            );

            -- ── Chat (group channel + DMs) ───────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                sender_id INTEGER NOT NULL,
                sender_name TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(sender_id) REFERENCES employees(id)
            );

            CREATE TABLE IF NOT EXISTS chat_reads (
                user_id INTEGER NOT NULL,
                room TEXT NOT NULL,
                last_read_msg_id INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, room)
            );

            -- ── Payroll runs ──────────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS payroll_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                emp_id INTEGER NOT NULL,
                emp_name TEXT,
                regular_hours REAL DEFAULT 0,
                overtime_hours REAL DEFAULT 0,
                hourly_rate REAL DEFAULT 0,
                regular_pay REAL DEFAULT 0,
                overtime_pay REAL DEFAULT 0,
                gross_pay REAL DEFAULT 0,
                sss_deduction REAL DEFAULT 0,
                philhealth_deduction REAL DEFAULT 0,
                pagibig_deduction REAL DEFAULT 0,
                tax_deduction REAL DEFAULT 0,
                total_deductions REAL DEFAULT 0,
                net_pay REAL DEFAULT 0,
                total_pay REAL DEFAULT 0,
                status TEXT DEFAULT 'Pending',
                hr_approved_by TEXT,
                hr_approved_at TEXT,
                approved_by TEXT,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id)
            );

            -- ── Clients & notes ───────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                hex_color TEXT DEFAULT '#3b82f6',
                monthly_retainer REAL DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now', '+8 hours'))
            );

            CREATE TABLE IF NOT EXISTS client_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                author_name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS card_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                timestamp TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(card_id) REFERENCES work_logs(id) ON DELETE CASCADE
            );

            -- ── Card activity timeline ────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS card_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                actor_name TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                detail TEXT,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(card_id) REFERENCES work_logs(id) ON DELETE CASCADE
            );

            -- ── Timesheet submissions ─────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS timesheet_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                status TEXT DEFAULT 'Draft',
                submitted_at TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                hr_notes TEXT,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE,
                UNIQUE(emp_id, week_start)
            );

            CREATE TABLE IF NOT EXISTS timesheet_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                time_in TEXT,
                time_out TEXT,
                computed_hours REAL DEFAULT 0,
                manual_hours REAL DEFAULT 0,
                ot_hours REAL DEFAULT 0,
                ot_approved INTEGER DEFAULT 0,
                leave_hours REAL DEFAULT 0,
                leave_type TEXT,
                total_hours REAL DEFAULT 0,
                FOREIGN KEY(submission_id) REFERENCES timesheet_submissions(id) ON DELETE CASCADE
            );

            -- ── Shift Schedules ──────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                mon TEXT DEFAULT 'Morning',
                tue TEXT DEFAULT 'Morning',
                wed TEXT DEFAULT 'Morning',
                thu TEXT DEFAULT 'Morning',
                fri TEXT DEFAULT 'Morning',
                sat TEXT DEFAULT 'Off',
                sun TEXT DEFAULT 'Off',
                notes TEXT,
                created_by INTEGER,
                updated_at TEXT DEFAULT (datetime('now', '+8 hours')),
                UNIQUE(emp_id, week_start),
                FOREIGN KEY(emp_id) REFERENCES employees(id)
            );

            -- ── Registration requests (pre-employment applications) ────────────────────
            CREATE TABLE IF NOT EXISTS registration_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                gender TEXT,
                birthday TEXT,
                address TEXT,
                position_applied TEXT,
                message TEXT,
                privacy_agreed INTEGER DEFAULT 0,
                terms_agreed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'Pending',
                reviewed_by TEXT,
                reviewed_at TEXT,
                rejection_reason TEXT,
                employee_id INTEGER,
                created_at TEXT DEFAULT (datetime('now', '+8 hours'))
            );

            -- ── Performance Reviews ───────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS performance_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                period TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                self_rating INTEGER,
                self_comments TEXT,
                self_submitted_at TEXT,
                hr_rating INTEGER,
                hr_comments TEXT,
                hr_reviewed_by TEXT,
                hr_reviewed_at TEXT,
                status TEXT DEFAULT 'Pending Self-Review',
                created_by INTEGER,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(emp_id) REFERENCES employees(id)
            );
        """)

        # ── Seed employees ────────────────────────────────────────────────────────
        count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        if count == 0:
            seeds = [
                ("Alex Mercer",    "alex@company.com",  hash_password("password123"), "Employee",   250.0, "Morning", "Automation Specialist"),
                ("Sarah Connor",   "sarah@company.com", hash_password("password123"), "Employee",   300.0, "Night",   "UI Designer"),
                ("Maria Santos",   "maria@company.com", hash_password("password123"), "Employee",   275.0, "Morning", "Content Writer"),
                ("HR Manager",     "hr@company.com",    hash_password("password123"), "HR Manager",   0.0, "Morning", "HR Compliance"),
                ("Admin Director", "admin@company.com", hash_password("password123"), "Admin",        0.0, "Morning", "Management"),
            ]
            conn.executemany(
                "INSERT INTO employees (name, email, password, role, hourly_rate, shift_type, capabilities) VALUES (?, ?, ?, ?, ?, ?, ?)",
                seeds,
            )
        else:
            # Migrate plaintext passwords
            for row in conn.execute("SELECT id, password FROM employees").fetchall():
                if not row["password"].startswith("$2"):
                    conn.execute("UPDATE employees SET password=? WHERE id=?",
                                 (hash_password(row["password"]), row["id"]))

        conn.execute("UPDATE work_logs SET status='Todo' WHERE status='To Do'")
        conn.execute("UPDATE work_logs SET status='In Progress' WHERE status='Doing'")

        # ── Column migrations — employees ────────────────────────────────────────
        _ecols = {r[1] for r in conn.execute("PRAGMA table_info(employees)").fetchall()}
        for col, typ in [
            ("birthday", "TEXT"), ("marital_status", "TEXT"), ("gender", "TEXT"),
            ("emergency_contact", "TEXT"), ("emergency_phone", "TEXT"),
            ("admin_notes", "TEXT"), ("hr_feedback", "TEXT"),
            ("sss_no", "TEXT"), ("philhealth_no", "TEXT"),
            ("tin_no", "TEXT"), ("pagibig_no", "TEXT"),
            ("bank_name", "TEXT"), ("bank_account", "TEXT"),
            ("sss_enrolled", "INTEGER DEFAULT 1"),
            ("philhealth_enrolled", "INTEGER DEFAULT 1"),
            ("pagibig_enrolled", "INTEGER DEFAULT 1"),
            ("tax_enrolled", "INTEGER DEFAULT 1"),
            ("vl_enabled", "INTEGER DEFAULT 1"),
            ("sl_enabled", "INTEGER DEFAULT 1"),
            ("vl_days_per_year", "INTEGER DEFAULT 15"),
            ("sl_days_per_year", "INTEGER DEFAULT 15"),
            ("doc_nbi_expiry", "TEXT"),
            ("doc_sss_expiry", "TEXT"),
            ("doc_tin_expiry", "TEXT"),
            ("doc_philhealth_expiry", "TEXT"),
            ("doc_pagibig_expiry", "TEXT"),
            ("employment_type", "TEXT DEFAULT 'Full-time'"),
            ("department", "TEXT"),
            ("first_name", "TEXT"),
            ("last_name", "TEXT"),
            ("middle_name", "TEXT"),
            ("no_middle_name", "INTEGER DEFAULT 0"),
            ("prefix", "TEXT"),
            ("nickname", "TEXT"),
            ("emp_status", "TEXT DEFAULT 'Active'"),
            ("bank_account_name", "TEXT"),
            ("bank_qr_path", "TEXT"),
            ("status_note", "TEXT"),
        ]:
            col_name = col.split()[0]
            if col_name not in _ecols:
                conn.execute(f"ALTER TABLE employees ADD COLUMN {col} {typ}")

        # username backfill
        if "username" not in _ecols:
            conn.execute("ALTER TABLE employees ADD COLUMN username TEXT")
            conn.execute(
                "UPDATE employees SET username = LOWER(REPLACE(SUBSTR(email,1,INSTR(email,'@')-1),'.','_')) WHERE username IS NULL AND email IS NOT NULL AND INSTR(email,'@')>0"
            )

        # ── Column migrations — attendance ────────────────────────────────────────
        _att = {r[1] for r in conn.execute("PRAGMA table_info(attendance)").fetchall()}
        for col, typ in [
            ("is_on_break", "INTEGER DEFAULT 0"),
            ("late_flag", "INTEGER DEFAULT 0"),
            ("ip_address", "TEXT"),
        ]:
            if col not in _att:
                conn.execute(f"ALTER TABLE attendance ADD COLUMN {col} {typ}")

        # ── Column migrations — work_logs ─────────────────────────────────────────
        _wcols = {r[1] for r in conn.execute("PRAGMA table_info(work_logs)").fetchall()}
        for col, typ in [
            ("output_files", "TEXT"), ("started_at", "TEXT"),
            ("is_running", "INTEGER DEFAULT 0"), ("reviewer_name", "TEXT"),
            ("priority", "TEXT DEFAULT 'Medium'"), ("due_date", "TEXT"),
            ("is_archived", "INTEGER DEFAULT 0"),
            ("revision_count", "INTEGER DEFAULT 0"),
            ("created_by_name", "TEXT"),
            ("assigned_emp_id", "INTEGER"),
            ("urgency", "TEXT DEFAULT 'Normal'"),
        ]:
            col_name = col.split()[0]
            if col_name not in _wcols:
                conn.execute(f"ALTER TABLE work_logs ADD COLUMN {col} {typ}")

        # ── Column migrations — payroll_runs ──────────────────────────────────────
        _pcols = {r[1] for r in conn.execute("PRAGMA table_info(payroll_runs)").fetchall()}
        for col, typ in [
            ("hr_approved_by", "TEXT"), ("hr_approved_at", "TEXT"),
            ("gross_pay", "REAL DEFAULT 0"),
            ("sss_deduction", "REAL DEFAULT 0"),
            ("philhealth_deduction", "REAL DEFAULT 0"),
            ("pagibig_deduction", "REAL DEFAULT 0"),
            ("tax_deduction", "REAL DEFAULT 0"),
            ("total_deductions", "REAL DEFAULT 0"),
            ("net_pay", "REAL DEFAULT 0"),
        ]:
            if col not in _pcols:
                conn.execute(f"ALTER TABLE payroll_runs ADD COLUMN {col} {typ}")

        # ── Column migrations — clients ───────────────────────────────────────────
        _clcols = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "monthly_retainer" not in _clcols:
            conn.execute("ALTER TABLE clients ADD COLUMN monthly_retainer REAL DEFAULT 0")

        # ── Column migrations — registration_requests ─────────────────────────────
        _regcols = {r[1] for r in conn.execute("PRAGMA table_info(registration_requests)").fetchall()}
        for col, typ in [
            ("first_name", "TEXT"), ("last_name", "TEXT"),
            ("middle_name", "TEXT"), ("no_middle_name", "INTEGER DEFAULT 0"),
            ("prefix", "TEXT"), ("nickname", "TEXT"),
        ]:
            if col not in _regcols:
                conn.execute(f"ALTER TABLE registration_requests ADD COLUMN {col} {typ}")

        # ── Column migrations — chat_messages ────────────────────────────────────
        _chatcols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
        for col, typ in [
            ("attachment_drive_id", "TEXT"),
            ("attachment_name",     "TEXT"),
            ("attachment_type",     "TEXT"),
            ("reply_to_id",         "INTEGER"),
            ("is_deleted",          "INTEGER DEFAULT 0"),
            ("edited_at",           "TEXT"),
        ]:
            if col not in _chatcols:
                conn.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} {typ}")

        # ── Column migrations — employees (hire_date) ─────────────────────────────
        _ecols2 = {r[1] for r in conn.execute("PRAGMA table_info(employees)").fetchall()}
        if "hire_date" not in _ecols2:
            conn.execute("ALTER TABLE employees ADD COLUMN hire_date TEXT")

        # ── Column migrations — work_logs (recurring) ─────────────────────────────
        _wcols2 = {r[1] for r in conn.execute("PRAGMA table_info(work_logs)").fetchall()}
        for col, typ in [
            ("is_recurring",  "INTEGER DEFAULT 0"),
            ("recur_freq",    "TEXT"),
        ]:
            if col not in _wcols2:
                conn.execute(f"ALTER TABLE work_logs ADD COLUMN {col} {typ}")

        # ── Calendar events ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                description     TEXT,
                start_date      TEXT    NOT NULL,
                end_date        TEXT,
                all_day         INTEGER DEFAULT 1,
                color           TEXT    DEFAULT '#2563eb',
                event_type      TEXT    DEFAULT 'event',
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TEXT    DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(created_by_id) REFERENCES employees(id) ON DELETE SET NULL
            )""")

        # ── Surveys ───────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS surveys (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                description     TEXT,
                status          TEXT DEFAULT 'draft',
                created_by_id   INTEGER,
                created_by_name TEXT,
                created_at      TEXT DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(created_by_id) REFERENCES employees(id) ON DELETE SET NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_questions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id       INTEGER NOT NULL,
                question_text   TEXT NOT NULL,
                question_type   TEXT NOT NULL DEFAULT 'text',
                sort_order      INTEGER DEFAULT 0,
                FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id   INTEGER NOT NULL,
                emp_id      INTEGER NOT NULL,
                assigned_at TEXT DEFAULT (datetime('now', '+8 hours')),
                UNIQUE(survey_id, emp_id),
                FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE,
                FOREIGN KEY(emp_id)    REFERENCES employees(id) ON DELETE CASCADE
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_responses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id    INTEGER NOT NULL,
                emp_id       INTEGER NOT NULL,
                emp_name     TEXT,
                submitted_at TEXT DEFAULT (datetime('now', '+8 hours')),
                UNIQUE(survey_id, emp_id),
                FOREIGN KEY(survey_id) REFERENCES surveys(id) ON DELETE CASCADE
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_answers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                response_id  INTEGER NOT NULL,
                question_id  INTEGER NOT NULL,
                answer_text  TEXT,
                FOREIGN KEY(response_id) REFERENCES survey_responses(id) ON DELETE CASCADE
            )""")

        # ── Task files (Google Drive) ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_files (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         INTEGER NOT NULL,
                file_type       TEXT    NOT NULL DEFAULT 'output',
                drive_id        TEXT    NOT NULL,
                file_name       TEXT    NOT NULL,
                file_size       INTEGER DEFAULT 0,
                uploaded_by_id  INTEGER,
                uploaded_by_name TEXT,
                uploaded_at     TEXT    DEFAULT (datetime('now', '+8 hours')),
                FOREIGN KEY(task_id)        REFERENCES work_logs(id) ON DELETE CASCADE,
                FOREIGN KEY(uploaded_by_id) REFERENCES employees(id) ON DELETE SET NULL
            )""")

        # ── Chat reactions ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_reactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                emoji      TEXT    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now', '+8 hours')),
                UNIQUE(message_id, user_id, emoji),
                FOREIGN KEY(message_id) REFERENCES chat_messages(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id)    REFERENCES employees(id)     ON DELETE CASCADE
            )""")

        # ── HR Kanban tasks (private HR/Admin board) ─────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hr_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                assigned_to INTEGER REFERENCES employees(id),
                assigned_name TEXT,
                priority TEXT DEFAULT 'Normal',
                due_date TEXT,
                status TEXT DEFAULT 'Todo',
                created_by INTEGER,
                created_by_name TEXT,
                created_at TEXT DEFAULT (datetime('now', '+8 hours')),
                is_archived INTEGER DEFAULT 0
            )""")

        # ── Skills tables ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT (datetime('now', '+8 hours'))
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS employee_skills (
                emp_id INTEGER NOT NULL,
                skill_id INTEGER NOT NULL,
                PRIMARY KEY (emp_id, skill_id),
                FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE,
                FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
            )""")
        # Seed default skills
        default_skills = ["Web Developer", "Graphic Designer", "Admin", "HR", "Video Editor"]
        for s in default_skills:
            conn.execute("INSERT OR IGNORE INTO skills (name) VALUES (?)", (s,))

        # ── Payslip logs ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payslip_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                payroll_run_id INTEGER,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                gross_pay REAL DEFAULT 0,
                total_deductions REAL DEFAULT 0,
                net_pay REAL DEFAULT 0,
                generated_by TEXT,
                generated_at TEXT DEFAULT (datetime('now', '+8 hours')),
                printed_by TEXT,
                printed_at TEXT,
                FOREIGN KEY(emp_id) REFERENCES employees(id)
            )""")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )""")

        # ── Seed clients ──────────────────────────────────────────────────────────
        if conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO clients (name, hex_color, sort_order) VALUES (?, ?, ?)",
                [
                    ("Byron",         "#2563eb", 1),
                    ("Pej",           "#7c3aed", 2),
                    ("CHD",           "#ea580c", 3),
                    ("Waren Digital", "#0891b2", 4),
                    ("Syllabi",       "#db2777", 5),
                    ("MBQ",           "#16a34a", 6),
                    ("Internal",      "#64748b", 7),
                    ("Other",         "#ca8a04", 8),
                ],
            )


# ── Time helpers ──────────────────────────────────────────────────────────────

def get_today_date(shift_type: str = "Morning") -> str:
    now = get_pht_now()
    if shift_type == "Night" and now.hour < 7:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def get_week_range(ref_date: date = None):
    if ref_date is None:
        ref_date = date.today()
    monday = ref_date - timedelta(days=ref_date.weekday())
    saturday = monday + timedelta(days=5)
    return monday.strftime("%Y-%m-%d"), saturday.strftime("%Y-%m-%d")


def calculate_hours(clock_in: str, clock_out: str) -> float:
    if not clock_in or not clock_out:
        return 0.0
    try:
        fmt = "%H:%M:%S"
        ci = datetime.strptime(clock_in, fmt)
        co = datetime.strptime(clock_out, fmt)
        if co < ci:
            co += timedelta(days=1)
        return round((co - ci).total_seconds() / 3600, 2)
    except Exception:
        return 0.0


def get_break_minutes(att_id: int) -> float:
    with get_db() as conn:
        breaks = conn.execute(
            "SELECT break_start, break_end FROM attendance_breaks WHERE att_id=? AND break_end IS NOT NULL",
            (att_id,)
        ).fetchall()
    total = 0.0
    for b in breaks:
        try:
            fmt = "%H:%M:%S"
            bs = datetime.strptime(b["break_start"], fmt)
            be = datetime.strptime(b["break_end"], fmt)
            if be < bs:
                be += timedelta(days=1)
            total += (be - bs).total_seconds() / 60
        except Exception:
            pass
    return round(total, 2)


def get_clients():
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM clients WHERE is_active=1 ORDER BY sort_order, name"
        ).fetchall()]


def get_compliance_status(emp: dict) -> tuple[str, int]:
    docs = [emp.get("doc_resume"), emp.get("doc_nbi"), emp.get("doc_sss"),
            emp.get("doc_tin"), emp.get("doc_philhealth"), emp.get("doc_pagibig")]
    count = sum(1 for d in docs if d)
    if count == 6:
        return "compliant", count
    elif count > 0:
        return "partial", count
    return "missing", count


# ── Deduction helpers ─────────────────────────────────────────────────────────

def compute_sss(monthly_equiv: float) -> float:
    """Employee SSS share — 4.5% of monthly salary credit, min ₱135, max ₱900."""
    contrib = round(monthly_equiv * SSS_EMPLOYEE_RATE, 2)
    return max(135.0, min(contrib, 900.0))


def compute_philhealth(monthly_equiv: float) -> float:
    """Employee PhilHealth share — 2.5% of basic salary, min ₱250, max ₱2,500."""
    contrib = round(monthly_equiv * PHILHEALTH_RATE, 2)
    return max(250.0, min(contrib, 2500.0)) / 2  # divided by 2 because semi-monthly


def compute_pagibig(monthly_equiv: float) -> float:
    """Employee Pag-IBIG share — 2%, max ₱100 per month (₱50 per semi-monthly)."""
    contrib = round(monthly_equiv * PAGIBIG_RATE, 2)
    return min(contrib, PAGIBIG_MAX) / 2


def compute_withholding_tax(taxable_income: float) -> float:
    """
    Simplified semi-monthly withholding tax (BIR 2023 tax table).
    taxable_income = gross semi-monthly pay after mandatory deductions.
    """
    ann = taxable_income * 24  # annualize
    if ann <= 250_000:
        return 0.0
    elif ann <= 400_000:
        t = (ann - 250_000) * 0.15
    elif ann <= 800_000:
        t = 22_500 + (ann - 400_000) * 0.20
    elif ann <= 2_000_000:
        t = 102_500 + (ann - 800_000) * 0.25
    elif ann <= 8_000_000:
        t = 402_500 + (ann - 2_000_000) * 0.30
    else:
        t = 2_202_500 + (ann - 8_000_000) * 0.35
    return round(t / 24, 2)  # de-annualize back to semi-monthly


def get_approved_ot_hours(emp_id: int, week_start: str, week_end: str) -> float:
    """Returns total approved OT hours for an employee in a given period."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT hours_computed FROM overtime_requests
               WHERE emp_id=? AND ot_date BETWEEN ? AND ? AND status='Approved'""",
            (emp_id, week_start, week_end)
        ).fetchall()
    return round(sum(r["hours_computed"] for r in rows), 2)


# ── Payroll computation ───────────────────────────────────────────────────────

def compute_payroll_for_employee(emp_id: int, week_start: str, week_end: str) -> dict:
    with get_db() as conn:
        emp = conn.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
        if not emp:
            return {}
        emp = dict(emp)

        # Prefer approved timesheet hours over raw attendance
        approved_ts = conn.execute(
            """SELECT ts.id FROM timesheet_submissions ts
               WHERE ts.emp_id=? AND ts.week_start=? AND ts.status='Approved'
               LIMIT 1""",
            (emp_id, week_start),
        ).fetchone()

        if approved_ts:
            entries = conn.execute(
                "SELECT * FROM timesheet_entries WHERE submission_id=?",
                (approved_ts["id"],),
            ).fetchall()
            regular_hours = sum(e["manual_hours"] or 0 for e in entries)
            approved_ot   = sum(e["ot_hours"] or 0 for e in entries)
        else:
            records = conn.execute(
                "SELECT * FROM attendance WHERE emp_id=? AND date_logged BETWEEN ? AND ?",
                (emp_id, week_start, week_end),
            ).fetchall()
            regular_hours = 0.0
            for rec in records:
                break_mins = get_break_minutes(rec["id"])
                raw_hours = calculate_hours(rec["clock_in"], rec["clock_out"])
                daily = max(0.0, raw_hours - break_mins / 60)
                regular_hours += min(daily, 8.0)
            approved_ot = get_approved_ot_hours(emp_id, week_start, week_end)

    rate = emp["hourly_rate"]
    ot_rate = rate * OT_MULTIPLIER_REGULAR

    regular_pay = round(regular_hours * rate, 2)
    overtime_pay = round(approved_ot * ot_rate, 2)
    gross_pay = round(regular_pay + overtime_pay, 2)

    # Estimate monthly equivalent (assume 2 payroll periods/month)
    monthly_equiv = gross_pay * 2

    sss   = compute_sss(monthly_equiv)   if emp.get("sss_enrolled", 1)       else 0.0
    phic  = compute_philhealth(monthly_equiv) if emp.get("philhealth_enrolled", 1) else 0.0
    hdmf  = compute_pagibig(monthly_equiv)    if emp.get("pagibig_enrolled", 1)    else 0.0

    taxable = gross_pay - sss - phic - hdmf
    tax   = compute_withholding_tax(taxable) if emp.get("tax_enrolled", 1) else 0.0

    total_deductions = round(sss + phic + hdmf + tax, 2)
    net_pay = round(gross_pay - total_deductions, 2)

    return {
        "emp_id":              emp_id,
        "emp_name":            emp["name"],
        "shift_type":          emp["shift_type"],
        "hourly_rate":         rate,
        "regular_hours":       round(regular_hours, 2),
        "overtime_hours":      approved_ot,
        "regular_pay":         regular_pay,
        "overtime_pay":        overtime_pay,
        "gross_pay":           gross_pay,
        "sss_deduction":       sss,
        "philhealth_deduction": phic,
        "pagibig_deduction":   hdmf,
        "tax_deduction":       tax,
        "total_deductions":    total_deductions,
        "net_pay":             net_pay,
        "total_pay":           gross_pay,  # kept for backwards compat
        "sss_enrolled":        bool(emp.get("sss_enrolled", 1)),
        "philhealth_enrolled": bool(emp.get("philhealth_enrolled", 1)),
        "pagibig_enrolled":    bool(emp.get("pagibig_enrolled", 1)),
        "tax_enrolled":        bool(emp.get("tax_enrolled", 1)),
        "run_id":              None,
    }


# ── Notification helper ───────────────────────────────────────────────────────

def push_notification(conn, user_id: int, title: str, body: str = "", link: str = ""):
    conn.execute(
        "INSERT INTO notifications (user_id, title, body, link) VALUES (?, ?, ?, ?)",
        (user_id, title, body, link)
    )


def get_unread_count(user_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM notifications
               WHERE user_id=? AND is_read=0
               AND (link IS NULL OR link NOT LIKE '/chat%')""",
            (user_id,)
        ).fetchone()
    return row[0] if row else 0


# ── Chat ─────────────────────────────────────────────────────────────────────

def dm_room(id1: int, id2: int) -> str:
    a, b = sorted([int(id1), int(id2)])
    return f"dm_{a}_{b}"


def get_chat_unread_count(user_id: int) -> int:
    """Total unread messages across the group channel and all DM threads."""
    with get_db() as conn:
        rooms = conn.execute(
            """SELECT DISTINCT room FROM chat_messages
               WHERE room = 'group' OR room LIKE 'dm_%'"""
        ).fetchall()
        total = 0
        for r in rooms:
            room = r["room"]
            if room != "group" and not _user_in_dm_room(room, user_id):
                continue
            last_read = conn.execute(
                "SELECT last_read_msg_id FROM chat_reads WHERE user_id=? AND room=?",
                (user_id, room)
            ).fetchone()
            last_read_id = last_read["last_read_msg_id"] if last_read else 0
            cnt = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE room=? AND id>? AND sender_id!=?",
                (room, last_read_id, user_id)
            ).fetchone()[0]
            total += cnt
    return total


def _user_in_dm_room(room: str, user_id: int) -> bool:
    if not room.startswith("dm_"):
        return False
    parts = room.split("_")
    return str(user_id) in parts[1:3]


# ── Audit logging ─────────────────────────────────────────────────────────────

def audit(conn, user_id, user_name: str, action: str,
          target_table: str = None, target_id: int = None,
          old_value: str = None, new_value: str = None, ip: str = None):
    conn.execute(
        """INSERT INTO audit_log
           (user_id, user_name, action, target_table, target_id, old_value, new_value, ip_address)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, user_name, action, target_table, target_id, old_value, new_value, ip)
    )


def log_card_activity(conn, card_id: int, actor_name: str,
                      activity_type: str, detail: str = None):
    conn.execute(
        "INSERT INTO card_activities (card_id, actor_name, activity_type, detail) VALUES (?,?,?,?)",
        (card_id, actor_name, activity_type, detail)
    )


# ── Company settings ──────────────────────────────────────────────────────────

_COMPANY_DEFAULTS = {
    "name":        "Hundredfold Digital Marketing",
    "tagline":     "Employee Portal",
    "address":     "",
    "phone":       "",
    "email":       "",
    "website":     "",
    "tin":         "",
    "sss_employer": "",
    "philhealth_employer": "",
    "pagibig_employer": "",
    "dti":         "",
    "logo_path":   "",
}

def get_company_settings() -> dict:
    settings = dict(_COMPANY_DEFAULTS)
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM company_settings").fetchall()
        for r in rows:
            if r["key"] in settings:
                settings[r["key"]] = r["value"] or ""
    return settings


def save_company_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO company_settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
