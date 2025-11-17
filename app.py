from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash
import json
import random
from datetime import datetime, timedelta
import os
import itertools
import base64
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # Generate a secure secret key
app.permanent_session_lifetime = timedelta(days=30)  # Sessions last 30 days

# Database initialization
def init_db():
    conn = sqlite3.connect('gre_practice.db')
    c = conn.cursor()
    
    # Create users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create user_progress table
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER,
            attempted_questions_bitmap TEXT,
            test_history TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            PRIMARY KEY (user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Load and normalize questions from JSON file
def load_questions():
    with open('gre_quant_questions.json', 'r') as f:
        data = json.load(f)
    
    questions = []
    id_counter = itertools.count(1)
    
    for difficulty, qlist in data.items():
        for q in qlist:
            q = q.copy()
            q['difficulty'] = difficulty
            
            # Standardize QC quantity keys
            if q.get('type') == 'qc':
                if 'quantityA' in q:
                    q['quantity_a'] = q.pop('quantityA')
                if 'quantityB' in q:
                    q['quantity_b'] = q.pop('quantityB')
            
            # Assign topic based on question content
            q['topic'] = assign_topic(q)
            
            # Normalize the correct answer format
            q = normalize_correct_answer(q)
            
            q['id'] = next(id_counter)
            questions.append(q)
    
    return questions

def assign_topic(question):
    """Assign topic based on question content"""
    q_text = question.get('question', '').lower()
    
    if any(word in q_text for word in ['gcd', 'lcm', 'factor', 'prime', 'divisible', 'multiple']):
        return 'Number Properties'
    elif any(word in q_text for word in ['volume', 'area', 'radius', 'sphere', 'cone', 'cylinder', 'triangle', 'circle', 'rectangle']):
        return 'Geometry'
    elif any(word in q_text for word in ['equation', 'variable', 'solve', 'function', 'absolute']):
        return 'Algebra'
    elif any(word in q_text for word in ['percentage', 'ratio', 'proportion', 'average', 'mean']):
        return 'Arithmetic'
    elif any(word in q_text for word in ['chart', 'graph', 'data', 'statistics']):
        return 'Data Analysis'
    else:
        return 'Arithmetic'  # Default

def normalize_correct_answer(question):
    """Normalize the correct answer to be consistent"""
    q_type = question.get('type')
    correct = question.get('correct')
    
    if q_type == 'qc':
        # Standard QC options
        QC_OPTIONS = [
            "Quantity A is greater",
            "Quantity B is greater", 
            "The two quantities are equal",
            "The relationship cannot be determined from the information given"
        ]
        
        # Convert various formats to standard index
        if isinstance(correct, int):
            # Already an index, ensure it's valid
            if 0 <= correct < 4:
                question['correct'] = correct
            else:
                question['correct'] = 0  # Default
        elif isinstance(correct, str):
            # Could be A/B/C/D or full text
            if correct.upper() in "ABCD":
                question['correct'] = "ABCD".index(correct.upper())
            elif correct in QC_OPTIONS:
                question['correct'] = QC_OPTIONS.index(correct)
            else:
                question['correct'] = 0  # Default
                
    return question

# GRE distribution patterns
GRE_DISTRIBUTION = {
    'types': {
        'qc': 0.40,
        'mc': 0.30,
        'ma': 0.15,
        'numeric': 0.15
    },
    'difficulty': {
        'easy': 0.30,
        'medium': 0.50,
        'hard': 0.20
    }
}

# Database helper functions
def get_user_by_email(email):
    conn = sqlite3.connect('gre_practice.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE email = ?', (email,))
    user = c.fetchone()
    conn.close()
    return user

def create_user(email, password):
    conn = sqlite3.connect('gre_practice.db')
    c = conn.cursor()
    password_hash = generate_password_hash(password)
    try:
        c.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, password_hash))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        return None

def load_user_progress(user_id):
    conn = sqlite3.connect('gre_practice.db')
    c = conn.cursor()
    c.execute('SELECT attempted_questions_bitmap, test_history FROM user_progress WHERE user_id = ?', (user_id,))
    progress = c.fetchone()
    conn.close()
    
    if progress:
        bitmap = progress[0] if progress[0] else ''
        history = json.loads(progress[1]) if progress[1] else []
        return bitmap, history
    return '', []

