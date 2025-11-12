import os
import io
import json
from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai
import PyPDF2
import docx
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import navy, black, red
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# --- App Configuration ---
load_dotenv()

from datetime import datetime
from urllib.parse import urlparse

app = Flask(__name__)

@app.route('/health')
def health_check():
    """Health check endpoint for Render"""
    try:
        # Verify database connection
        db.session.execute(text('SELECT 1'))
        db.session.commit()
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'database_url': app.config['SQLALCHEMY_DATABASE_URI'].split('@')[1] if '@' in app.config['SQLALCHEMY_DATABASE_URI'] else 'local',
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'database': str(e),
            'database_url': app.config['SQLALCHEMY_DATABASE_URI'].split('@')[1] if '@' in app.config['SQLALCHEMY_DATABASE_URI'] else 'local',
            'timestamp': datetime.utcnow().isoformat()
        }), 500

@app.route('/api/debug/email_config')
def debug_email_config():
    """Diagnostic endpoint: check email provider configuration (no secrets exposed)"""
    resend_present = bool(os.getenv('RESEND_API_KEY'))
    resend_client_ready = resend_client is not None
    mail_sender = app.config.get('MAIL_DEFAULT_SENDER', 'NOT SET')
    smtp_server = app.config.get('MAIL_SERVER', 'NOT SET')
    smtp_port = app.config.get('MAIL_PORT', 'NOT SET')
    
    return jsonify({
        'timestamp': datetime.utcnow().isoformat(),
        'resend_api_key_present': resend_present,
        'resend_client_initialized': resend_client_ready,
        'mail_default_sender': mail_sender,
        'smtp_server': smtp_server,
        'smtp_port': smtp_port,
        'primary_method': 'Resend API' if resend_client_ready else 'SMTP fallback' if smtp_server != 'NOT SET' else 'NONE - email may fail'
    })


@app.route('/api/debug/db_config')
def debug_db_config():
    """Diagnostic endpoint to inspect DATABASE_URL host and DNS resolution.
    Does not expose credentials.
    """
    database_url = os.getenv('DATABASE_URL') or app.config.get('SQLALCHEMY_DATABASE_URI')
    if not database_url:
        return jsonify({'error': 'DATABASE_URL not set in environment or app config.'}), 500

    try:
        parsed = urlparse(database_url)
        host = parsed.hostname
        port = parsed.port
        dbname = parsed.path[1:] if parsed.path.startswith('/') else parsed.path
        # Try to resolve hostname to IP(s)
        import socket
        try:
            addrs = socket.getaddrinfo(host, port or 5432)
            resolved = sorted(list({a[4][0] for a in addrs}))
            dns_ok = True
        except Exception as e:
            resolved = []
            dns_ok = False

        return jsonify({
            'database_url_present': True,
            'host': host,
            'port': port,
            'dbname': dbname,
            'dns_resolves': dns_ok,
            'resolved_addresses': resolved,
            'note': 'This endpoint does NOT expose credentials. If dns_resolves is false, check DATABASE_URL in Render and that the DB service is linked.'
        })
    except Exception as e:
        return jsonify({'error': f'Failed to parse DATABASE_URL: {str(e)}'}), 500

# Flask configuration
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY", os.urandom(24))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file upload
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour session timeout

REPORT_FOLDER = 'reports'
os.makedirs(REPORT_FOLDER, exist_ok=True)

# --- Database Configuration ---
def get_database_url():
    """Get database URL with fallback for development"""
    database_url = os.getenv('DATABASE_URL')
    
    # Allow local fallback only in explicit development mode
    flask_debug = os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 'on']
    flask_env = os.getenv('FLASK_ENV', '').lower()

    if not database_url:
        if flask_debug or flask_env == 'development':
            # Development fallback
            print("WARNING: No DATABASE_URL found. Using default local database for development.")
            return 'postgresql://postgres:postgres@localhost:5432/hiring_platform'
        # In production, fail fast so Render doesn't try to connect to localhost
        raise RuntimeError(
            "DATABASE_URL environment variable is missing. In production this must be set. "
            "On Render, ensure the DATABASE_URL env var is configured and the database service is linked."
        )

    # Handle Render's DATABASE_URL format (postgres:// -> postgresql://)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    # Validate host: prevent accidental localhost DB usage in production
    parsed = urlparse(database_url)
    host = parsed.hostname
    if host in ("localhost", "127.0.0.1", "::1") and not (flask_debug or flask_env == 'development'):
        raise RuntimeError(
            "DATABASE_URL resolves to a localhost address in a non-development environment. "
            "On Render this usually means the DATABASE_URL env var was not populated or was set incorrectly."
        )

    print(f"Database host detected: {host if host else 'unknown'}")
    return database_url

