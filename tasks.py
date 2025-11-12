import os
import json
from datetime import datetime

from app import app, db, send_email
from app import Application, Job, Candidate

# This module is imported by the RQ worker (run: `rq worker --url $REDIS_URL default`)
# The worker must run in the same project where `app` and models are defined.

def send_bulk_invites(job_id):
    """Background job: send interview invites to all shortlisted candidates for a job.

    This function runs inside an RQ worker process. It uses the Flask app context
    to access SQLAlchemy and the send_email() helper defined in `app.py`.
    """
    with app.app_context():
        job = Job.query.get(job_id)
        if not job:
            print(f"send_bulk_invites: job {job_id} not found")
            return {'status': 'error', 'reason': 'job_not_found'}

        applications = Application.query.filter_by(job_id=job_id, status='Shortlisted').all()
        results = []
        for application in applications:
            try:
                candidate = Candidate.query.get(application.candidate_id)
                interview_link = app.test_request_context().push() or ''
                # build interview link using url_for with _external disabled in worker
                interview_link = f"{os.getenv('WEBAPP_URL','')}/interview/{application.id}"
                subject = f"Interview Invitation for the {job.title} role"
                body = (
                    f"Dear {candidate.name},\n\n"
                    f"Congratulations! Your application for the {job.title} position has been shortlisted.\n"
                    f"Please use the following link to complete your AI-proctored virtual interview:\n{interview_link}\n\n"
                    f"Best of luck!\nThe {job.admin.company_name} Hiring Team"
                )
                send_email(candidate.email, subject, body)
                application.status = 'Invited'
                db.session.add(application)
                db.session.commit()
                results.append({'application_id': application.id, 'email': candidate.email, 'status': 'sent'})
            except Exception as e:
                print(f"send_bulk_invites: failed to send to application {application.id}: {e}")
                # keep going with other applications
                results.append({'application_id': application.id, 'error': str(e)})
        return {'status': 'completed', 'sent': len([r for r in results if r.get('status')=='sent']), 'results': results}