def save_user_progress(user_id, bitmap=None, history=None):
    conn = sqlite3.connect('gre_practice.db')
    c = conn.cursor()
    
    # Get existing progress
    c.execute('SELECT attempted_questions_bitmap, test_history FROM user_progress WHERE user_id = ?', (user_id,))
    existing = c.fetchone()
    
    if existing:
        # Update existing progress
        if bitmap is not None:
            current_bitmap = bitmap
        else:
            current_bitmap = existing[0] if existing[0] else ''
            
        if history is not None:
            current_history = json.dumps(history)
        else:
            current_history = existing[1] if existing[1] else '[]'
            
        c.execute('''
            UPDATE user_progress 
            SET attempted_questions_bitmap = ?, test_history = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        ''', (current_bitmap, current_history, user_id))
    else:
        # Create new progress entry
        bitmap = bitmap if bitmap is not None else ''
        history_json = json.dumps(history) if history is not None else '[]'
        c.execute('''
            INSERT INTO user_progress (user_id, attempted_questions_bitmap, test_history)
            VALUES (?, ?, ?)
        ''', (user_id, bitmap, history_json))
    
    conn.commit()
    conn.close()

def get_attempted_questions_bitmap():
    if 'user_id' not in session:
        b64 = session.get('attempted_questions_bitmap', None)
        if b64 is None:
            return bytearray()
        try:
            return bytearray(base64.b64decode(b64))
        except Exception:
            return bytearray()
    else:
        # Load from database
        bitmap_str, _ = load_user_progress(session['user_id'])
        if bitmap_str:
            try:
                return bytearray(base64.b64decode(bitmap_str))
            except:
                return bytearray()
        return bytearray()

def set_attempted_questions_bitmap(bitmap):
    bitmap_str = base64.b64encode(bytes(bitmap)).decode('ascii')
    if 'user_id' in session:
        # Save to database
        save_user_progress(session['user_id'], bitmap=bitmap_str)
    else:
        session['attempted_questions_bitmap'] = bitmap_str

def mark_question_attempted_bitmap(question_id):
    idx = question_id - 1
    bitmap = get_attempted_questions_bitmap()
    byte_idx = idx // 8
    bit_idx = idx % 8
    while len(bitmap) <= byte_idx:
        bitmap.append(0)
    bitmap[byte_idx] |= (1 << bit_idx)
    set_attempted_questions_bitmap(bitmap)

def is_question_attempted_bitmap(question_id):
    idx = question_id - 1
    bitmap = get_attempted_questions_bitmap()
    byte_idx = idx // 8
    bit_idx = idx % 8
    if byte_idx >= len(bitmap):
        return False
    return (bitmap[byte_idx] & (1 << bit_idx)) != 0

def get_attempted_questions():
    all_questions = load_questions()
    attempted = []
    for q in all_questions:
        if is_question_attempted_bitmap(q['id']):
            attempted.append(q['id'])
    return attempted

def mark_question_attempted(question_id):
    mark_question_attempted_bitmap(question_id)

def select_questions(num_questions):
    all_questions = load_questions()
    attempted = get_attempted_questions()
    available_questions = [q for q in all_questions if q['id'] not in attempted]
    
    if len(available_questions) < num_questions:
        # Reset attempted questions if not enough available
        set_attempted_questions_bitmap(bytearray())
        available_questions = all_questions
    
    # Select questions based on type distribution
    target_types = {k: int(v * num_questions) for k, v in GRE_DISTRIBUTION['types'].items()}
    total_types = sum(target_types.values())
    if total_types < num_questions:
        target_types['mc'] += num_questions - total_types
    
    selected = []
    for q_type, count in target_types.items():
        type_questions = [q for q in available_questions if q['type'] == q_type and q['id'] not in [s['id'] for s in selected]]
        if len(type_questions) >= count:
            selected.extend(random.sample(type_questions, count))
        else:
            selected.extend(type_questions)
    
    # Fill remaining if needed
    if len(selected) < num_questions:
        remaining = [q for q in available_questions if q['id'] not in [s['id'] for s in selected]]
        needed = num_questions - len(selected)
        selected.extend(random.sample(remaining, min(needed, len(remaining))))
    
    random.shuffle(selected)
    return selected[:num_questions]

