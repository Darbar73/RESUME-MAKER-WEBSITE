import os
import json
import logging
import re
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import google.generativeai as genai
import PyPDF2

# 1. SETUP & CONFIGURATION
load_dotenv()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'fallback-secret-key-123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///career_ai.db')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB Max file size

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database & Login Manager Setup
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'auth_route'

# Gemini AI Key Load
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
else:
    logger.warning("⚠️ GEMINI_API_Key not found.")

# 2. DATABASE MODELS

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100), nullable=False)

class ATSScan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    score = db.Column(db.Integer)
    data = db.Column(db.Text) # AI response as JSON string
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Resume(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    full_name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    linkedin = db.Column(db.String(200))
    summary = db.Column(db.Text)
    skills = db.Column(db.Text)
    experience = db.Column(db.Text)
    education = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

# 3. SMART HELPER FUNCTIONS

def extract_text_from_pdf(filepath):
    try:
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
    except Exception as e:
        logger.error(f"PDF Error: {e}")
        return ""

def analyze_with_gemini(resume_text, job_desc):
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        
        prompt = f"""
        Act as a strict Senior Technical Recruiter & ATS System.
        Analyze this resume against the JD.
        
        Strictly check for:
        1. Grammar/Spelling Errors.
        2. Missing Keywords.
        3. Formatting Issues.
        
        Output PURE JSON:
        {{
            "score": <0-100>,
            "summary": "<Short strict feedback>",
            "grammar_errors": ["<error 1>", "<error 2>"],
            "missing_keywords": ["<keyword 1>", "<keyword 2>"],
            "improvements": ["<actionable advice 1>", "<advice 2>"]
        }}

        RESUME: {resume_text[:4000]}
        JOB DESCRIPTION: {job_desc[:1000]}
        """
        
        response = model.generate_content(prompt)
        # Cleaning JSON string
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_json)
        
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return {
            "score": 0, 
            "summary": f"AI Analysis Failed. Error: {str(e)}",
            "grammar_errors": [],
            "missing_keywords": [],
            "improvements": ["Check API Key or Internet connection."]
        }

# --- Smart Text Formatters ---
def smart_format_text(text):
    if not text: return ""
    # 1. Title Case (First letter capital)
    text = text.strip().title()
    # 2. Uni -> University fix
    text = text.replace(" Uni ", " University ").replace(" Uni", " University")
    return text

def smart_format_skills(text):
    if not text: return ""
    # Comma ya Space se todkar wapas comma se jodna
    parts = re.split(r'[,\s]+', text)
    clean_parts = [p.strip().title() for p in parts if p.strip()]
    return ", ".join(clean_parts)

# 4. ROUTES (URLS)

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('auth_route'))

# LOGIN / SIGNUP
@app.route('/auth', methods=['GET', 'POST'])
def auth_route():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form.get('email')
        password = request.form.get('password')

        if action == 'signup':
            if User.query.filter_by(email=email).first():
                flash('Email already registered!', 'danger')
            else:
                hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
                new_user = User(name=request.form.get('name'), email=email, password_hash=hashed_pw)
                db.session.add(new_user)
                db.session.commit()
                login_user(new_user)
                return redirect(url_for('dashboard'))
        
        elif action == 'login':
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                login_user(user)
                return redirect(url_for('dashboard'))
            else:
                flash('Login Failed. Check email or password.', 'danger')

    return render_template('auth.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth_route'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Fetch recent history
    scans = ATSScan.query.filter_by(user_id=current_user.id).order_by(ATSScan.created_at.desc()).limit(5).all()
    resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).limit(5).all()
    
    # Parse JSON data for display
    scan_data = []
    for scan in scans:
        try:
            data = json.loads(scan.data)
            scan_data.append({'scan': scan, 'summary': data.get('summary', 'No summary')})
        except:
            scan_data.append({'scan': scan, 'summary': 'Error loading data'})

    return render_template('dashboard.html', user=current_user, scans=scan_data, resumes=resumes)

# ATS SCANNER
@app.route('/tools/ats', methods=['GET', 'POST'])
@login_required
def ats_tool():
    result = None
    
    if request.method == 'POST':
        if 'resume' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
            
        file = request.files['resume']
        jd = request.form.get('jd')

        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)

        if file and jd:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            text = extract_text_from_pdf(filepath)
            
            if not text:
                flash('Could not read PDF. Make sure it contains text.', 'warning')
            else:
                result = analyze_with_gemini(text, jd)
                try:
                    new_scan = ATSScan(
                        user_id=current_user.id,
                        score=result.get('score', 0),
                        data=json.dumps(result)
                    )
                    db.session.add(new_scan)
                    db.session.commit()
                except Exception as e:
                    logger.error(f"DB Error: {e}")

            try:
                os.remove(filepath)
            except:
                pass

    return render_template('tools/ats.html', result=result)

# RESUME BUILDER (Smart Version)
@app.route('/tools/resume-builder', methods=['GET', 'POST'])
@login_required
def resume_builder():
    if request.method == 'POST':
        # 1. Personal Details Formatting
        full_name = smart_format_text(request.form.get('full_name'))
        
        # 2. Education Split & Combine
        degree = smart_format_text(request.form.get('edu_degree'))
        college = smart_format_text(request.form.get('edu_college'))
        year = request.form.get('edu_year')
        grade = request.form.get('edu_grade')
        
        # Formatting Education String for PDF
        formatted_education = f"{degree}\n{college}\nYear: {year} | Grade: {grade}"

        # 3. Skills Auto-Comma
        raw_skills = request.form.get('skills')
        formatted_skills = smart_format_skills(raw_skills)

        experience = request.form.get('experience')

        # 4. AI Auto-Summary (If empty)
        summary = request.form.get('summary')
        if not summary or summary.strip() == "":
            try:
                model = genai.GenerativeModel('gemini-flash-latest')
                prompt = f"""
                Write a professional 3-line resume summary for a fresher named {full_name}.
                Skills: {formatted_skills}
                Education: {degree} from {college}
                Keep it impactful and professional.
                """
                ai_resp = model.generate_content(prompt)
                summary = ai_resp.text.strip()
            except:
                summary = "Passionate student looking for opportunities to apply skills and grow."

        # 5. Save to DB
        new_resume = Resume(
            user_id=current_user.id,
            full_name=full_name,
            email=request.form.get('email'),
            phone=request.form.get('phone'),
            linkedin=request.form.get('linkedin'),
            summary=summary,
            skills=formatted_skills,
            experience=experience,
            education=formatted_education
        )
        db.session.add(new_resume)
        db.session.commit()
        
        return redirect(url_for('resume_view', id=new_resume.id))
        
    return render_template('tools/resume_builder.html')

@app.route('/tools/resume/<int:id>')
@login_required
def resume_view(id):
    resume = Resume.query.get_or_404(id)
    if resume.user_id != current_user.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('tools/resume_view.html', resume=resume)

# MAIN RUNNER
if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Tables banayega agar nahi hain
    app.run(debug=True)