from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import json
import random
from datetime import datetime
import os
import itertools
import base64

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'

# Load questions from JSON file
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
            
            q['id'] = next(id_counter)
            questions.append(q)
    return questions

# GRE distribution patterns
GRE_DISTRIBUTION = {
    'types': {
        'qc': 0.40,   # Quantitative Comparison
        'mc': 0.30,   # Multiple Choice (single)
        'ma': 0.15,   # Multiple Answer
        'numeric': 0.15  # Numeric Entry
    },
    'topics': {
        'Arithmetic': 0.45,
        'Algebra': 0.20,
        'Geometry': 0.15,
        'Data Analysis': 0.20
    },
    'difficulty': {
        'easy': 0.30,
        'medium': 0.50,
        'hard': 0.20
    }
}

def get_attempted_questions_bitmap():
    b64 = session.get('attempted_questions_bitmap', None)
    if b64 is None:
        return bytearray()
    try:
        return bytearray(base64.b64decode(b64))
    except Exception:
        return bytearray()

def set_attempted_questions_bitmap(bitmap):
    session['attempted_questions_bitmap'] = base64.b64encode(bytes(bitmap)).decode('ascii')

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
        session['attempted_questions_bitmap'] = base64.b64encode(b'').decode('ascii')
        available_questions = all_questions
    
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
    
    if len(selected) < num_questions:
        remaining = [q for q in available_questions if q['id'] not in [s['id'] for s in selected]]
        needed = num_questions - len(selected)
        selected.extend(random.sample(remaining, min(needed, len(remaining))))
    
    random.shuffle(selected)
    return selected[:num_questions]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_test', methods=['POST'])
def start_test():
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

    for q_id in question_ids:
        mark_question_attempted(q_id)

    session['current_test'] = {
        'format': test_format,
        'question_ids': question_ids,
        'time_limit': params['time'],
        'start_time': datetime.now().isoformat(),
        'answers': {}
    }
    session['current_question_idx'] = 0
    
    return redirect(url_for('test'))

@app.route('/test', methods=['GET', 'POST'])
def test():
    if 'current_test' not in session:
        return redirect(url_for('index'))
    
    test_data = session['current_test']
    all_questions = load_questions()
    question_lookup = {q['id']: q for q in all_questions}
    question_ids = test_data['question_ids']
    total_questions = len(question_ids)
    
    q_idx = request.args.get('q', type=int)
    if q_idx is None:
        q_idx = session.get('current_question_idx', 0)
    else:
        session['current_question_idx'] = q_idx
    
    q_idx = max(0, min(q_idx, total_questions - 1))
    session['current_question_idx'] = q_idx
    q_id = question_ids[q_idx]
    question = question_lookup[q_id]
    answers = test_data.get('answers', {})
    
    # CRITICAL FIX: Use integer key for answers
    return render_template('test.html',
        question=question,
        q_idx=q_idx,
        total_questions=total_questions,
        answer=answers.get(str(q_id)),  # Changed to str key
        time_limit=test_data['time_limit'])

@app.route('/submit_answer', methods=['POST'])
def submit_answer():
    if 'current_test' not in session:
        return jsonify({'error': 'No active test'}), 400
    
    data = request.json
    question_id = data.get('question_id')
    answer = data.get('answer')
    
    try:
        question_id = str(int(question_id))  # Changed to str key
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question id'}), 400
    
    session['current_test']['answers'][question_id] = answer
    session.modified = True
    return jsonify({'success': True})

@app.route('/submit_test', methods=['POST'])
def submit_test():
    if 'current_test' not in session:
        return redirect(url_for('index'))
    
    test_data = session['current_test']
    all_questions = load_questions()
    question_lookup = {q['id']: q for q in all_questions}
    question_ids = test_data['question_ids']
    user_answers = test_data['answers']
    
    correct = 0
    total = len(question_ids)
    results_detail = []
    
    # Standard QC options
    QC_OPTIONS = [
        "Quantity A is greater",
        "Quantity B is greater",
        "The two quantities are equal",
        "The relationship cannot be determined from the information given"
    ]
    
    for q_id in question_ids:
        question = question_lookup[q_id]
        correct_spec = question.get('correct')
        user_answer = user_answers.get(str(q_id), '')  # Changed to str key
        is_correct = False
        
        try:
            if question['type'] == 'mc':
                # Convert index to actual option text
                correct_index = int(correct_spec)
                correct_text = question['options'][correct_index]
                is_correct = str(user_answer) == str(correct_text)
            
            elif question['type'] == 'ma':
                # Convert indices to actual option texts
                correct_indices = [int(i) for i in correct_spec]
                correct_texts = [question['options'][i] for i in correct_indices]
                if isinstance(user_answer, list):
                    is_correct = set(user_answer) == set(correct_texts)
                else:
                    is_correct = False
            
            elif question['type'] == 'qc':
                # Handle all QC variants
                expected_index = None
                
                # Case 1: numeric index (0,1,2,3)
                if isinstance(correct_spec, int) and 0 <= correct_spec < 4:
                    expected_index = correct_spec
                # Case 2: letter (A,B,C,D)
                elif isinstance(correct_spec, str) and correct_spec.upper() in "ABCD":
                    expected_index = "ABCD".index(correct_spec.upper())
                # Case 3: full text match
                elif isinstance(correct_spec, str) and correct_spec in QC_OPTIONS:
                    expected_index = QC_OPTIONS.index(correct_spec)
                
                # Get user answer index
                user_index = None
                if isinstance(user_answer, str):
                    if user_answer.upper() in "ABCD":
                        user_index = "ABCD".index(user_answer.upper())
                    elif user_answer in QC_OPTIONS:
                        user_index = QC_OPTIONS.index(user_answer)
                
                is_correct = (expected_index is not None) and (user_index is not None) and (expected_index == user_index)
            
            elif question['type'] == 'numeric':
                # Numeric comparison with tolerance
                try:
                    user_num = float(user_answer)
                    correct_num = float(correct_spec)
                    is_correct = abs(user_num - correct_num) < 1e-6
                except (ValueError, TypeError):
                    is_correct = str(user_answer) == str(correct_spec)
            
            else:
                is_correct = str(user_answer) == str(correct_spec)
        
        except Exception as e:
            print(f"Scoring error for question {q_id}: {str(e)}")
            is_correct = False
        
        if is_correct:
            correct += 1
        
        results_detail.append({
            'question': question,
            'user_answer': user_answer,
            'correct': is_correct
        })
    
    accuracy = (correct / total * 100) if total > 0 else 0
    
    if 'test_history' not in session:
        session['test_history'] = []
    
    session['test_history'].append({
        'date': datetime.now().isoformat(),
        'format': test_data['format'],
        'accuracy': round(accuracy, 1),
        'correct': correct,
        'total': total
    })
    
    session['test_history'] = session['test_history'][-10:]
    
    session['last_results'] = {
        'accuracy': round(accuracy, 1),
        'correct': correct,
        'total': total,
        'details': results_detail
    }
    
    session.pop('current_test', None)
    session.modified = True
    
    return redirect(url_for('results'))

@app.route('/results')
def results():
    if 'last_results' not in session:
        return redirect(url_for('index'))
    
    results = session['last_results']
    history = session.get('test_history', [])
    return render_template('results.html', results=results, history=history)

@app.route('/reset_history', methods=['POST'])
def reset_history():
    session['attempted_questions_bitmap'] = base64.b64encode(b'').decode('ascii')
    session.modified = True
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)