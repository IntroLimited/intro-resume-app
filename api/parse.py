from http.server import BaseHTTPRequestHandler
import json
import os
import re
import cgi
import io
import subprocess
import tempfile
import urllib.request
import urllib.error
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# ─── Text extraction ──────────────────────────────────────────────────────────

def extract_text_from_pdf(file_bytes):
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(file_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            ['python3', '-c', f"""
import sys
try:
    from pdfminer.high_level import extract_text
    print(extract_text("{tmp}"))
except Exception as e:
    with open("{tmp}", "rb") as f:
        raw = f.read().decode("latin-1", errors="ignore")
    import re
    text = re.sub(r'[^\\x20-\\x7E\\n]', ' ', raw)
    print(text[:10000])
"""],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    finally:
        os.unlink(tmp)

def extract_text_from_docx(file_bytes):
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
        f.write(file_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            ['python3', '-c', f"""
from docx import Document
doc = Document("{tmp}")
print("\\n".join(p.text for p in doc.paragraphs))
"""],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    finally:
        os.unlink(tmp)

def extract_resume_text(file_bytes, filename):
    fn = filename.lower()
    if fn.endswith('.pdf'):
        return extract_text_from_pdf(file_bytes)
    elif fn.endswith('.docx'):
        return extract_text_from_docx(file_bytes)
    else:
        return file_bytes.decode('utf-8', errors='ignore')

# ─── Claude parsing ───────────────────────────────────────────────────────────

def parse_resume_with_claude(resume_text, candidate_name, highlights=""):
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    highlight_block = ""
    if highlights:
        highlight_block = f"\n\nADDITIONAL NOTES FROM RECRUITER:\n{highlights}\nMake sure to incorporate and highlight these points where relevant.\n"

    prompt = f"""You are an expert executive recruiter at Intro Limited, a premium recruiting firm.

Analyze this resume for {candidate_name} and write structured candidate notes.{highlight_block}

Follow these rules exactly:
- Use shorthand sentence structure. Keep sentences short and basic.
- Tone: casual/professional. No extraneous words, no slang.
- Create a new paragraph for every 1-2 sentences to visually break up information.
- Do NOT use bullet point formatting anywhere.
- Do NOT add any structure, headers, or colons aside from the 4 category names below.
- The 4 categories are: BASICS, STRONG POINTS, POTENTIAL CHALLENGES, COMPENSATION
- Add a blank line between each section.
- BASICS should have the most detail. Try to keep it 200 words or less. Do not list their name or how long they've been working. Do not include date ranges or time at each company.
- STRONG POINTS should succinctly summarize only 3-4 things that are their strengths, not duplicative of BASICS. If you repeat anything from BASICS, make it very tightly summarized so nothing appears duplicative.
- COMPENSATION should be stated as target compensation unless current comp is also noted. Always use "$" before amounts followed by "k". Example: $180k. For ranges: "$180k - $195k base" (space dash space, dollar sign on both numbers).
- Do not mention years of experience or years at companies anywhere.
- Do not add fluff words.
- Extract phone number and email if visible in the resume.

RESUME TEXT:
{resume_text[:7000]}

Return ONLY a JSON object with these exact keys:
{{
  "basics": "...",
  "strong_points": "...",
  "potential_challenges": "...",
  "compensation": "...",
  "phone": "extracted phone number or empty string",
  "email": "extracted email address or empty string"
}}

Return ONLY the JSON. No markdown, no explanation."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

# ─── Notion helpers ───────────────────────────────────────────────────────────

def notion_request(method, path, payload=None):
    url = f"https://api.notion.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def find_notion_candidate(candidate_name):
    """Search for candidate by name, try progressively broader matches."""
    parts = candidate_name.strip().split()
    # Try full name first, then first name only
    search_terms = [candidate_name] + parts

    for term in search_terms:
        try:
            data = notion_request("POST", f"/databases/{NOTION_DATABASE_ID}/query", {
                "filter": {
                    "property": "Name",
                    "title": {"contains": term}
                }
            })
            results = data.get("results", [])
            if not results:
                continue

            # Score results by name similarity
            best = None
            best_score = 0
            target = candidate_name.lower()

            for page in results:
                name_prop = page.get("properties", {}).get("Name", {})
                page_name = "".join(
                    t.get("plain_text", "")
                    for t in name_prop.get("title", [])
                ).lower()

                # Calculate match score
                score = 0
                for word in target.split():
                    if word in page_name:
                        score += 1

                if score > best_score:
                    best_score = score
                    best = page

            if best:
                return best["id"], best

        except Exception:
            continue

    return None, None

def get_existing_notes(page):
    """Extract existing notes text from a Notion page."""
    try:
        notes_prop = page.get("properties", {}).get("Notes", {})
        rich_text = notes_prop.get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in rich_text)
    except Exception:
        return ""

def build_notes_content(parsed, candidate_name, role_for, recruiter, existing_notes):
    """Build the full notes string: new summary above existing notes."""
    today = datetime.now().strftime("%m/%d/%y")

    header = f"{recruiter} spoke to for {role_for} {today}"

    new_section = f"""{header}

BASICS
{parsed['basics']}

STRONG POINTS
{parsed['strong_points']}

POTENTIAL CHALLENGES
{parsed['potential_challenges']}

COMPENSATION
{parsed['compensation']}"""

    if existing_notes and existing_notes.strip():
        return new_section + "\n\n" + "—" * 30 + "\n\n" + existing_notes.strip()
    return new_section

def rich_text_blocks(text, max_chunk=1900):
    """Split text into Notion-compatible rich text blocks."""
    chunks = [text[i:i+max_chunk] for i in range(0, len(text), max_chunk)]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]

def update_notion_candidate(page_id, parsed, notes_text, stage_tag, phone, email):
    """Update Notion page with notes, stage, phone, email."""
    properties = {
        "Notes": {
            "rich_text": rich_text_blocks(notes_text)
        },
        "Stage": {
            "select": {"name": stage_tag}
        }
    }

    if phone:
        properties["Phone"] = {"phone_number": phone}

    if email:
        properties["Email"] = {"email": email}

    # Try alternate field names for email
    notion_request("PATCH", f"/pages/{page_id}", {"properties": properties})

# ─── Multipart parsing ────────────────────────────────────────────────────────

def parse_multipart(content_type, body):
    fs = cgi.FieldStorage(
        fp=io.BytesIO(body),
        environ={
            'REQUEST_METHOD': 'POST',
            'CONTENT_TYPE': content_type,
            'CONTENT_LENGTH': str(len(body)),
        }
    )

    result = {}
    for key in ['candidate_name', 'role_for', 'recruiter', 'stage_tag', 'highlights']:
        if key in fs:
            result[key] = fs[key].value

    if 'resume' in fs:
        item = fs['resume']
        result['resume_filename'] = item.filename or 'resume.pdf'
        result['resume_bytes'] = item.file.read()

    return result

# ─── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            self._serve_file(
                os.path.join(os.path.dirname(__file__), '..', 'public', 'index.html'),
                'text/html; charset=utf-8'
            )
        else:
            self.send_error(404)

    def _serve_file(self, filepath, content_type):
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/parse':
            self._handle_parse()
        else:
            self.send_error(404)

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _handle_parse(self):
        try:
            content_type = self.headers.get('Content-Type', '')
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            form = parse_multipart(content_type, body)

            candidate_name = form.get('candidate_name', '').strip()
            role_for = form.get('role_for', '').strip()
            recruiter = form.get('recruiter', 'AW').strip()
            stage_tag = form.get('stage_tag', 'Intro Interviewed').strip()
            highlights = form.get('highlights', '').strip()
            resume_filename = form.get('resume_filename', 'resume.pdf')
            resume_bytes = form.get('resume_bytes', b'')

            if not candidate_name:
                return self._json(400, {'error': 'Candidate name is required.'})
            if not role_for:
                return self._json(400, {'error': 'Role is required.'})
            if not resume_bytes:
                return self._json(400, {'error': 'Resume file is required.'})

            # 1. Extract resume text
            resume_text = extract_resume_text(resume_bytes, resume_filename)
            if not resume_text or len(resume_text) < 40:
                return self._json(400, {'error': 'Could not read the resume. Try a different file format.'})

            # 2. Parse with Claude
            parsed = parse_resume_with_claude(resume_text, candidate_name, highlights)

            # 3. Find candidate in Notion
            page_id, page = find_notion_candidate(candidate_name)
            if not page_id:
                return self._json(404, {
                    'error': f'"{candidate_name}" not found in Notion. Check the name matches exactly.'
                })

            # 4. Get existing notes
            existing_notes = get_existing_notes(page)

            # 5. Build full notes
            notes_text = build_notes_content(
                parsed, candidate_name, role_for, recruiter, existing_notes
            )

            # 6. Update Notion
            phone = parsed.get('phone', '')
            email = parsed.get('email', '')
            update_notion_candidate(page_id, parsed, notes_text, stage_tag, phone, email)

            # 7. Return preview
            return self._json(200, {
                'basics': parsed.get('basics', ''),
                'strong_points': parsed.get('strong_points', ''),
                'potential_challenges': parsed.get('potential_challenges', ''),
                'compensation': parsed.get('compensation', ''),
            })

        except urllib.error.HTTPError as e:
            err = e.read().decode()
            return self._json(500, {'error': f'Notion error: {err}'})
        except json.JSONDecodeError:
            return self._json(500, {'error': 'Claude returned an unexpected response. Try again.'})
        except Exception as e:
            return self._json(500, {'error': str(e)})

    def log_message(self, format, *args):
        pass
