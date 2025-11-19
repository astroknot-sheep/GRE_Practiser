# app.py - Ultra Fast GRE Quantitative Practice (Optimized for Render Free Tier)

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
import json
import random
from datetime import datetime, timedelta
import os
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import time
from contextlib import contextmanager
import re

app = Flask(__name__)
# Use environment variable for secret key or fallback to random (note: random invalidates sessions on restart)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)

# ========================= GLOBAL CACHED DATA =========================
questions = []
question_lookup = {}
max_question_id = 0

def load_processed_questions():
    global questions, question_lookup, max_question_id
    start = time.time()
    try:
        with open('processed_questions.json', 'r', encoding='utf-8') as f:
            questions = json.load(f)
        question_lookup = {q['id']: q for q in questions}
        max_question_id = max((q['id'] for q in questions), default=0)
        app.logger.info(f"Successfully loaded {len(questions)} questions in {time.time() - start:.3f}s")
    except FileNotFoundError:
        app.logger.error("processed_questions.json not found! Run preprocess.py first.")
        questions = []
        question_lookup = {}
    except Exception as e:
        app.logger.error(f"Error loading questions: {e}")
        questions = []
        question_lookup = {}

# Load questions once at startup
load_processed_questions()

if not questions:
    raise RuntimeError("No questions loaded. Create processed_questions.json using preprocess.py")

# ========================= DATABASE INIT =========================
# ========================= DATABASE INIT =========================
@contextmanager
def get_db():
    conn = sqlite3.connect('gre_practice.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Optimized: storing attempted_questions as JSON list instead of bitmap
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id INTEGER PRIMARY KEY,
                attempted_questions TEXT,
                test_history TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        conn.commit()

init_db()

# ========================= DECORATORS & HELPERS =========================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated

def get_user_by_email(email):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT id, email, password_hash FROM users WHERE email = ?', (email,))
        return c.fetchone()

def create_user(email, password):
    password_hash = generate_password_hash(password)
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, password_hash))
            user_id = c.lastrowid
            # Initialize empty progress
            c.execute('INSERT INTO user_progress (user_id, attempted_questions, test_history) VALUES (?, ?, ?)',
                      (user_id, '[]', '[]'))
            conn.commit()
            return user_id
    except sqlite3.IntegrityError:
        return None

