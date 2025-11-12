Resend + RQ worker setup (bulk invites)

This project uses Resend (HTTPS API) for sending transactional emails and RQ + Redis for background jobs.

What I added
- `tasks.py` — contains `send_bulk_invites(job_id)` which sends invites to all shortlisted candidates for a given job.
- `requirements.txt` updated with `rq` and `redis`.
- `/api/admin/send_bulk_invites/<job_id>` endpoint — enqueues the job and returns an RQ job id.

Render setup (recommended)
1. Add Resend env vars to your Web Service (and Worker):
   - RESEND_API_KEY = re_...
   - MAIL_DEFAULT_SENDER = noreply@yourdomain.com (must be verified in Resend)

2. Add a Redis service (recommended: Upstash or managed Redis).
   - If using Upstash, create a Redis instance and copy the `REDIS_URL`.
   - Add `REDIS_URL` to your Web Service and Worker environment variables in Render.

3. Add a Worker service in Render:
   - type: worker
   - name: invite-worker
   - env: python
   - buildCommand: pip install -r requirements.txt
   - startCommand: rq worker --url $REDIS_URL default
   - envVars: set REDIS_URL, RESEND_API_KEY, MAIL_DEFAULT_SENDER, DATABASE_URL (link DB)

Example `render.yaml` snippet for worker (illustrative; add to your existing render.yaml):

  # Worker service for background tasks
  - type: worker
    name: invite-worker
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: rq worker --url $REDIS_URL default
    envVars:
      - key: REDIS_URL
        sync: false
      - key: RESEND_API_KEY
        sync: false
      - key: MAIL_DEFAULT_SENDER
        sync: false
      - key: DATABASE_URL
        fromService:
          type: pserv
          name: interview-db
          property: connectionString
    plan: free

How to trigger bulk invites (from admin UI)
1. Call the endpoint (POST) once you have shortlisted candidates for a job:
   POST /api/admin/send_bulk_invites/<job_id>
   (must be invoked as an Admin session)
2. The endpoint returns an RQ job id. Monitor the worker logs for job progress.

Local testing
1. Install Redis locally (or use Docker). Start Redis at redis://localhost:6379
2. Set env var: REDIS_URL=redis://localhost:6379
3. Start a worker: rq worker --url $REDIS_URL default
4. From the app (or curl), POST to /api/admin/send_bulk_invites/<job_id>

Notes & next steps
- RQ supports retry/backoff configuration. We currently commit status changes per application; you may want to add a table to record per-email failures for later retries.
- Consider adding an admin UI to view job status (RQ provides job ids; you can query Redis for job progress).
- Ensure `MAIL_DEFAULT_SENDER` is a verified domain in Resend to avoid 403 errors.

If you want, I can also:
- Add a small admin page that enqueues and shows job id/status.
- Add retry/backoff and failure recording to DB.