# Configure SQLAlchemy with better connection handling
app.config['SQLALCHEMY_DATABASE_URI'] = get_database_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,         # Enable connection health checks
    'pool_recycle': 300,           # Recycle connections every 5 minutes
    'pool_timeout': 30,            # Wait up to 30 seconds for a connection
    'max_overflow': 10,            # Allow up to 10 extra connections
    'connect_args': {
        'connect_timeout': 10,      # Connection timeout in seconds
        'application_name': 'interview-platform'  # Identify app in pg_stat_activity
    }
}

# Initialize SQLAlchemy with better error handling
try:
    print("Initializing database connection...")
    db = SQLAlchemy(app)
    print("Database initialization successful")
except Exception as e:
    print(f"Error initializing database: {str(e)}")
    raise

# Import the text function for raw SQL
from sqlalchemy import text

# Verify database connection on startup
with app.app_context():
    try:
        db.session.execute(text('SELECT 1'))
        db.session.commit()
        print("Database connection test successful!")
    except Exception as e:
        print(f"Database connection test failed: {str(e)}")
        raise


# --- Email Configuration (Resend API only) ---
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@example.com')

# Initialize Resend client (try SDK first, fallback to lightweight HTTP client)
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
resend_client = None
if RESEND_API_KEY:
    try:
        # Preferred: official Resend Python SDK (if available)
        from resend import Resend
        resend_client = Resend(RESEND_API_KEY)
        print("✓ Resend SDK client initialized successfully")
    except Exception as e:
        print(f"⚠ Resend SDK import failed: {e}; falling back to HTTP API via requests")
        # Fallback: simple HTTP client using requests so we don't depend on SDK shape
        try:
            import requests

            class SimpleResendClient:
                def __init__(self, api_key):
                    self.api_key = api_key
                    # mimic SDK surface: `.emails.send(...)`
                    self.emails = self

                def send(self, *args, **kwargs):
                    # Accept positional or keyword args used by various SDK versions
                    to = kwargs.get('to') or (args[0] if len(args) > 0 else None)
                    from_ = kwargs.get('from_') or kwargs.get('from') or (args[1] if len(args) > 1 else None)
                    subject = kwargs.get('subject') or (args[2] if len(args) > 2 else None)
                    html = kwargs.get('html') or kwargs.get('text') or (args[3] if len(args) > 3 else None)

                    if not (to and from_ and subject and html):
                        raise ValueError('Missing required email fields for Resend HTTP API')

                    payload = {
                        'from': from_,
                        'to': [to],
                        'subject': subject,
                        'html': html
                    }

                    resp = requests.post(
                        'https://api.resend.com/emails',
                        headers={
                            'Authorization': f'Bearer {self.api_key}',
                            'Content-Type': 'application/json'
                        },
                        json=payload,
                        timeout=15
                    )
                    try:
                        resp.raise_for_status()
                        return {'status_code': resp.status_code, 'body': resp.text}
                    except requests.exceptions.HTTPError as http_err:
                        # Attach response text for easier debugging upstream
                        content = resp.text
                        raise RuntimeError(f"Resend HTTP {resp.status_code} error: {content}") from http_err

            resend_client = SimpleResendClient(RESEND_API_KEY)
            print('✓ Resend HTTP client configured')
        except Exception as e2:
            print(f"⚠ Failed to configure fallback Resend HTTP client: {e2}")
else:
    print("⚠ RESEND_API_KEY not set — email sending will fail")

def send_email(to_email, subject, body, html_body=None):
    """Send an email via Resend API (HTTPS-based, reliable on Render).
    Requires RESEND_API_KEY env var to be set.
    """
    if not resend_client:
        raise RuntimeError(
            'Resend API not configured. Set RESEND_API_KEY environment variable. '
            'Get it from https://resend.com/api-keys'
        )
    
    sender = app.config.get('MAIL_DEFAULT_SENDER')
    if not sender:
        raise RuntimeError('MAIL_DEFAULT_SENDER environment variable is not set')
    
    try:
        # Convert plain text to HTML if not provided
        payload_html = html_body if html_body else (body.replace('\n', '<br/>'))
        
        print(f"Sending email via Resend: to={to_email}, from={sender}")
        resp = resend_client.emails.send(
            to=to_email,
            from_=sender,
            subject=subject,
            html=payload_html
        )
        print(f"Email sent successfully via Resend: {resp}")
        return True
    except Exception as e:
        print(f"Resend API error: {e}")
        import traceback
        traceback.print_exc()
        raise

# --- Database Models ---
class Admin(db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20))
    password = db.Column(db.String(255), nullable=False)
    jobs = db.relationship('Job', backref='admin', lazy=True, cascade='all, delete-orphan')

class Candidate(db.Model):
    __tablename__ = 'candidates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    applications = db.relationship('Application', backref='candidate', lazy=True, cascade='all, delete-orphan')

class Job(db.Model):
    __tablename__ = 'jobs'
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admins.id'), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    applications = db.relationship('Application', backref='job', lazy=True, cascade='all, delete-orphan')

