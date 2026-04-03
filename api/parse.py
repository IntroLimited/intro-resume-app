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
from http.server import BaseHTTPRequestHandler

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

def extract_text_from_pdf(file_bytes):
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
        f.write(file_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            ['python3', '-c', f'from pdfminer.high_level import extract_text; print(extract_text("{tmp}"))'],
            capture_output=True, text=True, timeout=30
        )
        text = result.stdout.strip()
        if not text:
            with open(tmp, 'rb') as f:
                raw = f.read().decode('latin-1', errors='ignore')
            text = re.sub(r'[^\x20-\x7E\n]', ' ', raw)[:10000]
        return text
    finally:
        try: os.unlink(tmp)
        except: pass

def extract_text_from_docx(file_bytes):
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
        f.write(file_bytes)
        tmp = f.name
    try:
        result = subprocess.run(
            ['python3', '-c', f'from docx import Document; doc = Document("{tmp}"); print("\\n".join(p.text for p in doc.paragraphs))'],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    finally:
        try: os.unlink(tmp)
        except: pass

def extract_resume_text(file_bytes, filename):
    fn = (filename or '').lower()
    if fn.endswith('.pdf'):
        return extract_text_from_pdf(file_bytes)
    elif fn.endswith('.docx'):
        return extract_text_from_docx(file_bytes)
    return file_bytes.decode('utf-8', errors='ignore')

def parse_resume_with_claude(resume_text, candidate_name, highlights=""):
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    highlight_block = f"\n\nADDITIONAL NOTES FROM RECRUITER:\n{highlights}\n" if highlights else ""
    prompt = f"""You are an expert executive recruiter at Intro Limited.
Analyze this resume for {candidate_name} and write structured candidate notes.{highlight_block}

Rules:
- Shorthand sentence structure, short sentences, casual/professional tone, no slang, no fluff.
- New paragraph every 1-2 sentences.
- NO bullet points. NO extra headers or colons beyond the 4 category names.
- Categories: BASICS, STRONG POINTS, POTENTIAL CHALLENGES, COMPENSATION. Blank line between each.
- BASICS: most detail, max 200 words, no name, no date ranges, no time at companies.
- STRONG POINTS: 3-4 specific strengths, not duplicative of BASICS.
- COMPENSATION: target comp unless current also noted. Use "$Xk" format. Ranges: "$180k - $195k base".
- No years of experience mentioned. No years at companies.
- Extract phone and email if visible in the resume but do NOT include them in the written notes sections — only return them in the phone and email JSON fields.

RESUME:
{resume_text[:7000]}

Return ONLY this JSON, no markdown:
{{"basics":"...","strong_points":"...","potential_challenges":"...","compensation":"...","phone":"...","email":"..."}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

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
    try:
        data = notion_request("POST", f"/databases/{NOTION_DATABASE_ID}/query", {
            "filter": {"property": "title", "title": {"contains": candidate_name}}
        })
        results = data.get("results", [])
        target = candidate_name.lower().strip()

        # First pass: exact match
        for page in results:
            name_prop = (page.get("properties", {}).get("\ufeffName") or
                        page.get("properties", {}).get("Name", {}))
            page_name = "".join(
                t.get("plain_text", "") for t in name_prop.get("title", [])
            ).lower().strip()
            if page_name == target:
                return page["id"], page

        # Second pass: best word match
        best, best_score = None, 0
        for page in results:
            name_prop = (page.get("properties", {}).get("\ufeffName") or
                        page.get("properties", {}).get("Name", {}))
            page_name = "".join(
                t.get("plain_text", "") for t in name_prop.get("title", [])
            ).lower().strip()
            score = sum(1 for w in target.split() if w in page_name.split())
            if score > best_score:
                best_score, best = score, page

        if best:
            return best["id"], best
    except Exception:
        pass
    return None, None
def get_existing_notes(page):
    try:
        rt = page.get("properties", {}).get("Notes", {}).get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in rt)
    except:
        return ""

def build_notes(parsed, role_for, recruiter, existing_notes):
    today = datetime.now().strftime("%m/%d/%y")
    new = f"{recruiter} spoke to for {role_for} {today}\n\nBASICS\n{parsed['basics']}\n\nSTRONG POINTS\n{parsed['strong_points']}\n\nPOTENTIAL CHALLENGES\n{parsed['potential_challenges']}\n\nCOMPENSATION\n{parsed['compensation']}"
    if existing_notes and existing_notes.strip():
        return new + "\n\n" + "—"*30 + "\n\n" + existing_notes.strip()
    return new

def rt_blocks(text, size=1900):
    return [{"type": "text", "text": {"content": text[i:i+size]}} for i in range(0, len(text), size)]

def parse_multipart(content_type, body):
    fs = cgi.FieldStorage(fp=io.BytesIO(body), environ={
        'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': content_type, 'CONTENT_LENGTH': str(len(body))
    })
    result = {}
    for key in ['candidate_name', 'role_for', 'recruiter', 'stage_tag', 'highlights']:
        if key in fs:
            result[key] = fs[key].value
    if 'resume' in fs:
        item = fs['resume']
        result['resume_filename'] = item.filename or 'resume.pdf'
        result['resume_bytes'] = item.file.read()
    return result

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        try:
            ct = self.headers.get('Content-Type', '')
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            form = parse_multipart(ct, body)

            name = form.get('candidate_name', '').strip()
            role = form.get('role_for', '').strip()
            recruiter = form.get('recruiter', 'AW')
            stage = form.get('stage_tag', 'Intro Interviewed')
            highlights = form.get('highlights', '')
            filename = form.get('resume_filename', 'resume.pdf')
            rbytes = form.get('resume_bytes', b'')

            if not name: return self._err(400, 'Candidate name is required.')
            if not role: return self._err(400, 'Role is required.')
            if not rbytes: return self._err(400, 'Resume file is required.')

            resume_text = extract_resume_text(rbytes, filename)
            if not resume_text or len(resume_text) < 40:
                return self._err(400, 'Could not read resume. Try a different format.')

            parsed = parse_resume_with_claude(resume_text, name, highlights)

            page_id, page = find_notion_candidate(name)
            if not page_id:
                return self._err(404, f'"{name}" not found in Notion. Check the name matches exactly.')

            existing = get_existing_notes(page)
            notes = build_notes(parsed, role, recruiter, existing)

            props = {
                "Notes": {"rich_text": rt_blocks(notes)},
                "Stage": {"status": {"name": stage}}
            }
            phone = parsed.get('phone', '')
            email = parsed.get('email', '')
            if phone: props["Phone"] = {"phone_number": phone}
            if email: props["Primary Email"] = {"email": email}
            notion_request("PATCH", f"/pages/{page_id}", {"properties": props})

            self._ok({
                'basics': parsed.get('basics', ''),
                'strong_points': parsed.get('strong_points', ''),
                'potential_challenges': parsed.get('potential_challenges', ''),
                'compensation': parsed.get('compensation', ''),
            })

        except urllib.error.HTTPError as e:
            self._err(500, f'Notion error: {e.read().decode()}')
        except json.JSONDecodeError:
            self._err(500, 'Claude returned unexpected response. Try again.')
        except Exception as e:
            self._err(500, str(e))

    def _ok(self, data):
        self._json(200, data)

    def _err(self, code, msg):
        self._json(code, {'error': msg})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args): pass
