# Granola Daily Meeting Digest

Automated daily email digest of your Granola meeting transcripts, summarized by Claude.

## How It Works

Every weekday morning at 7:00 AM CT, a GitHub Action:

1. Pulls all meeting notes from yesterday via the Granola API
2. Fetches full transcripts for each meeting
3. Sends everything to Claude (Sonnet) for a concise, actionable summary
4. Emails you the digest

## Setup (10 minutes)

### 1. Create a GitHub repo

```bash
# From this directory
git init
git add .
git commit -m "Initial commit"
gh repo create granola-digest --private --push
```

Or create a private repo on github.com and push manually.

### 2. Set up Gmail App Password

You need a Gmail "App Password" (not your regular password):

1. Go to https://myaccount.google.com/apppasswords
2. You may need 2FA enabled first
3. Create an app password — name it "Granola Digest" or whatever
4. Copy the 16-character password it gives you

### 3. Add secrets to the repo

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these five secrets:

| Secret Name | Value |
|---|---|
| `GRANOLA_API_KEY` | Your Granola API key (`grn_...`) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `EMAIL_TO` | The email address to receive the digest |
| `SMTP_USER` | Your Gmail address (e.g., `josh@gmail.com`) |
| `SMTP_PASS` | The Gmail App Password from step 2 |

### 4. Test it

Go to **Actions** → **Daily Meeting Digest** → **Run workflow** → **Run workflow**

Watch the logs to confirm it pulls meetings and sends the email.

### 5. Adjust schedule (optional)

The cron runs Mon-Fri at 12:00 UTC (7:00 AM CT). Edit `.github/workflows/daily_digest.yml` to change:

```yaml
- cron: '0 12 * * 1-5'   # Current: 7am CT, weekdays only
- cron: '0 13 * * *'     # Example: 8am CT, every day
- cron: '0 23 * * 1-5'   # Example: 6pm CT, weekdays (end of day)
```

## Cost

- **GitHub Actions**: Free for private repos (2,000 min/month)
- **Anthropic API**: ~$0.01–0.10/day depending on meeting volume
- **Granola**: Requires Business plan ($14/mo) for API access

## Troubleshooting

**No meetings found**: The script looks for notes created in the last 24 hours. If you had no meetings yesterday, it exits silently.

**Gmail auth errors**: Make sure you're using an App Password, not your regular password. Also check that "Less secure app access" isn't blocking you (App Passwords bypass this).

**Granola 401/403**: Check your API key is valid and you're on a Business plan or higher.

**Anthropic errors**: Verify your API key at https://console.anthropic.com.
