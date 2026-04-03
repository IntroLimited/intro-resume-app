# Intro Limited — Candidate Parser

Drop a resume → Claude writes structured notes → Saved to Notion automatically.

## What it does

1. Drop a candidate's resume (PDF or Word)
2. Select who is submitting (Alyssa, Kiki, Bastien, Michael)
3. Type the role this candidate is being considered for
4. Select a stage tag (Intro Interviewed, General Connection, etc.)
5. Optionally add highlight notes
6. Claude parses the resume into BASICS / STRONG POINTS / POTENTIAL CHALLENGES / COMPENSATION
7. Notes are prepended above any existing Notion notes
8. Stage field is updated in Notion
9. Phone and email are extracted and saved if found in the resume

## Notes format saved to Notion

```
AW spoke to for GM – NOT Beauty 04/03/25

BASICS
[concise career summary, no dates, no fluff]

STRONG POINTS
[3-4 specific strengths, not duplicative of basics]

POTENTIAL CHALLENGES
[honest gaps or concerns]

COMPENSATION
[target comp with $ and k formatting]

——————————————————————————
[existing notes preserved below]
```

## Setup — Deploy to Vercel

### Step 1 — GitHub
1. Create a free account at github.com
2. Create a new repository called `intro-resume-app`
3. Upload all files from this zip into the repo

### Step 2 — Vercel
1. Go to vercel.com and sign up with your GitHub account
2. Click "Add New Project" and import your `intro-resume-app` repo
3. Before deploying, add these Environment Variables:

| Variable | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | Your key from console.anthropic.com |
| `NOTION_API_KEY` | Your integration token from notion.so/my-integrations |
| `NOTION_DATABASE_ID` | The ID from your Tracker database URL |

4. Click Deploy

### Step 3 — Bookmark
Vercel gives you a URL like `intro-resume-app.vercel.app`
Bookmark it on your phone and desktop — it works anywhere, always on.

## Getting your keys

**ANTHROPIC_API_KEY**
→ console.anthropic.com → API Keys → Create Key

**NOTION_API_KEY**  
→ notion.so/my-integrations → your integration → Internal Integration Token

**NOTION_DATABASE_ID**
→ Open your Tracker database in Notion → look at the URL
→ It's the long ID between the last `/` and the `?`
→ Example: notion.so/workspace/`1234abcd5678efgh`?v=...