def load_user_progress(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT attempted_questions, test_history FROM user_progress WHERE user_id = ?', (user_id,))
        row = c.fetchone()

    if not row:
        return set(), []

    # Optimized: Load directly from JSON
    try:
        attempted_list = json.loads(row[0]) if row[0] else []
        attempted_set = set(attempted_list)
    except (json.JSONDecodeError, TypeError):
        attempted_set = set()
        
    try:
        history = json.loads(row[1]) if row[1] else []
    except (json.JSONDecodeError, TypeError):
        history = []
        
    return attempted_set, history

def save_user_progress(user_id, attempted_set=None, history=None):
    # We need to fetch existing if partial update, but usually we have full state in session
    # For optimization, let's assume we pass what we want to save.
    
    with get_db() as conn:
        c = conn.cursor()
        
        # If we only have one, we might need to fetch the other, but let's try to avoid read-before-write if possible
        # or just do a quick read.
        
        current_attempted_json = None
        current_history_json = None
        
        if attempted_set is None or history is None:
            c.execute('SELECT attempted_questions, test_history FROM user_progress WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            if row:
                if attempted_set is None:
                    current_attempted_json = row[0]
                if history is None:
                    current_history_json = row[1]
        
        final_attempted_json = json.dumps(list(attempted_set)) if attempted_set is not None else (current_attempted_json or '[]')
        final_history_json = json.dumps(history) if history is not None else (current_history_json or '[]')

        c.execute('''
            INSERT INTO user_progress (user_id, attempted_questions, test_history)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            attempted_questions = excluded.attempted_questions,
            test_history = excluded.test_history,
            updated_at = CURRENT_TIMESTAMP
        ''', (user_id, final_attempted_json, final_history_json))
        conn.commit()

# ========================= ROUTES =========================

@app.route('/')
def index():
    logged_in = 'user_id' in session
    return render_template('index.html', logged_in=logged_in)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        confirm = request.form['confirm_password']

        # Password complexity check
        if password != confirm:
            flash('Passwords do not match', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters', 'error')
        elif not re.search(r"[A-Z]", password) or not re.search(r"\d", password):
            flash('Password must contain at least one uppercase letter and one number', 'error')
        elif get_user_by_email(email):
            flash('Email already registered', 'error')
        else:
            user_id = create_user(email, password)
            if user_id:
                session['user_id'] = user_id
                session['user_email'] = email
                attempted_set, history = load_user_progress(user_id)
                session['attempted_set'] = list(attempted_set)
                session['test_history'] = history
                flash('Account created successfully!', 'success')
                return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = get_user_by_email(email)
        if user and check_password_hash(user[2], password):
            session['user_id'] = user[0]
            session['user_email'] = user[1]
            attempted_set, history = load_user_progress(user[0])
            session['attempted_set'] = list(attempted_set)
            session['test_history'] = history
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next') or url_for('index')
            return redirect(next_page)
        flash('Invalid email or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))

# ========================= TEST GENERATION =========================
GRE_FORMATS = {
    'quick': {'questions': 12, 'minutes': 18},
    'standard': {'questions': 15, 'minutes': 23},
    'full': {'questions': 27, 'minutes': 47}
}

@app.route('/start_test', methods=['POST'])
@login_required
def start_test():
    format_type = request.form['format']
    if format_type not in GRE_FORMATS:
        flash('Invalid test format', 'error')
        return redirect(url_for('index'))

    cfg = GRE_FORMATS[format_type]
    num_questions = cfg['questions']
    time_limit = cfg['minutes']

    attempted_set = set(session.get('attempted_set', []))

    # Filter unattempted questions
    available = [q for q in questions if q['id'] not in attempted_set]
    
    if len(available) < num_questions:
        # Reset if not enough
        if len(available) == 0:
            flash('No new questions left! Resetting your progress.', 'warning')
        else:
            flash(f'Only {len(available)} new questions left. Including some repeats.', 'info')
        available = questions[:]

    selected = random.sample(available, min(num_questions, len(available)))
    question_ids = [q['id'] for q in selected]

    test_data = {
        'question_ids': question_ids,
        'format': format_type,
        'time_limit': time_limit,
        'start_time': datetime.now().isoformat()
    }

    session['current_test'] = test_data
    session['clear_gre_timer'] = True  # Clear old timer
    session.modified = True

    return redirect(url_for('test', q_idx=0))

# ========================= TEST TAKING =========================
@app.route('/test/<int:q_idx>')
@login_required
def test(q_idx):
    if 'current_test' not in session:
        return redirect(url_for('index'))

    test_data = session['current_test']
    question_ids = test_data['question_ids']
    total_questions = len(question_ids)
    time_limit = test_data['time_limit']

    if q_idx < 0 or q_idx >= total_questions:
        return redirect(url_for('test', q_idx=0 if q_idx < 0 else total_questions-1))

    q_id = question_ids[q_idx]
    question = question_lookup[q_id]
    user_answer = session.get('user_answers', {}).get(str(q_id))

    # Auto-save format for JS
    answer = None
    if question['type'] == 'ma' and user_answer:
        answer = user_answer if isinstance(user_answer, list) else [user_answer]

    # Calculate remaining time
    start_time = datetime.fromisoformat(test_data['start_time'])
    elapsed = (datetime.now() - start_time).total_seconds()
    remaining_seconds = max(0, int(time_limit * 60 - elapsed))

    return render_template('test.html',
                           question=question,
                           q_idx=q_idx,
                           total_questions=total_questions,
                           time_limit=time_limit,
                           remaining_seconds=remaining_seconds,
                           answer=answer)

@app.route('/submit_answer', methods=['POST'])
@login_required
def submit_answer():
    data = request.get_json()
    q_id = data['question_id']
    answer = data['answer']

    if 'user_answers' not in session:
        session['user_answers'] = {}
    session['user_answers'][str(q_id)] = answer
    session.modified = True
    return jsonify({'status': 'saved'})

# ========================= SUBMIT TEST =========================
def score_answer(question, user_answer):
    if user_answer is None or user_answer == '':
        return False

    q_type = question['type']
    correct = question.get('correct')

    try:
        if q_type == 'mc':
            correct_text = question['options'][correct]
            return str(user_answer) == str(correct_text)

        elif q_type == 'ma':
            if not isinstance(user_answer, list):
                return False
            correct_indices = correct if isinstance(correct, list) else [correct]
            correct_texts = [question['options'][i] for i in correct_indices]
            return set(user_answer) == set(correct_texts)

        elif q_type == 'qc':
            # Define constants once
            QC_OPTIONS = [
                "Quantity A is greater",
                "Quantity B is greater",
                "The two quantities are equal",
                "The relationship cannot be determined from the information given"
            ]
            correct_idx = correct if isinstance(correct, int) else 0
            user_idx = QC_OPTIONS.index(user_answer) if user_answer in QC_OPTIONS else -1
            return user_idx == correct_idx

        elif q_type == 'numeric':
            user_num = float(str(user_answer).strip())
            correct_num = float(correct)
            return abs(user_num - correct_num) < 0.01
    except:
        return False
    return False

@app.route('/submit_test', methods=['POST'])
@login_required
def submit_test():
    if 'current_test' not in session:
        return redirect(url_for('index'))

    test_data = session['current_test']
    question_ids = test_data['question_ids']
    user_answers = session.get('user_answers', {})

    correct_count = 0
    details = []

    attempted_set = set(session.get('attempted_set', []))

    for q_id in question_ids:
        q = question_lookup[q_id]
        ans = user_answers.get(str(q_id))
        is_correct = score_answer(q, ans)
        if is_correct:
            correct_count += 1

        # Mark as attempted
        attempted_set.add(q_id)

        details.append({
            'question': q,
            'user_answer': ans if isinstance(ans, list) else str(ans) if ans else None,
            'correct': is_correct,
            'correct_answer': q['correct']
        })

    accuracy = round(correct_count / len(question_ids) * 100, 1) if question_ids else 0

    # Update history
    history = session.get('test_history', [])
    history.append({
        'date': datetime.now().isoformat(),
        'format': test_data['format'],
        'accuracy': accuracy,
        'correct': correct_count,
        'total': len(question_ids)
    })
    history = history[-10:]

    # Save to DB and session
    session['attempted_set'] = list(attempted_set)
    session['test_history'] = history
    save_user_progress(session['user_id'], attempted_set=attempted_set, history=history)

    session['last_results'] = {
        'accuracy': accuracy,
        'correct': correct_count,
        'total': len(question_ids),
        'details': details
    }

    # Cleanup
    session.pop('current_test', None)
    session.pop('user_answers', None)
    session.modified = True

    return redirect(url_for('results'))

@app.route('/results')
@login_required
def results():
    if 'last_results' not in session:
        return redirect(url_for('index'))
    results = session['last_results']
    history = session.get('test_history', [])
    return render_template('results.html', results=results, history=history)

@app.route('/reset_history', methods=['POST'])
@login_required
def reset_history():
    session['attempted_set'] = []
    session['test_history'] = []
    save_user_progress(session['user_id'], attempted_set=set(), history=[])
    flash('All progress and history have been reset.', 'success')
    return jsonify({'success': True})

@app.route('/profile')
@login_required
def profile():
    history = session.get('test_history', [])
    attempted = len(session.get('attempted_set', []))
    total_tests = len(history)
    avg_accuracy = round(sum(h['accuracy'] for h in history) / total_tests, 1) if total_tests else 0
    total_correct = sum(h['correct'] for h in history)
    total_questions = sum(h['total'] for h in history)

    return render_template('profile.html',
                           email=session['user_email'],
                           total_tests=total_tests,
                           avg_accuracy=avg_accuracy,
                           total_correct=total_correct,
                           total_questions=total_questions,
                           questions_attempted=attempted,
                           total_available=len(questions),
                           test_history=history[-10:])

if __name__ == '__main__':
    app.run(debug=True)