class Application(db.Model):
    __tablename__ = 'applications'
    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey('candidates.id'), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey('jobs.id'), nullable=False, index=True)
    resume_text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), nullable=False, default='Applied', index=True)
    shortlist_reason = db.Column(db.Text)
    report_path = db.Column(db.String(500))
    interview_results = db.Column(db.Text)
    
    # Add unique constraint to prevent duplicate applications
    __table_args__ = (db.UniqueConstraint('candidate_id', 'job_id', name='unique_application'),)

# Create database tables with retry logic
def init_db(retries=5, delay=2):
    import time
    for attempt in range(retries):
        try:
            with app.app_context():
                db.create_all()
                print("Database tables created successfully!")
                return
        except Exception as e:
            if attempt + 1 == retries:
                print(f"Failed to create database tables after {retries} attempts: {e}")
                raise
            print(f"Database initialization attempt {attempt + 1} failed, retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= 2  # Exponential backoff

# Initialize database
init_db()

# --- Gemini API Configuration ---
try:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: raise ValueError("GEMINI_API_KEY not found.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-flash-latest')
except Exception as e:
    print(f"FATAL: Error configuring Gemini API: {e}")
    model = None

# ==============================================================================
# TEMPLATE RENDERING & CORE ROUTES
# ==============================================================================
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/dashboard')
def admin_dashboard():
    if session.get('user_type') != 'admin': return redirect(url_for('index'))
    return render_template('admin_dashboard.html')

@app.route('/candidate/dashboard')
def candidate_dashboard():
    if session.get('user_type') != 'candidate': return redirect(url_for('index'))
    return render_template('candidate_dashboard.html')

@app.route('/interview/<int:application_id>')
def interview_page(application_id):
    app_data = db.session.query(Job.title).join(Application).filter(Application.id == application_id).first()
    if not app_data: return "Interview link is invalid or has expired.", 404
    return render_template('interview.html', job_title=app_data[0], application_id=application_id)

# ==============================================================================
# AUTHENTICATION API
# ==============================================================================
@app.route('/api/register/admin', methods=['POST'])
def register_admin():
    data = request.json
    
    # Input validation
    if not data:
        return jsonify({'error': 'No data provided.'}), 400
    
    required_fields = ['company_name', 'email', 'password']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required.'}), 400
    
    # Email validation
    email = data['email'].strip().lower()
    if '@' not in email or len(email) < 5:
        return jsonify({'error': 'Invalid email format.'}), 400
    
    # Password strength check
    password = data['password']
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400
    
    try:
        admin = Admin(
            company_name=data['company_name'].strip(),
            email=email,
            phone=data.get('phone', '').strip(),
            password=generate_password_hash(password)
        )
        db.session.add(admin)
        db.session.commit()
        return jsonify({'message': 'Registration successful.'})
    except Exception as e:
        db.session.rollback()
        if 'unique constraint' in str(e).lower() or 'duplicate' in str(e).lower():
            return jsonify({'error': 'Email already exists.'}), 409
        print(f"Registration error: {e}")
        return jsonify({'error': 'Registration failed. Please try again.'}), 500

@app.route('/api/login/admin', methods=['POST'])
def login_admin():
    data = request.json
    admin = Admin.query.filter_by(email=data['email']).first()
    if admin and check_password_hash(admin.password, data['password']):
        session['user_type'] = 'admin'
        session['admin_id'] = admin.id
        session['company_name'] = admin.company_name
        return jsonify({'message': 'Login successful.', 'company_name': admin.company_name})
    return jsonify({'error': 'Invalid credentials.'}), 401
    
@app.route('/api/register/candidate', methods=['POST'])
def register_candidate():
    data = request.json
    
    # Input validation
    if not data:
        return jsonify({'error': 'No data provided.'}), 400
    
    required_fields = ['name', 'email', 'password']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required.'}), 400
    
    # Email validation
    email = data['email'].strip().lower()
    if '@' not in email or len(email) < 5:
        return jsonify({'error': 'Invalid email format.'}), 400
    
    # Password strength check
    password = data['password']
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400
    
    try:
        candidate = Candidate(
            name=data['name'].strip(),
            email=email,
            password=generate_password_hash(password)
        )
        db.session.add(candidate)
        db.session.commit()
        return jsonify({'message': 'Registration successful.'})
    except Exception as e:
        db.session.rollback()
        if 'unique constraint' in str(e).lower() or 'duplicate' in str(e).lower():
            return jsonify({'error': 'Email already exists.'}), 409
        print(f"Registration error: {e}")
        return jsonify({'error': 'Registration failed. Please try again.'}), 500

@app.route('/api/login/candidate', methods=['POST'])
def login_candidate():
    data = request.json
    candidate = Candidate.query.filter_by(email=data['email']).first()
    if candidate and check_password_hash(candidate.password, data['password']):
        session['user_type'] = 'candidate'
        session['candidate_id'] = candidate.id
        session['candidate_name'] = candidate.name
        return jsonify({'message': 'Login successful.'})
    return jsonify({'error': 'Invalid credentials.'}), 401

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/check_session')
def check_session():
    if session.get('user_type') == 'admin':
        return jsonify({'logged_in': True, 'user_type': 'admin', 'company_name': session.get('company_name')})
    if session.get('user_type') == 'candidate':
        return jsonify({'logged_in': True, 'user_type': 'candidate', 'candidate_name': session.get('candidate_name')})
    return jsonify({'logged_in': False})

# ==============================================================================
# ADMIN API
# ==============================================================================
@app.route('/api/admin/jobs')
def get_admin_jobs():
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 401
    
    jobs = Job.query.filter_by(admin_id=session['admin_id']).order_by(Job.id.desc()).all()
    data = []
    for job in jobs:
        job_dict = {
            'id': job.id,
            'title': job.title,
            'description': job.description,
            'admin_id': job.admin_id
        }
        applications = db.session.query(
            Application.id, Application.status, 
            Candidate.name, Candidate.email, 
            Application.report_path
        ).join(Candidate).filter(Application.job_id == job.id).all()
        job_dict['applications'] = [
            {
                'id': app[0],
                'status': app[1],
                'name': app[2],
                'email': app[3],
                'report_path': app[4]
            } for app in applications
        ]
        data.append(job_dict)
    return jsonify(data)

@app.route('/api/admin/create_job', methods=['POST'])
def create_job():
    print("\n=== Create Job Endpoint Called ===")
    print(f"Session data: {dict(session)}")
    
    if session.get('user_type') != 'admin':
        print(f"Unauthorized access attempt. User type: {session.get('user_type')}")
        return jsonify({'error': 'Unauthorized. Please log in as admin.'}), 401
    
    if 'admin_id' not in session:
        print("No admin_id in session")
        return jsonify({'error': 'Session expired. Please log in again.'}), 401
    
    try:
        if not request.is_json:
            print("Request is not JSON")
            return jsonify({'error': 'Invalid request format. Expected JSON.'}), 400
        
        data = request.json
        print(f"Received job data: {data}")
        print(f"Admin ID from session: {session.get('admin_id')}")
        
        if not data.get('title') or not data.get('description'):
            print("Missing required fields")
            return jsonify({'error': 'Title and description are required.'}), 400
        
        job = Job(
            admin_id=session['admin_id'],
            title=data['title'],
            description=data['description']
        )
        
        print("Adding job to session...")
        db.session.add(job)
        print("Committing to database...")
        db.session.commit()
        print(f"Job created successfully with ID: {job.id}")
        
        return jsonify({
            'message': 'Job created successfully.',
            'job_id': job.id
        })
    except Exception as e:
        print(f"Error creating job: {str(e)}")
        db.session.rollback()
        return jsonify({'error': f'Failed to create job: {str(e)}'}), 500
    finally:
        print("=== End Create Job Endpoint ===\n")
    
@app.route('/api/admin/shortlist/<int:job_id>', methods=['POST'])
def shortlist_candidates(job_id):
    if session.get('user_type') != 'admin': 
        return jsonify({'error': 'Unauthorized'}), 401
    
    job = Job.query.filter_by(id=job_id, admin_id=session['admin_id']).first()
    if not job: 
        return jsonify({'error': 'Job not found'}), 404
    
    applications = Application.query.filter_by(job_id=job_id, status='Applied').all()
    if not applications: 
        return jsonify({'message': 'No new applications to shortlist.'})
    
    if not model:
        return jsonify({'error': 'AI model not configured. Cannot perform shortlisting.'}), 500

    shortlisted_count = 0
    rejected_count = 0
    
    for app in applications:
        prompt = f"""Analyze if the candidate's resume is a good fit for the job description.
Provide a JSON response with exactly two keys: "shortlisted" (boolean) and "reason" (a brief explanation in 1-2 sentences).

**Job Description:**
{job.description[:1000]}

**Candidate Resume:**
{app.resume_text[:2000]}

Return only valid JSON, no markdown formatting."""
        
        try:
            response = model.generate_content(prompt)
            cleaned_text = response.text.strip().replace('```json', '').replace('```', '').strip()
            result = json.loads(cleaned_text)
            
            if result.get('shortlisted', False):
                app.status = 'Shortlisted'
                app.shortlist_reason = result.get('reason', 'Candidate profile matches job requirements.')
                shortlisted_count += 1
            else:
                app.status = 'Rejected'
                app.shortlist_reason = result.get('reason', 'Profile does not match requirements.')
                rejected_count += 1
                
        except json.JSONDecodeError as e:
            print(f"JSON decode error for application {app.id}: {e}")
            # Keep as Applied if AI fails
        except Exception as e:
            print(f"Error shortlisting application {app.id}: {e}")
            # Keep as Applied if AI fails

    db.session.commit()
    return jsonify({
        'message': f'Shortlisting complete.',
        'total_processed': len(applications),
        'shortlisted': shortlisted_count,
        'rejected': rejected_count
    })

@app.route('/api/admin/send_invite/<int:application_id>', methods=['POST'])
def send_invite(application_id):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 401
    
    # Explicitly select from Application and join Candidate and Job to avoid ambiguity
    app_data = db.session.query(
        Candidate.email,
        Job.title
    ).select_from(Application).join(Candidate).join(Job).filter(Application.id == application_id).first()
    
    if not app_data: return jsonify({'error': 'Application not found.'}), 404
    
    interview_link = url_for('interview_page', application_id=application_id, _external=True)
    subject = f"🎉 Interview Invitation - {app_data.title} at {session['company_name']}"
    
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9fafb;">
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px 10px 0 0; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 28px;">🎉 Congratulations!</h1>
        </div>
        
        <div style="background-color: white; padding: 30px; border-radius: 0 0 10px 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <p style="font-size: 16px; color: #374151; line-height: 1.6;">Dear Candidate,</p>
            
            <p style="font-size: 16px; color: #374151; line-height: 1.6;">
                Great news! Your application for the <strong>{app_data.title}</strong> position at <strong>{session['company_name']}</strong> has been shortlisted.
            </p>
            
            <p style="font-size: 16px; color: #374151; line-height: 1.6;">
                We're excited to invite you to the next stage: an AI-proctored virtual interview.
            </p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{interview_link}" 
                   style="display: inline-block; background-color: #4f46e5; color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px;">
                    Start Interview
                </a>
            </div>
            
            <div style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 20px 0; border-radius: 4px;">
                <p style="margin: 0; color: #92400e; font-size: 14px;">
                    <strong>⚠️ Important:</strong> Please ensure you have a working camera and microphone. The interview is proctored and requires your full attention.
                </p>
            </div>
            
            <p style="font-size: 14px; color: #6b7280; line-height: 1.6;">
                Best of luck!<br>
                <strong>The {session['company_name']} Hiring Team</strong>
            </p>
        </div>
        
        <div style="text-align: center; padding: 20px; color: #9ca3af; font-size: 12px;">
            <p>This is an automated email from AI Interview Platform</p>
        </div>
    </div>
    """
    
    try:
        send_email(app_data.email, subject, body=None, html_body=html_body)
        application = Application.query.get(application_id)
        application.status = 'Invited'
        db.session.commit()
        return jsonify({'message': 'Interview invitation sent.'})
    except Exception as e:
        print(f"MAIL SENDING ERROR: {e}")
        return jsonify({'error': f'Failed to send email: {str(e)}. Ensure MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD are configured.'}), 500

@app.route('/api/admin/update_status/<int:application_id>', methods=['POST'])
def update_status(application_id):
    if session.get('user_type') != 'admin': return jsonify({'error': 'Unauthorized'}), 401
    
    if not request.is_json:
        return jsonify({'error': 'Invalid request: Content-Type must be application/json.'}), 415

    data = request.get_json()
    status = data.get('status')
    if status not in ['Accepted', 'Rejected']: 
        return jsonify({'error': 'Invalid status provided in request body.'}), 400
    
    app_data = db.session.query(
        Candidate.email,
        Job.title,
        Application.report_path
    ).join(Application).join(Job).filter(Application.id == application_id).first()
    if not app_data: return jsonify({'error': 'Application not found.'}), 404

    try:
        if status == 'Accepted':
            subject = f"✅ Congratulations! Next Steps for {app_data.title}"
            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9fafb;">
                <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 30px; border-radius: 10px 10px 0 0; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 28px;">✅ You're Moving Forward!</h1>
                </div>
                
                <div style="background-color: white; padding: 30px; border-radius: 0 0 10px 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <p style="font-size: 16px; color: #374151; line-height: 1.6;">Dear Candidate,</p>
                    
                    <p style="font-size: 16px; color: #374151; line-height: 1.6;">
                        Congratulations! We're impressed with your interview performance for the <strong>{app_data.title}</strong> position.
                    </p>
                    
                    <p style="font-size: 16px; color: #374151; line-height: 1.6;">
                        We would like to invite you to our office for the next round of interviews. Our team will contact you shortly with the details.
                    </p>
                    
                    <div style="background-color: #d1fae5; border-left: 4px solid #10b981; padding: 15px; margin: 20px 0; border-radius: 4px;">
                        <p style="margin: 0; color: #065f46; font-size: 14px;">
                            <strong>🎯 Next Steps:</strong> Keep an eye on your email for scheduling details.
                        </p>
                    </div>
                    
                    <p style="font-size: 14px; color: #6b7280; line-height: 1.6;">
                        Looking forward to meeting you!<br>
                        <strong>The {session['company_name']} Hiring Team</strong>
                    </p>
                </div>
            </div>
            """
            send_email(app_data.email, subject, body=None, html_body=html_body)
        
        application = Application.query.get(application_id)
        application.status = status
        db.session.commit()
        return jsonify({'message': f'Candidate status updated to {status}.'})
    except Exception as e:
        print(f"MAIL SENDING ERROR: {e}")
        return jsonify({'error': f'Failed to send email: {str(e)}. Ensure MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD are configured.'}), 500

@app.route('/api/download_report/<int:application_id>')
def download_report(application_id):
    if 'admin_id' not in session: 
        return jsonify({'error': 'Unauthorized'}), 401
    
    report = db.session.query(Application.report_path).join(Job).filter(
        Application.id == application_id,
        Job.admin_id == session['admin_id']
    ).first()
    
    if not report or not report.report_path:
        return jsonify({'error': 'Report not found.'}), 404
    
    # Security: Ensure the path is within REPORT_FOLDER
    report_path = os.path.abspath(report.report_path)
    report_folder_abs = os.path.abspath(REPORT_FOLDER)
    
    if not report_path.startswith(report_folder_abs):
        print(f"Security: Attempted path traversal - {report_path}")
        return jsonify({'error': 'Invalid report path.'}), 403
    
    if not os.path.exists(report_path):
        return jsonify({'error': 'Report file not found.'}), 404
    
    try:
        with open(report_path, 'rb') as f:
            pdf_data = f.read()
        
        return Response(
            pdf_data,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment;filename=report_application_{application_id}.pdf',
                'Content-Length': len(pdf_data)
            }
        )
    except Exception as e:
        print(f"Error reading report file: {e}")
        return jsonify({'error': 'Failed to read report.'}), 500

# ==============================================================================
# CANDIDATE API & SHARED HELPERS
# ==============================================================================
@app.route('/api/jobs')
def get_jobs():
    if session.get('user_type') != 'candidate': return jsonify({'error': 'Unauthorized'}), 401
    
    jobs = db.session.query(
        Job.id,
        Job.title,
        Job.description,
        Admin.company_name
    ).join(Admin).order_by(Job.id.desc()).all()
    
    return jsonify([{
        'id': job.id,
        'title': job.title,
        'description': job.description,
        'company_name': job.company_name
    } for job in jobs])

@app.route('/api/apply/<int:job_id>', methods=['POST'])
def apply_to_job(job_id):
    if session.get('user_type') != 'candidate': return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    
    existing = Application.query.filter_by(
        candidate_id=session['candidate_id'],
        job_id=job_id
    ).first()
    
    if existing:
        return jsonify({'error': 'You have already applied to this job.'}), 409
    
    application = Application(
        candidate_id=session['candidate_id'],
        job_id=job_id,
        resume_text=data['resume_text']
    )
    db.session.add(application)
    db.session.commit()
    return jsonify({'message': 'Application submitted successfully.'})
    
@app.route('/api/candidate/applications')
def get_candidate_applications():
    if session.get('user_type') != 'candidate': return jsonify({'error': 'Unauthorized'}), 401
    
    # Explicitly select from Application to avoid ambiguous joins
    applications = db.session.query(
        Application.id,
        Application.status,
        Application.report_path,
        Job.title,
        Admin.company_name
    ).select_from(Application).join(Job).join(Admin).filter(
        Application.candidate_id == session['candidate_id']
    ).order_by(Application.id.desc()).all()
    
    return jsonify([{
        'id': app.id,
        'status': app.status,
        'report_path': app.report_path,
        'title': app.title,
        'company_name': app.company_name
    } for app in applications])
    
def generate_questions_for_job(job_description, skills):
    """Generate interview questions using AI with fallback to default questions"""
    
    # Default fallback questions
    default_questions = [
        "Could you please tell me about your relevant experience?",
        "What is your biggest strength and how does it apply to this role?",
        "Describe a challenging project you worked on and how you overcame obstacles.",
        "Why are you interested in this position?",
        "Where do you see yourself in 5 years?"
    ]
    
    if not model:
        print("AI model not configured, using default questions")
        return {"questions": default_questions}
    
    try:
        prompt = f"""Act as an expert technical hiring manager. Generate 5 targeted interview questions based on the job requirements and candidate's background.

**Job Requirements:**
{job_description}

**Candidate's Skills:**
{skills}

Provide a valid JSON response with a key "questions" containing an array of exactly 5 interview question strings. Make questions specific, relevant, and professional."""
        
        response = model.generate_content(prompt)
        cleaned_response_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        result = json.loads(cleaned_response_text)
        
        # Validate response
        if 'questions' in result and isinstance(result['questions'], list) and len(result['questions']) >= 5:
            return {"questions": result['questions'][:5]}
        else:
            print("Invalid AI response format, using default questions")
            return {"questions": default_questions}
            
    except json.JSONDecodeError as e:
        print(f"JSON decode error in question generation: {e}")
        return {"questions": default_questions}
    except Exception as e:
        print(f"Error generating questions: {e}")
        return {"questions": default_questions}

@app.route('/api/start_interview', methods=['POST'])
def start_interview():
    data = request.json
    application_id = data.get('application_id')
    
    app_data = db.session.query(
        Job.description,
        Application.resume_text
    ).join(Job).filter(Application.id == application_id).first()
    if not app_data: 
        return jsonify({'error': 'Invalid interview link.'}), 404
    
    # store interview context in session
    session['application_id'] = application_id
    session['job_requirements'] = app_data.description
    # initialize proctoring counters/flags for tab switching detection
    session['tab_switch_count'] = 0
    session['proctoring_flags'] = []
    session['last_tab_switch_ts'] = None
    
    questions_data = generate_questions_for_job(app_data.description, app_data.resume_text)
    return jsonify(questions_data)


@app.route('/api/proctor/tab_switch', methods=['POST'])
def proctor_tab_switch():
    """Record a tab-switch event. Implements server-side debouncing to ignore rapid repeated events
    from the client (e.g., accidental double-fires). If 3 recorded switches occur, terminate the application.
    """
    if 'application_id' not in session:
        print(f"PROCTOR_EVENT: no session active - ip={request.remote_addr}")
        return jsonify({'error': 'No active interview.'}), 401

    try:
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        # Lightweight server-side logging for debugging/proctor audit
        print(f"PROCTOR_EVENT: application_id={session.get('application_id')} ip={request.remote_addr} time={now.isoformat()} last_ts={session.get('last_tab_switch_ts')} count_before={session.get('tab_switch_count')}")
        # Server-side debounce window (ignore events within 1s)
        last_ts = session.get('last_tab_switch_ts')
        if last_ts:
            last = datetime.fromisoformat(last_ts)
            if now - last < timedelta(seconds=1):
                return jsonify({'message': 'Ignored rapid event.', 'count': session.get('tab_switch_count', 0), 'terminated': False}), 200

        session['last_tab_switch_ts'] = now.isoformat()
        # increment the persistent counter
        session['tab_switch_count'] = session.get('tab_switch_count', 0) + 1
        count = session['tab_switch_count']

        # store a short flag for reporting
        flags = session.get('proctoring_flags', [])
        flags.append(f"Tab switch at {now.isoformat()}")
        session['proctoring_flags'] = flags

        # terminate on threshold
        if count >= 3:
            application = Application.query.get(session['application_id'])
            if application:
                snapshot = json.dumps({'termination_reason': 'Excessive tab switching', 'proctoring_flags': flags})
                application.status = 'Terminated'
                application.interview_results = snapshot
                db.session.commit()
            
            session.clear()
            return jsonify({'message': 'Candidate terminated due to repeated tab switching.', 'terminated': True}), 200

        return jsonify({'message': 'Tab switch recorded.', 'count': count, 'terminated': False}), 200
    except Exception as e:
        print(f"Proctor tab switch error: {e}")
        return jsonify({'error': str(e)}), 500



    
@app.route('/api/extract_text', methods=['POST'])
def extract_text():
    if 'file' not in request.files: return jsonify({'error': 'No file found.'}), 400
    file = request.files['file']
    text = ""
    try:
        if file.filename.endswith('.pdf'):
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file.read()))
            for page in pdf_reader.pages: text += page.extract_text() or ""
        elif file.filename.endswith('.docx'):
            doc = docx.Document(io.BytesIO(file.read()))
            for para in doc.paragraphs: text += para.text + '\n'
        else: return jsonify({'error': 'Unsupported file type.'}), 400
        return jsonify({'text': text})
    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500