@app.route('/')
def index():
    return render_template('index.html', logged_in='user_id' in session)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        if not email or '@' not in email:
            flash('Please enter a valid email address', 'error')
            return redirect(url_for('register'))
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long', 'error')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return redirect(url_for('register'))
        
        # Create user
        user_id = create_user(email, password)
        if user_id:
            session.permanent = True
            session['user_id'] = user_id
            session['user_email'] = email
            flash('Registration successful! Welcome to GRE Practice.', 'success')
            return redirect(url_for('index'))
        else:
            flash('An account with this email already exists', 'error')
            return redirect(url_for('register'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        user = get_user_by_email(email)
        if user and check_password_hash(user[2], password):  # user[2] is password_hash
            session.permanent = True
            session['user_id'] = user[0]  # user[0] is id
            session['user_email'] = user[1]  # user[1] is email
            
            # Load user's test history into session
            _, history = load_user_progress(user[0])
            session['test_history'] = history
            
            flash('Welcome back!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid email or password', 'error')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('index'))

@app.route('/start_test', methods=['POST'])
@login_required
def start_test():
    # Clear any existing test data
    session.pop('current_test', None)
    session.pop('current_question_idx', None)
    session['clear_gre_timer'] = True
    
    test_format = request.form.get('format')
    test_params = {
        'quick': {'questions': 12, 'time': 18},
        'standard': {'questions': 15, 'time': 23},
        'full': {'questions': 27, 'time': 47}
    }
    
    params = test_params.get(test_format)
    if not params:
        return redirect(url_for('index'))
    
    questions = select_questions(params['questions'])
    question_ids = [q['id'] for q in questions]
    
    # Mark questions as attempted
    for q_id in question_ids:
        mark_question_attempted(q_id)
    
    session['current_test'] = {
        'format': test_format,
        'question_ids': question_ids,
        'time_limit': params['time'],
        'start_time': datetime.now().isoformat(),
        'answers': {}  # Will store answers with string keys
    }
    session['current_question_idx'] = 0
    
    return redirect(url_for('test'))

@app.route('/test', methods=['GET', 'POST'])
@login_required
def test():
    if 'current_test' not in session:
        return redirect(url_for('index'))
    
    test_data = session['current_test']
    all_questions = load_questions()
    question_lookup = {q['id']: q for q in all_questions}
    question_ids = test_data['question_ids']
    total_questions = len(question_ids)
    
    # Get current question index
    q_idx = request.args.get('q', type=int)
    if q_idx is None:
        q_idx = session.get('current_question_idx', 0)
    else:
        session['current_question_idx'] = q_idx
    
    q_idx = max(0, min(q_idx, total_questions - 1))
    session['current_question_idx'] = q_idx
    q_id = question_ids[q_idx]
    question = question_lookup[q_id]
    
    # Get existing answer if any
    answers = test_data.get('answers', {})
    existing_answer = answers.get(str(q_id))  # Use string key consistently
    
    return render_template('test.html',
        question=question,
        q_idx=q_idx,
        total_questions=total_questions,
        answer=existing_answer,
        time_limit=test_data['time_limit'])

@app.route('/submit_answer', methods=['POST'])
@login_required
def submit_answer():
    if 'current_test' not in session:
        return jsonify({'error': 'No active test'}), 400
    
    data = request.json
    question_id = data.get('question_id')
    answer = data.get('answer')
    
    # Ensure question_id is stored as string
    try:
        question_id = str(int(question_id))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question id'}), 400
    
    # Store the answer
    session['current_test']['answers'][question_id] = answer
    session.modified = True
    
    return jsonify({'success': True})

@app.route('/submit_test', methods=['POST'])
@login_required
def submit_test():
    if 'current_test' not in session:
        return redirect(url_for('index'))
    
    test_data = session['current_test']
    all_questions = load_questions()
    question_lookup = {q['id']: q for q in all_questions}
    question_ids = test_data['question_ids']
    user_answers = test_data.get('answers', {})
    
    correct = 0
    total = len(question_ids)
    results_detail = []
    
    for q_id in question_ids:
        question = question_lookup[q_id]
        user_answer = user_answers.get(str(q_id))  # Use string key
        
        # Score the answer
        is_correct = score_answer(question, user_answer)
        
        if is_correct:
            correct += 1
        
        # Format the correct answer for display
        display_correct = format_correct_answer(question)
        
        results_detail.append({
            'question': question,
            'user_answer': format_user_answer(question, user_answer),
            'correct': is_correct,
            'correct_answer': display_correct
        })
    
    accuracy = round((correct / total * 100) if total > 0 else 0, 1)
    
    # Update test history
    if 'test_history' not in session:
        session['test_history'] = []
    
    session['test_history'].append({
        'date': datetime.now().isoformat(),
        'format': test_data['format'],
        'accuracy': accuracy,
        'correct': correct,
        'total': total
    })
    
    # Keep only last 10 tests
    session['test_history'] = session['test_history'][-10:]
    
    # Save to database
    if 'user_id' in session:
        save_user_progress(session['user_id'], history=session['test_history'])
    
    # Store results for display
    session['last_results'] = {
        'accuracy': accuracy,
        'correct': correct,
        'total': total,
        'details': results_detail
    }
    
    # Clear current test
    session.pop('current_test', None)
    session.modified = True
    
    return redirect(url_for('results'))

def score_answer(question, user_answer):
    """Score a single answer"""
    if user_answer is None or user_answer == '':
        return False
    
    q_type = question['type']
    correct = question.get('correct')
    
    try:
        if q_type == 'mc':
            # Multiple choice - correct is an index
            if isinstance(correct, int) and 0 <= correct < len(question['options']):
                correct_text = question['options'][correct]
                return str(user_answer) == str(correct_text)
            return False
            
        elif q_type == 'ma':
            # Multiple answer - correct is a list of indices
            if not isinstance(user_answer, list):
                return False
            correct_indices = correct if isinstance(correct, list) else [correct]
            correct_texts = [question['options'][i] for i in correct_indices if i < len(question['options'])]
            return set(user_answer) == set(correct_texts)
            
        elif q_type == 'qc':
            # Quantitative comparison
            QC_OPTIONS = [
                "Quantity A is greater",
                "Quantity B is greater",
                "The two quantities are equal",
                "The relationship cannot be determined from the information given"
            ]
            
            # Get the correct answer index (should be normalized by now)
            correct_idx = correct if isinstance(correct, int) else 0
            
            # Get user answer index
            user_idx = None
            if user_answer in QC_OPTIONS:
                user_idx = QC_OPTIONS.index(user_answer)
            
            return user_idx == correct_idx
            
        elif q_type == 'numeric':
            # Numeric answer
            try:
                user_num = float(str(user_answer).strip())
                correct_num = float(correct)
                # Allow small tolerance for floating point
                return abs(user_num - correct_num) < 0.01
            except (ValueError, TypeError):
                return False
                
    except Exception as e:
        print(f"Error scoring question {question.get('id')}: {e}")
        return False
    
    return False

def format_correct_answer(question):
    """Format the correct answer for display"""
    q_type = question['type']
    correct = question.get('correct')
    
    if q_type == 'mc':
        if isinstance(correct, int) and 0 <= correct < len(question['options']):
            return question['options'][correct]
        return "Error in answer"
        
    elif q_type == 'ma':
        correct_indices = correct if isinstance(correct, list) else [correct]
        return [question['options'][i] for i in correct_indices if i < len(question['options'])]
        
    elif q_type == 'qc':
        QC_OPTIONS = [
            "Quantity A is greater",
            "Quantity B is greater",
            "The two quantities are equal",
            "The relationship cannot be determined from the information given"
        ]
        idx = correct if isinstance(correct, int) else 0
        return QC_OPTIONS[idx] if idx < len(QC_OPTIONS) else "Error"
        
    elif q_type == 'numeric':
        return str(correct)
        
    return str(correct)

def format_user_answer(question, user_answer):
    """Format user answer for display"""
    if user_answer is None:
        return None
    if isinstance(user_answer, list):
        return user_answer
    return str(user_answer)

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
    set_attempted_questions_bitmap(bytearray())
    session.modified = True
    return jsonify({'success': True})

@app.route('/profile')
@login_required
def profile():
    _, history = load_user_progress(session['user_id'])
    total_tests = len(history)
    
    if total_tests > 0:
        avg_accuracy = sum(h['accuracy'] for h in history) / total_tests
        total_questions = sum(h['total'] for h in history)
        total_correct = sum(h['correct'] for h in history)
    else:
        avg_accuracy = 0
        total_questions = 0
        total_correct = 0
    
    # Count attempted questions
    attempted = get_attempted_questions()
    all_questions = load_questions()
    
    return render_template('profile.html',
        email=session['user_email'],
        total_tests=total_tests,
        avg_accuracy=round(avg_accuracy, 1),
        total_questions=total_questions,
        total_correct=total_correct,
        questions_attempted=len(attempted),
        total_available=len(all_questions),
        test_history=history[-10:] if history else []
    )

if __name__ == '__main__':
    app.run(debug=True)
