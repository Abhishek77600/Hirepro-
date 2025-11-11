# Email Troubleshooting Guide

## Common Issues and Solutions

### Issue 1: "SENDGRID_API_KEY environment variable is not set"

**Solution:**
1. Go to Render Dashboard → Your Web Service → Environment
2. Add environment variable:
   - Key: `SENDGRID_API_KEY`
   - Value: Your SendGrid API key (starts with `SG.`)
3. Save and wait for redeploy

### Issue 2: "MAIL_DEFAULT_SENDER environment variable is not set"

**Solution:**
1. Go to Render Dashboard → Your Web Service → Environment
2. Add environment variable:
   - Key: `MAIL_DEFAULT_SENDER`
   - Value: Your verified SendGrid sender email
3. Save and wait for redeploy

### Issue 3: Emails not sending (most common)

**This usually happens because:**

#### A. Sender Email Not Verified
SendGrid requires sender authentication. You MUST verify your sender email.

**Steps to verify:**
1. Go to [SendGrid Dashboard](https://app.sendgrid.com)
2. Navigate to **Settings** → **Sender Authentication**
3. Click **Verify a Single Sender**
4. Enter your email address
5. Check your email inbox for verification email
6. Click the verification link
7. Once verified, use this email as `MAIL_DEFAULT_SENDER` in Render

**OR** use Domain Authentication (recommended for production):
1. Go to **Settings** → **Sender Authentication**
2. Click **Authenticate Your Domain**
3. Follow the DNS setup instructions
4. Once verified, you can use any email from that domain

#### B. API Key Permissions
Your API key must have "Mail Send" permissions.

**Steps to check/fix:**
1. Go to SendGrid Dashboard → **Settings** → **API Keys**
2. Find your API key (or create a new one)
3. Click on the API key
4. Ensure **Mail Send** permission is enabled
5. If not, edit the key and enable "Full Access" or "Restricted Access" with "Mail Send" enabled

#### C. API Key Invalid or Expired
- Verify the API key is correct (no extra spaces, complete key)
- If unsure, create a new API key in SendGrid and update it in Render

#### D. SendGrid Account Restrictions
- Free tier accounts have sending limits
- Check SendGrid dashboard for any account warnings
- Ensure your account is in good standing

### Issue 4: Getting 403 Forbidden or 401 Unauthorized

**Possible causes:**
- Invalid API key
- API key doesn't have proper permissions
- Sender email not verified

**Solution:**
1. Verify API key in SendGrid dashboard
2. Check API key permissions (must have "Mail Send")
3. Verify sender email is authenticated in SendGrid

### Issue 5: Getting 400 Bad Request

**Possible causes:**
- Invalid sender email format
- Invalid recipient email format
- Missing required fields

**Solution:**
- Ensure `MAIL_DEFAULT_SENDER` is a valid email address
- Ensure recipient emails are valid
- Check SendGrid logs for specific error details

## Testing Your Configuration

### Method 1: Check Health Endpoint
Visit: `https://your-app.onrender.com/health`

Look for the `email` section:
```json
{
  "email": {
    "configured": true,
    "sendgrid_key_set": true,
    "sender_set": true,
    "sender_email": "your-email@example.com"
  }
}
```

### Method 2: Use Test Email Endpoint
1. Log in as admin
2. Make a POST request to `/api/test-email` with:
```json
{
  "email": "your-test-email@example.com"
}
```

### Method 3: Check Render Logs
1. Go to Render Dashboard → Your Service → Logs
2. Look for detailed error messages
3. The improved error handling will show specific issues

## Quick Checklist

- [ ] `SENDGRID_API_KEY` is set in Render environment variables
- [ ] `MAIL_DEFAULT_SENDER` is set in Render environment variables
- [ ] Sender email is verified in SendGrid dashboard
- [ ] API key has "Mail Send" permissions
- [ ] API key is valid and not expired
- [ ] SendGrid account is active and in good standing
- [ ] Checked Render logs for specific error messages

## Still Having Issues?

1. **Check Render Logs**: Look for detailed error messages with "ERROR:" prefix
2. **Check SendGrid Activity**: Go to SendGrid Dashboard → Activity to see if emails are being attempted
3. **Verify Environment Variables**: Use the `/health` endpoint to verify configuration
4. **Test with Simple Email**: Use the `/api/test-email` endpoint to isolate the issue

## SendGrid Resources

- [SendGrid Documentation](https://docs.sendgrid.com/)
- [Sender Authentication Guide](https://docs.sendgrid.com/ui/sending-email/sender-verification)
- [API Key Management](https://docs.sendgrid.com/ui/account-and-settings/api-keys)