@app.route('/api/make_casual', methods=['POST'])
def make_casual_api():
    if not model: return jsonify({'error': 'AI model not configured.'}), 500
    data = request.json; question = data.get('question')
    prompt = f'Rewrite this interview question in a conversational tone: "{question}". Return JSON with key "casual_question".'
    try:
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        return jsonify(json.loads(cleaned_text))
    except Exception: return jsonify({'casual_question': question})

@app.route('/api/score_answer', methods=['POST'])
def score_answer():
    if not model: 
        return jsonify({'error': 'AI model not configured.'}), 500
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided.'}), 400
            
        question = data.get('question', '').strip()
        answer = data.get('answer', '').strip()

        if not question or not answer:
            return jsonify({'error': 'Both question and answer are required.'}), 400
        
        if len(answer) < 10:
            return jsonify({
                'score': 2,
                'feedback': 'Answer is too short. Please provide more detail.'
            })

        prompt = f"""As an expert technical interviewer, evaluate the following answer for the given question.
Provide a score from 0 to 10 (integer) and concise, constructive feedback (2-3 sentences).

Question: "{question[:500]}"
Candidate's Answer: "{answer[:1000]}"

Return ONLY valid JSON with exactly two keys: "score" (integer 0-10) and "feedback" (string).
No markdown formatting."""

        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        result = json.loads(cleaned_text)
        
        # Validate response
        if 'score' not in result or 'feedback' not in result:
            raise ValueError("Invalid AI response format")
        
        # Ensure score is an integer between 0-10
        score = int(result['score'])
        if score < 0 or score > 10:
            score = max(0, min(10, score))
        
        return jsonify({
            'score': score,
            'feedback': result['feedback']
        })
        
    except json.JSONDecodeError as e:
        print(f"JSON decode error in score_answer: {e}")
        return jsonify({
            'score': 5,
            'feedback': 'Unable to evaluate answer at this time. Please continue with the interview.'
        })
    except Exception as e:
        print(f"Error scoring answer: {e}")
        return jsonify({'error': 'Failed to score answer. Please try again.'}), 500

@app.route('/api/generate_final_report', methods=['POST'])
def generate_final_report():
    if 'application_id' not in session: 
        return jsonify({'error': 'Unauthorized. No active interview session.'}), 401
    
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided.'}), 400
            
        interview_results = data.get('interview_results', [])
        proctoring_flags = data.get('proctoring_flags', [])
        application_id = session.get('application_id')
        job_requirements = session.get('job_requirements', 'N/A')
        
        if not interview_results:
            return jsonify({'error': 'No interview results provided.'}), 400

        # Calculate average score
        avg_score = sum(r.get('score', 0) for r in interview_results) / len(interview_results) if interview_results else 0
        
        formatted_results = "\n".join([
            f"Q: {r.get('question', 'N/A')}\nA: {r.get('answer', 'N/A')}\nScore: {r.get('score', 0)}/10\nFeedback: {r.get('feedback', 'N/A')}\n" 
            for r in interview_results
        ])

        # Generate AI scorecard with fallback
        scorecard_data = {
            'overall_summary': f'Candidate completed the interview with an average score of {avg_score:.1f}/10.',
            'strengths': ['Completed all interview questions'],
            'areas_for_improvement': ['Further evaluation recommended'],
            'final_recommendation': 'Review Required'
        }
        
        if model:
            try:
                prompt = f"""Act as a senior hiring manager. Analyze this interview performance and provide a comprehensive evaluation.

**Job Requirements:**
{job_requirements[:1000]}

**Interview Transcript & Evaluation:**
{formatted_results[:3000]}

**Average Score:** {avg_score:.1f}/10

Provide a JSON scorecard with exactly these keys:
- "overall_summary": A 2-3 sentence summary of performance
- "strengths": Array of 2-4 key strengths demonstrated
- "areas_for_improvement": Array of 2-4 areas needing development
- "final_recommendation": One of ["Strongly Recommend", "Recommend", "Consider", "Not Recommended"]

Return only valid JSON, no markdown."""
                
                response = model.generate_content(prompt)
                cleaned_text = response.text.strip().replace('```json', '').replace('```', '').strip()
                ai_scorecard = json.loads(cleaned_text)
                
                # Validate and use AI scorecard
                if all(key in ai_scorecard for key in ['overall_summary', 'strengths', 'areas_for_improvement', 'final_recommendation']):
                    scorecard_data = ai_scorecard
            except Exception as e:
                print(f"Error generating AI scorecard: {e}. Using fallback.")
        
        # --- PDF Generation and Saving ---
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72)
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name='TitleStyle', fontName='Helvetica-Bold', fontSize=24, alignment=TA_CENTER, spaceAfter=20))
        styles.add(ParagraphStyle(name='Heading1Style', fontName='Helvetica-Bold', fontSize=16, spaceBefore=12, spaceAfter=6, textColor=navy))
        styles.add(ParagraphStyle(name='BulletStyle', leftIndent=20, spaceBefore=2))
        styles.add(ParagraphStyle(name='WarningStyle', leftIndent=20, spaceBefore=2, textColor=red))

        story = []
        story.append(Paragraph("Candidate Performance Report", styles['TitleStyle']))
        story.append(Paragraph("Overall Summary", styles['Heading1Style']))
        story.append(Paragraph(scorecard_data.get('overall_summary', 'N/A'), styles['Normal']))
        story.append(Spacer(1, 12))
        story.append(Paragraph("Key Strengths", styles['Heading1Style']))
        for s in scorecard_data.get('strengths', []): story.append(Paragraph(f"• {s}", styles['BulletStyle']))
        story.append(Spacer(1, 12))
        story.append(Paragraph("Areas for Improvement", styles['Heading1Style']))
        for a in scorecard_data.get('areas_for_improvement', []): story.append(Paragraph(f"• {a}", styles['BulletStyle']))
        story.append(Spacer(1, 12))
        story.append(Paragraph("Final Recommendation", styles['Heading1Style']))
        story.append(Paragraph(f"<b>{scorecard_data.get('final_recommendation', 'N/A')}</b>", styles['Normal']))
        
        if proctoring_flags:
            story.append(Spacer(1, 12)); story.append(HRFlowable(width="100%"))
            story.append(Paragraph("Proctoring Flags", styles['Heading1Style']))
            for flag in sorted(list(set(proctoring_flags))): story.append(Paragraph(f"• {flag}", styles['WarningStyle']))
        
        doc.build(story)
        
        report_path = os.path.join(REPORT_FOLDER, f'report_application_{application_id}.pdf')
        with open(report_path, 'wb') as f: f.write(buffer.getvalue())
        
        # Update application with report and results
        application = Application.query.get(application_id)
        if application:
            application.report_path = report_path
            application.status = 'Completed'
            application.interview_results = json.dumps(interview_results)
            db.session.commit()

        session.clear()
        return jsonify({'message': 'Interview submitted successfully.'})
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)