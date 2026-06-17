import os
import asyncio
import json
import re
import socket
import urllib.request
import urllib.error
from pathlib import Path

GENERATED_DIR = "generated_apps"
BACKEND_PORT_START = 8100


# ─────────────────────────────────────────────────────────────────────────────
# Port helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_free_port(start: int) -> int:
    port = start
    while port < start + 200:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            port += 1
    raise RuntimeError(f"Could not find a free port starting from {start}")


def sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name).strip("_")


# ─────────────────────────────────────────────────────────────────────────────
# Prompt normalisation + intent detection
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_EXPANSIONS = {
    # travel
    "travel": "travel planner with destination, days, budget, and activities",
    "itinerary": "travel planner with destination, days, budget, and activities",
    "trip": "travel planner with destination, days, budget, and activities",
    # habit
    "habit": "habit tracker with habit name, frequency, streak, and completion status",
    "routine": "habit tracker with habit name, frequency, streak, and completion status",
    # sleep
    "sleep": "sleep tracker with bedtime, wake time, duration hours, and quality rating",
    # mood
    "mood": "mood journal with mood rating, energy level, notes, and recorded date",
    "journal": "mood journal with mood rating, energy level, notes, and recorded date",
    # contact
    "contact": "contact manager with name, email, phone, company, and notes",
    "crm": "contact manager with name, email, phone, company, and notes",
    # event
    "event": "event planner with event name, date, location, attendees, and status",
    "calendar": "event planner with event name, date, location, attendees, and status",
    # movie / media
    "movie": "movie watchlist with title, genre, rating, watched status, and watch date",
    "watchlist": "movie watchlist with title, genre, rating, watched status, and watch date",
    # password / credential (safe demo only)
    "password": "credential manager with site name, username, category, and last updated date",
    # plant / garden
    "plant": "plant care tracker with plant name, species, watering frequency, last watered, and health status",
    "garden": "plant care tracker with plant name, species, watering frequency, last watered, and health status",
    # pet
    "pet": "pet care tracker with pet name, species, age, last vet visit, and health notes",
}

_APP_TYPE_MAP = [
    # (keywords tuple, display label)
    (("gym", "workout", "exercise", "fitness", "training", "weightlift", "crossfit"),   "Gym Tracker"),
    (("study", "learning", "course", "lesson", "lecture", "revision", "homework"),       "Study Planner"),
    (("expense", "finance", "money", "budget", "spending", "payment", "bill", "receipt"),"Expense Tracker"),
    (("task", "todo", "project", "kanban", "sprint", "assignment", "ticket"),             "Task Manager"),
    (("subscription", "saas", "recurring", "membership"),                                 "Subscription Tracker"),
    (("inventory", "stock", "warehouse", "asset", "supply"),                              "Inventory Manager"),
    (("invoice", "client", "freelance"),                                                  "Invoice Manager"),
    (("health", "medical", "symptom", "medication", "clinic", "vitals"),                 "Health Tracker"),
    (("note", "memo", "notebook", "jot", "snippet"),                                     "Notes App"),
    (("recipe", "food", "cook", "meal", "ingredient", "dish", "cuisine"),                "Recipe Collection"),
    (("book", "reading", "library", "author", "novel"),                                   "Book Tracker"),
    (("travel", "itinerary", "trip", "destination"),                                      "Travel Planner"),
    (("habit", "routine", "streak"),                                                      "Habit Tracker"),
    (("sleep",),                                                                          "Sleep Tracker"),
    (("mood", "journal", "diary"),                                                        "Mood Journal"),
    (("contact", "crm"),                                                                  "Contact Manager"),
    (("event", "calendar"),                                                               "Event Planner"),
    (("movie", "watchlist", "film"),                                                      "Movie Watchlist"),
    (("pet",),                                                                            "Pet Tracker"),
    (("plant", "garden"),                                                                 "Plant Tracker"),
]


def normalize_prompt(prompt: str) -> str:
    """
    Expand short/vague prompts into richer descriptions that help the LLM
    produce domain-specific schemas.  Falls through unchanged if no match.
    """
    lower = prompt.lower().strip()
    for keyword, expansion in _PROMPT_EXPANSIONS.items():
        if keyword in lower:
            print(f"[IdeaForge] Prompt normalised: {prompt!r} → {expansion!r}")
            return expansion
    return prompt


def detect_app_type(idea: str) -> str:
    """
    Return a human-readable app type label based on keywords in the idea.
    Always returns a non-empty string — falls back to "Custom CRUD App".
    """
    lower = idea.lower()
    for keywords, label in _APP_TYPE_MAP:
        if any(k in lower for k in keywords):
            return label
    return "Custom CRUD App"


# ─────────────────────────────────────────────────────────────────────────────
# Field-type catalogue
#
# Maps the LLM-returned type string → everything the rest of the system needs.
# Only these known types are ever accepted — unknown types fall back to "string".
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_TYPES = {
    # llm_type    sa_type             pydantic_type   html_input   js_read          default_py   default_js
    "string":  ("String(200)",        "str",          "text",      ".value.trim()", '""',        '""'),
    "text":    ("Text",               "str",          "textarea",  ".value.trim()", '""',        '""'),
    "integer": ("Integer",            "int",          "number",    ".value",        "0",         "0"),
    "float":   ("Float",              "float",        "number",    ".value",        "0.0",       "0"),
    "boolean": ("Boolean",            "bool",         "checkbox",  ".checked",     "False",     "false"),
    "date":    ("String(20)",         "Optional[str]","date",      ".value",        '""',        '""'),
    "datetime":("DateTime(timezone=True)", "Optional[str]","datetime-local",".value",'""',       '""'),
}

def _resolve_type(raw: str) -> str:
    """Normalise an LLM-supplied type string to one of the known keys."""
    t = raw.lower().strip()
    aliases = {
        "str": "string", "varchar": "string", "char": "string",
        "long_text": "text", "longtext": "text", "blob": "text",
        "int": "integer", "number": "integer", "bigint": "integer",
        "decimal": "float", "double": "float", "numeric": "float", "money": "float",
        "bool": "boolean",
        "timestamp": "datetime",
    }
    t = aliases.get(t, t)
    return t if t in _FIELD_TYPES else "string"


def _sanitize_field_name(raw: str) -> str:
    """
    Convert an LLM-supplied field name into a safe Python identifier.
    Lowercased, spaces→underscore, non-alphanumeric stripped.
    """
    name = raw.lower().strip()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    # Reserve Python keywords
    if not name or name in {"id", "class", "import", "from", "return", "def",
                             "type", "pass", "lambda", "yield", "raise"}:
        name = "value_" + name if name else "value"
    return name


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# LLM schema generation
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a database schema designer. Your job is to extract domain-specific fields \
from an app idea and return ONLY valid JSON — no explanation, no markdown, no code fences.

OUTPUT FORMAT (return exactly this structure, nothing else):
{
  "fields": [
    {"name": "field_name", "type": "string|integer|float|boolean|date|datetime|text"}
  ]
}

STRICT RULES:
1. MUST extract domain-specific fields from the app idea — fields that only make sense for THIS app.
2. MUST NOT return generic fields like "title", "description", or "name" unless the app \
genuinely needs them AND they are domain-specific (e.g. "exercise_name" is fine, bare "title" is not).
3. MUST include between 4 and 8 fields total.
4. MUST include at least one numeric field (integer or float).
5. Field names MUST be lowercase snake_case and directly related to the app domain.
6. NEVER return a schema whose only fields are generic placeholders like ["title", "description"].
7. No "id" or "created_at" fields — these are added automatically.
8. Allowed types only: string, text, integer, float, boolean, date, datetime.

DOMAIN EXAMPLES (use these as patterns, not as fixed templates):
- gym / workout  → exercise_name (string), sets (integer), reps (integer), weight_kg (float), workout_date (date)
- study / learning → subject (string), topic (string), duration_minutes (integer), study_date (date), completed (boolean)
- finance / expense → title (string), amount (float), category (string), date (date), is_recurring (boolean)
- task / todo    → task_name (string), priority (string), status (string), due_date (date), estimated_hours (float)
- recipe / food  → recipe_name (string), ingredients (text), prep_time_minutes (integer), servings (integer), calories (integer)
- health / medical → symptom (string), severity (integer), temperature (float), recorded_at (datetime), notes (text)
- inventory      → item_name (string), quantity (integer), unit_price (float), location (string), last_updated (date)
- book / reading → book_title (string), author (string), pages (integer), rating (float), finished_date (date)

FEW-SHOT EXAMPLES:

Input: Gym workout tracker
Output:
{"fields":[{"name":"exercise_name","type":"string"},{"name":"sets","type":"integer"},{"name":"reps","type":"integer"},{"name":"weight_kg","type":"float"},{"name":"workout_date","type":"date"},{"name":"completed","type":"boolean"}]}

Input: Study planner app
Output:
{"fields":[{"name":"subject","type":"string"},{"name":"topic","type":"string"},{"name":"duration_minutes","type":"integer"},{"name":"study_date","type":"date"},{"name":"completed","type":"boolean"},{"name":"notes","type":"text"}]}

Input: Personal expense tracker
Output:
{"fields":[{"name":"title","type":"string"},{"name":"amount","type":"float"},{"name":"category","type":"string"},{"name":"date","type":"date"},{"name":"is_recurring","type":"boolean"}]}

Input: Recipe collection app
Output:
{"fields":[{"name":"recipe_name","type":"string"},{"name":"ingredients","type":"text"},{"name":"prep_time_minutes","type":"integer"},{"name":"servings","type":"integer"},{"name":"difficulty","type":"string"},{"name":"rating","type":"float"}]}
"""


def _call_anthropic_api(prompt: str) -> dict:
    """
    Call the Anthropic Messages API (POST /v1/messages).

    Endpoint : https://api.anthropic.com/v1/messages
    Headers  : x-api-key, anthropic-version, content-type
    Model    : claude-3-haiku-20240307

    Returns the parsed JSON schema dict.
    Raises on any error — never swallows exceptions so the caller decides
    whether to fall back.  On HTTP 4xx/5xx the full response body is printed
    so the real error reason is always visible in the server log.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set")

    # The system instructions are embedded in the user message so the request
    # body matches the simplest valid Messages API format exactly.
    user_content = _SYSTEM_PROMPT + "\n\nApp idea: " + prompt

    body_dict = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 1000,
        "messages": [
            {"role": "user", "content": user_content}
        ],
    }

    payload = json.dumps(body_dict).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Read and log the full error body so the real reason is never hidden
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = "(could not read error body)"
        print(f"\n[IdeaForge] HTTP {exc.code} from Anthropic API:")
        print(f"[IdeaForge] {error_body}")
        print()
        raise RuntimeError(
            f"Anthropic API returned HTTP {exc.code}. Body: {error_body[:400]}"
        ) from exc

    # ── Read response from content[0]["text"] ────────────────────────────────
    raw_text = body["content"][0]["text"].strip()

    # ── DEBUG: always log the raw LLM text so failures are visible ───────────
    print(f"\n[IdeaForge] LLM RAW TEXT for prompt {prompt[:80]!r}:")
    print(f"[IdeaForge] {raw_text}")
    print()

    # Strip any accidental markdown fences the model may have added
    text = re.sub(r"^```[a-z]*\n?", "", raw_text)
    text = re.sub(r"\n?```$", "", text).strip()

    # Raises json.JSONDecodeError if not valid JSON — NOT caught here
    schema = json.loads(text)

    if "fields" not in schema or not isinstance(schema["fields"], list):
        raise ValueError(
            f"LLM response missing 'fields' list. Raw: {raw_text[:300]}"
        )
    if len(schema["fields"]) == 0:
        raise ValueError("LLM returned an empty fields list.")

    # ── DEBUG: log the parsed schema before validation ────────────────────────
    print(f"[IdeaForge] LLM SCHEMA (pre-validation): {schema}")
    print()

    return schema


def _validate_llm_schema(raw_schema: dict) -> list:
    """
    Validate and sanitise the raw LLM response.
    Returns a list of clean field dicts:
      {"name": safe_str, "type": known_type, "required": bool}

    Sanitisation rules:
    - Field names → safe Python identifiers via _sanitize_field_name.
    - Unknown types → normalised via _resolve_type (fallback: "string").
    - Duplicate names → deduplicated with numeric suffix.
    - Cap at 8 fields.

    Safety net (only fires when LLM ignores the prompt):
    - If the schema has NO string fields at all, prepend a generic "name" field
      so the app always has at least one human-readable primary field.
    - The old unconditional "always prepend title" behaviour is removed — the
      new prompt explicitly prohibits generic fields like "title".
    """
    raw_fields = list(raw_schema.get("fields", []))  # fresh copy, no mutation

    # Cap at 8 before processing
    raw_fields = raw_fields[:8]

    seen: dict = {}
    fields: list = []

    for f in raw_fields:
        name = _sanitize_field_name(f.get("name", "field"))
        typ  = _resolve_type(f.get("type", "string"))
        req  = bool(f.get("required", False))

        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0

        fields.append({"name": name, "type": typ, "required": req})

    # Safety net: if the LLM returned zero string/text fields (pure numeric schema),
    # prepend a generic "name" field so the app has one human-readable identifier.
    # This is a last-resort guard — the new prompt should prevent this case.
    has_string_field = any(f["type"] in ("string", "text") for f in fields)
    if not has_string_field:
        fields = [{"name": "name", "type": "string", "required": True}] + fields
        fields = fields[:8]  # re-apply cap after potential prepend

    # ── DEBUG: log the final validated field list ─────────────────────────────
    print(f"[IdeaForge] VALIDATED FIELDS: {fields}")
    print()

    return fields


def _fields_to_schema(fields: list) -> dict:
    """
    Convert the validated list of field dicts into the internal schema dict
    expected by _write_backend() and _write_frontend().

    All values produced here are safe Python/JS identifiers and known type
    strings from _FIELD_TYPES — never raw user text.
    """
    SA_IMPORTS_FIXED = "Column, Integer, String, Text, Float, Boolean, DateTime"

    sa_columns      = ["id = Column(Integer, primary_key=True, index=True)"]
    pydantic_create = []
    pydantic_update = []
    pydantic_out    = []
    ctor_args       = {}
    update_pairs    = []
    form_fields     = []
    card_lines      = []
    edit_fields     = []

    for f in fields:
        fname    = f["name"]
        ftype    = f["type"]
        required = f["required"]

        sa_type, py_type, html_type, js_read, py_default, js_default = _FIELD_TYPES[ftype]

        # ── SQLAlchemy column ─────────────────────────────────────────
        if ftype == "datetime":
            col = f'{fname} = Column(DateTime(timezone=True), nullable=True)'
        elif ftype == "boolean":
            col = f'{fname} = Column(Boolean, default=False)'
        elif ftype == "text":
            col = f'{fname} = Column(Text, default="")'
        elif ftype == "float":
            col = f'{fname} = Column(Float, nullable=False, default=0.0)'
        elif ftype == "integer":
            col = f'{fname} = Column(Integer, nullable=False, default=0)'
        elif ftype == "date":
            col = f'{fname} = Column(String(20), default="")'
        else:  # string (default / fallback)
            col = (
                f'{fname} = Column(String(200), nullable=False)'
                if required else
                f'{fname} = Column(String(200), default="")'
            )
        sa_columns.append(col)

        # ── Pydantic ──────────────────────────────────────────────────
        if ftype == "boolean":
            pydantic_create.append((fname, "Optional[bool]",  "False"))
            pydantic_update.append((fname, "Optional[bool]",  "None"))
            pydantic_out.append(   (fname, "bool",            "False"))
        elif ftype == "integer":
            pydantic_create.append((fname, "Optional[int]",   "0"))
            pydantic_update.append((fname, "Optional[int]",   "None"))
            pydantic_out.append(   (fname, "int",             "0"))
        elif ftype == "float":
            pydantic_create.append((fname, "Optional[float]", "0.0"))
            pydantic_update.append((fname, "Optional[float]", "None"))
            pydantic_out.append(   (fname, "float",           "0.0"))
        elif ftype in ("date", "datetime"):
            # Date/datetime stored as nullable string — always Optional in all schemas
            pydantic_create.append((fname, "Optional[str]",   '""'))
            pydantic_update.append((fname, "Optional[str]",   "None"))
            pydantic_out.append(   (fname, "Optional[str]",   '""'))
        elif required:
            pydantic_create.append((fname, "str",              None))
            pydantic_update.append((fname, "Optional[str]",   "None"))
            pydantic_out.append(   (fname, "str",             '""'))
        else:
            pydantic_create.append((fname, "Optional[str]",   '""'))
            pydantic_update.append((fname, "Optional[str]",   "None"))
            pydantic_out.append(   (fname, "Optional[str]",   '""'))

        # ── Constructor args (use safe helpers from generated main.py) ──
        if ftype == "boolean":
            ctor_args[fname] = f"_safe_bool(item.{fname})"
        elif ftype == "integer":
            ctor_args[fname] = f"_safe_int(item.{fname})"
        elif ftype == "float":
            ctor_args[fname] = f"_safe_float(item.{fname})"
        elif ftype in ("date", "datetime"):
            ctor_args[fname] = f"_safe_date(item.{fname})"
        else:
            ctor_args[fname] = f"_safe_str(item.{fname})"

        update_pairs.append((fname, ftype))

        # ── Form / edit field spec ────────────────────────────────────
        label = fname.replace("_", " ").title()
        form_fields.append({
            "id": f"inp-{fname}", "label": label, "type": ftype,
            "placeholder": label, "api_key": fname, "required": required,
        })
        edit_fields.append({
            "id": f"edit-{fname}", "label": label, "type": ftype,
            "placeholder": label, "api_key": fname, "required": required,
        })

        # ── Card display line ─────────────────────────────────────────
        if ftype == "float":
            js_expr = f"Number(item.{fname}).toFixed(2)"
        elif ftype == "boolean":
            js_expr = f"item.{fname} ? 'Yes' : 'No'"
        else:
            js_expr = f"escHtml(item.{fname} || '')"
        card_lines.append((label, js_expr))

    sa_columns.append("created_at = Column(DateTime(timezone=True), server_default=func.now())")

    return {
        "type": "dynamic",
        "sa_imports": SA_IMPORTS_FIXED,
        "sa_columns": sa_columns,
        "pydantic_create": pydantic_create,
        "pydantic_update": pydantic_update,
        "pydantic_out":    pydantic_out,
        "ctor_args":       ctor_args,
        "update_pairs":    update_pairs,
        "form_fields":     form_fields,
        "card_lines":      card_lines,
        "edit_fields":     edit_fields,
    }


def generate_schema_from_prompt(prompt: str) -> dict:
    """
    Call the Anthropic API and return a validated internal schema dict.

    This is the ONLY entry point for the LLM path.  It raises on any failure —
    callers must explicitly decide whether to fall back; nothing is silenced here.

    Debug output (printed to stdout / server log):
      [IdeaForge] LLM RAW TEXT         — raw API response before any processing
      [IdeaForge] LLM SCHEMA            — parsed JSON before validation
      [IdeaForge] VALIDATED FIELDS      — field list after sanitisation
      [IdeaForge] FINAL SCHEMA FIELDS   — api_keys used by backend + frontend

    Raises:
        EnvironmentError      — ANTHROPIC_API_KEY not set
        urllib.error.URLError — network failure
        json.JSONDecodeError  — LLM returned non-JSON
        ValueError            — LLM JSON missing required structure / empty
    """
    print(f"\n[IdeaForge] generate_schema_from_prompt called: {prompt[:120]!r}")

    raw_schema   = _call_anthropic_api(prompt)       # raises on any API / parse error
    clean_fields = _validate_llm_schema(raw_schema)  # sanitise names + types
    schema       = _fields_to_schema(clean_fields)   # build full internal dict

    final_keys = [f["api_key"] for f in schema["form_fields"]]
    print(f"[IdeaForge] FINAL SCHEMA FIELDS: {final_keys}")
    print(f"[IdeaForge] Schema type tag: {schema['type']}")
    print()

    return schema


# ─────────────────────────────────────────────────────────────────────────────
# Keyword-based fallback — used ONLY when LLM is unavailable or fails hard
# ─────────────────────────────────────────────────────────────────────────────

_EXPENSE_KEYWORDS = {"expense", "expenses", "budget", "spending", "money", "cost",
                     "finance", "financial", "payment", "invoice", "bill",
                     "receipt", "transaction"}
_TASK_KEYWORDS    = {"task", "tasks", "todo", "todos", "checklist", "project",
                     "kanban", "sprint", "assignment", "ticket", "issue",
                     "workflow", "to-do"}
_NOTE_KEYWORDS    = {"note", "notes", "journal", "diary", "memo", "memos",
                     "notebook", "jot", "jots", "snippet", "snippets", "writing"}


def _detect_schema_keywords(idea: str) -> dict:
    """
    Keyword-based schema selection.  Called ONLY when:
      - ANTHROPIC_API_KEY is not set, OR
      - the LLM call raises an exception.
    Never used as a silent default — the orchestrator always logs which path ran.
    """
    lower = idea.lower()
    words = set(lower.replace(",", " ").replace(".", " ").replace("-", " ").split())
    if words & _EXPENSE_KEYWORDS:
        return _fields_to_schema(_validate_llm_schema({"fields": [
            {"name": "title",        "type": "string",  "required": True},
            {"name": "amount",       "type": "float",   "required": True},
            {"name": "category",     "type": "string",  "required": False},
            {"name": "date",         "type": "date",    "required": False},
            {"name": "is_recurring", "type": "boolean", "required": False},
        ]}))
    if words & _TASK_KEYWORDS:
        return _fields_to_schema(_validate_llm_schema({"fields": [
            {"name": "title",       "type": "string", "required": True},
            {"name": "description", "type": "text",   "required": False},
            {"name": "priority",    "type": "string", "required": False},
            {"name": "status",      "type": "string", "required": False},
        ]}))
    if words & _NOTE_KEYWORDS:
        return _fields_to_schema(_validate_llm_schema({"fields": [
            {"name": "title",   "type": "string", "required": True},
            {"name": "content", "type": "text",   "required": False},
        ]}))
    return _fields_to_schema(_validate_llm_schema({"fields": [
        {"name": "title",       "type": "string", "required": True},
        {"name": "description", "type": "text",   "required": False},
    ]}))


def detect_schema_smart(idea: str) -> dict:
    """
    Domain-aware keyword fallback that returns richer, app-specific schemas.
    Used when the LLM is unavailable OR when the LLM returns a weak/generic schema.
    Returns an internal schema dict (same shape as generate_schema_from_prompt).
    """
    lower = idea.lower()

    def _make(fields):
        return _fields_to_schema(_validate_llm_schema({"fields": fields}))

    # ── Gym / Fitness ──────────────────────────────────────────────────────────
    if any(k in lower for k in ("gym", "workout", "exercise", "fitness", "training",
                                 "weightlift", "crossfit", "bodybuilding")):
        return _make([
            {"name": "exercise",     "type": "string",  "required": True},
            {"name": "sets",         "type": "integer", "required": False},
            {"name": "reps",         "type": "integer", "required": False},
            {"name": "weight",       "type": "float",   "required": False},
            {"name": "workout_date", "type": "date",    "required": False},
            {"name": "completed",    "type": "boolean", "required": False},
        ])

    # ── Study / Learning ───────────────────────────────────────────────────────
    if any(k in lower for k in ("study", "learning", "subject", "course",
                                 "lesson", "lecture", "revision", "homework")):
        return _make([
            {"name": "subject",          "type": "string",  "required": True},
            {"name": "topic",            "type": "string",  "required": False},
            {"name": "duration_minutes", "type": "integer", "required": False},
            {"name": "study_date",       "type": "date",    "required": False},
            {"name": "completed",        "type": "boolean", "required": False},
            {"name": "notes",            "type": "text",    "required": False},
        ])

    # ── Expense / Finance ──────────────────────────────────────────────────────
    if any(k in lower for k in ("expense", "finance", "money", "budget", "spending",
                                 "cost", "payment", "bill", "receipt", "transaction")):
        return _make([
            {"name": "title",        "type": "string",  "required": True},
            {"name": "amount",       "type": "float",   "required": True},
            {"name": "category",     "type": "string",  "required": False},
            {"name": "date",         "type": "date",    "required": False},
            {"name": "is_recurring", "type": "boolean", "required": False},
        ])

    # ── Task / Project / Todo ──────────────────────────────────────────────────
    if any(k in lower for k in ("task", "todo", "project", "kanban", "sprint",
                                 "assignment", "ticket", "issue", "workflow")):
        return _make([
            {"name": "title",       "type": "string", "required": True},
            {"name": "description", "type": "text",   "required": False},
            {"name": "priority",    "type": "string", "required": False},
            {"name": "status",      "type": "string", "required": False},
            {"name": "due_date",    "type": "date",   "required": False},
        ])

    # ── Subscription / Billing ─────────────────────────────────────────────────
    if any(k in lower for k in ("subscription", "billing", "saas", "recurring",
                                 "membership", "plan")):
        return _make([
            {"name": "service_name",      "type": "string",  "required": True},
            {"name": "cost",              "type": "float",   "required": True},
            {"name": "billing_cycle",     "type": "string",  "required": False},
            {"name": "next_payment_date", "type": "date",    "required": False},
            {"name": "active",            "type": "boolean", "required": False},
        ])

    # ── Inventory / Stock ──────────────────────────────────────────────────────
    if any(k in lower for k in ("inventory", "stock", "product", "warehouse",
                                 "asset", "supply", "item")):
        return _make([
            {"name": "item_name",    "type": "string",  "required": True},
            {"name": "quantity",     "type": "integer", "required": False},
            {"name": "price",        "type": "float",   "required": False},
            {"name": "category",     "type": "string",  "required": False},
            {"name": "last_updated", "type": "date",    "required": False},
        ])

    # ── Invoice / Client ──────────────────────────────────────────────────────
    if any(k in lower for k in ("invoice", "client", "freelance", "billing")):
        return _make([
            {"name": "client_name",  "type": "string", "required": True},
            {"name": "amount",       "type": "float",  "required": True},
            {"name": "invoice_date", "type": "date",   "required": False},
            {"name": "due_date",     "type": "date",   "required": False},
            {"name": "status",       "type": "string", "required": False},
            {"name": "paid",         "type": "boolean","required": False},
        ])

    # ── Health / Medical ──────────────────────────────────────────────────────
    if any(k in lower for k in ("health", "medical", "symptom", "medication",
                                 "doctor", "clinic", "vitals")):
        return _make([
            {"name": "symptom",     "type": "string",  "required": True},
            {"name": "severity",    "type": "integer", "required": False},
            {"name": "temperature", "type": "float",   "required": False},
            {"name": "recorded_at", "type": "date",    "required": False},
            {"name": "notes",       "type": "text",    "required": False},
        ])

    # ── Notes / Journal / Diary ────────────────────────────────────────────────
    if any(k in lower for k in ("note", "journal", "diary", "memo", "jot",
                                 "notebook", "writing", "log")):
        return _make([
            {"name": "title",        "type": "string", "required": True},
            {"name": "content",      "type": "text",   "required": False},
            {"name": "created_date", "type": "date",   "required": False},
        ])

    # ── Recipe / Food ──────────────────────────────────────────────────────────
    if any(k in lower for k in ("recipe", "food", "cook", "meal", "ingredient",
                                 "dish", "cuisine")):
        return _make([
            {"name": "recipe_name",      "type": "string",  "required": True},
            {"name": "ingredients",      "type": "text",    "required": False},
            {"name": "prep_time_minutes","type": "integer", "required": False},
            {"name": "servings",         "type": "integer", "required": False},
            {"name": "rating",           "type": "float",   "required": False},
        ])

    # ── Book / Reading ──────────────────────────────────────────────────────────
    if any(k in lower for k in ("book", "reading", "library", "author", "novel",
                                 "fiction", "nonfiction")):
        return _make([
            {"name": "book_title",     "type": "string",  "required": True},
            {"name": "author",         "type": "string",  "required": False},
            {"name": "pages",          "type": "integer", "required": False},
            {"name": "rating",         "type": "float",   "required": False},
            {"name": "finished_date",  "type": "date",    "required": False},
            {"name": "completed",      "type": "boolean", "required": False},
        ])

    # ── Default ────────────────────────────────────────────────────────────────
    return _make([
        {"name": "title",       "type": "string", "required": True},
        {"name": "description", "type": "text",   "required": False},
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def generate_app(app_name: str, app_idea: str, log, user_id: int = 0):
    """
    Generate a full-stack app.

    When user_id > 0 the app is placed under:
        generated_apps/user_{user_id}/{safe_name}/
    Otherwise (legacy / no auth):
        generated_apps/{safe_name}/

    Returns the Path object of the app root so callers can store it.
    """
    safe_name  = sanitize(app_name)
    model_name = safe_name.capitalize()
    table_name = safe_name.lower() + "_items"

    if user_id:
        base = Path(GENERATED_DIR) / f"user_{user_id}" / safe_name
    else:
        base = Path(GENERATED_DIR) / safe_name

    await log(f"🚀 Starting generation for: {app_name}", "info")
    await asyncio.sleep(0.3)

    # ── Prompt normalisation ──────────────────────────────────────────
    normalised_idea = normalize_prompt(app_idea)
    if normalised_idea != app_idea:
        await log(f"✏️  Prompt normalised for better schema accuracy", "info")

    # ── Intent detection ──────────────────────────────────────────────
    app_type = detect_app_type(normalised_idea)
    await log(f"🏷️  Detected App Type: {app_type}", "info")

    # ── Folder structure ──────────────────────────────────────────────
    await log("📁 Creating project folder structure...", "info")
    for folder in [base / "backend", base / "frontend", base / "database"]:
        folder.mkdir(parents=True, exist_ok=True)
    await asyncio.sleep(0.4)
    await log("✅ Folders created successfully", "success")

    # ── Schema resolution ─────────────────────────────────────────────
    schema = None
    schema_source = "unknown"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if api_key:
        await log("🤖 Requesting schema from AI (Anthropic)...", "info")
        try:
            loop = asyncio.get_event_loop()
            schema = await loop.run_in_executor(
                None, generate_schema_from_prompt, normalised_idea
            )
            schema_source = "llm"
            field_names = [f["api_key"] for f in schema["form_fields"]]
            await log(
                f"✅ AI schema ({len(field_names)} fields): {', '.join(field_names)}",
                "success",
            )
        except EnvironmentError as exc:
            await log(f"❌ AI auth error: {exc}", "error")
            await log("⚠️  Falling back to keyword-based schema.", "info")
        except json.JSONDecodeError as exc:
            await log(f"❌ AI returned invalid JSON: {exc}", "error")
            await log("⚠️  Falling back to keyword-based schema.", "info")
        except ValueError as exc:
            await log(f"❌ AI schema structure error: {exc}", "error")
            await log("⚠️  Falling back to keyword-based schema.", "info")
        except Exception as exc:
            await log(f"❌ AI call failed ({type(exc).__name__}): {exc}", "error")
            await log("⚠️  Falling back to keyword-based schema.", "info")
    else:
        await log("ℹ️  ANTHROPIC_API_KEY not set — using keyword-based schema.", "info")

    if schema is None:
        schema = detect_schema_smart(normalised_idea)
        schema_source = "smart-fallback"
        field_names = [f["api_key"] for f in schema["form_fields"]]
        await log(
            f"🔍 Smart fallback schema ({len(field_names)} fields): {', '.join(field_names)}",
            "info",
        )

    # ── Weak-schema safety check ──────────────────────────────────────
    _generic_only = {f["api_key"] for f in schema.get("form_fields", [])} <= {"title", "description"}
    if not schema or len(schema.get("form_fields", [])) <= 2 or _generic_only:
        await log("⚠️  Weak schema detected → switching to smart fallback", "info")
        schema = detect_schema_smart(normalised_idea)
        schema_source = "smart-fallback"
        field_names = [f["api_key"] for f in schema["form_fields"]]
        await log(
            f"🔍 Smart fallback schema ({len(field_names)} fields): {', '.join(field_names)}",
            "info",
        )

    await log(
        f"📋 Schema source: {schema_source} | "
        f"fields: {[f['api_key'] for f in schema['form_fields']]}",
        "info",
    )

    # ── Assign unique backend port ────────────────────────────────────
    backend_port = _find_free_port(BACKEND_PORT_START)
    (base / "ports.json").write_text(
        json.dumps({"backend": backend_port, "app_type": app_type}),
        encoding="utf-8",
    )
    await log(f"🔌 Assigned backend port {backend_port}", "info")

    # ── Backend ───────────────────────────────────────────────────────
    await log("⚙️  Generating FastAPI backend...", "info")
    await asyncio.sleep(0.5)
    _write_backend(base / "backend", safe_name, model_name, table_name, schema)
    await log("✅ Backend created: main.py, models.py, database.py", "success")

    # ── Frontend ──────────────────────────────────────────────────────
    await log("🎨 Generating frontend (HTML/CSS/JS)...", "info")
    await asyncio.sleep(0.5)
    display_name = safe_name.replace("_", " ").title()
    safe_idea_html = (
        app_idea[:120]
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
    _write_frontend(base / "frontend", display_name, safe_idea_html, schema, app_type)
    await log("✅ Frontend created: index.html, style.css, script.js", "success")

    # ── Database ──────────────────────────────────────────────────────
    await log("🗄️  Initialising SQLite database...", "info")
    await asyncio.sleep(0.4)
    (base / "database" / "app.db").touch()
    await log("✅ Database file created: database/app.db", "success")

    # ── Run scripts ───────────────────────────────────────────────────
    await log("📝 Writing launch scripts (run_app.bat + run_app.sh)...", "info")
    await asyncio.sleep(0.3)
    _write_bat(base, safe_name, backend_port)
    await log("✅ run_app.bat and run_app.sh created", "success")

    await log("", "spacer")
    await log("🎉 App generated successfully!", "done")
    await log(f"🏷️  App Type: {app_type}", "info")
    await log(f"📂 Location: {base}/", "info")
    await log("▶️  Head to the Runner to launch your app.", "info")

    return base



# ─────────────────────────────────────────────────────────────────────────────
# Code-generation helpers (unchanged from previous version)
# ─────────────────────────────────────────────────────────────────────────────

def _pydantic_field(name, typ, default):
    if default is None:
        return f"    {name}: {typ}"
    return f"    {name}: {typ} = {default}"

def _build_pydantic_create(fields):
    return "\n".join(_pydantic_field(n, t, d) for n, t, d in fields)

def _build_pydantic_update(fields):
    return "\n".join(_pydantic_field(n, t, d) for n, t, d in fields)

def _build_pydantic_out(fields):
    return "\n".join(_pydantic_field(n, t, d) for n, t, d in fields)

def _build_ctor_args(ctor_args):
    return "\n".join(f"        {k}={v}," for k, v in ctor_args.items())

def _build_update_body(update_pairs):
    """
    Generate per-field update lines indented for placement inside a try: block
    (8 spaces for the if, 12 spaces for the assignment).
    update_pairs is a list of (field_name, field_type) tuples.
    Uses safe helper functions for type coercion and date normalization.
    """
    lines = []
    for field, ftype in update_pairs:
        lines.append(f"        if item.{field} is not None:")
        if ftype == "integer":
            lines.append(f"            obj.{field} = _safe_int(item.{field})")
        elif ftype == "float":
            lines.append(f"            obj.{field} = _safe_float(item.{field})")
        elif ftype == "boolean":
            lines.append(f"            obj.{field} = _safe_bool(item.{field})")
        elif ftype in ("date", "datetime"):
            lines.append(f"            obj.{field} = _safe_date(item.{field})")
        else:
            lines.append(f"            obj.{field} = _safe_str(item.{field})")
    return "\n".join(lines)

def _build_sa_columns(columns):
    return "\n    ".join(columns)


# ─────────────────────────────────────────────────────────────────────────────
# _write_backend — schema-driven, zero user-text injection
# ─────────────────────────────────────────────────────────────────────────────

def _write_backend(path, app_name, model_name, table_name, schema):
    (path / "requirements.txt").write_text(
        "fastapi>=0.100.0\n"
        "uvicorn[standard]>=0.20.0\n"
        "sqlalchemy>=2.0.0\n"
        "python-multipart>=0.0.6\n"
        "itsdangerous>=2.1.0\n"
        "aiofiles>=23.0.0\n",
        encoding="utf-8",
    )

    (path / "database.py").write_text(
        """\
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

# Support both SQLAlchemy 1.4 and 2.x
try:
    from sqlalchemy.orm import DeclarativeBase
    class Base(DeclarativeBase):
        pass
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
    Base = declarative_base()

DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "database", "app.db")
)
DATABASE_URL = "sqlite:///" + DB_PATH

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
""",
        encoding="utf-8",
    )

    sa_col_block = _build_sa_columns(schema["sa_columns"])

    # models.py always uses the same complete import line — no dynamic
    # substitution of import names, which was the source of NameError crashes.
    models_src = """\
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base


class MODEL_PLACEHOLDER(Base):
    __tablename__ = "TABLE_PLACEHOLDER"

    COLUMNS_PLACEHOLDER
"""
    models_src = models_src.replace("MODEL_PLACEHOLDER",   model_name)
    models_src = models_src.replace("TABLE_PLACEHOLDER",   table_name)
    models_src = models_src.replace("COLUMNS_PLACEHOLDER", sa_col_block)
    (path / "models.py").write_text(models_src, encoding="utf-8")

    create_fields = _build_pydantic_create(schema["pydantic_create"])
    update_fields = _build_pydantic_update(schema["pydantic_update"])
    out_fields    = _build_pydantic_out(schema["pydantic_out"])
    ctor_args     = _build_ctor_args(schema["ctor_args"])
    update_body   = _build_update_body(schema["update_pairs"])

    main_src = """\
import os
import sys
import csv
import json
import hashlib
import io
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ideaforge")

from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import engine, get_db, Base
from models import MODEL_PLACEHOLDER


# ── User model ────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id       = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False)
    password = Column(String(128), nullable=False)


# ── Safe DB initialisation ────────────────────────────────────────────────────

try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created / verified OK")
except Exception as _db_exc:
    logger.error("DB init failed: %s", _db_exc)
    raise SystemExit(f"Cannot initialise database: {_db_exc}") from _db_exc


# ── App + middleware ──────────────────────────────────────────────────────────

app = FastAPI(title="APP_NAME_PLACEHOLDER API")

# SessionMiddleware MUST be added before CORSMiddleware
app.add_middleware(SessionMiddleware, secret_key="ideaforge-secret-change-in-prod-32chars!")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── Startup / shutdown events ─────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    logger.info("APP_NAME_PLACEHOLDER starting up — frontend: %s", FRONTEND_DIR)


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("APP_NAME_PLACEHOLDER shutting down")


# ── Global exception handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "path": str(request.url.path)},
    )


# ── Safe input helpers ────────────────────────────────────────────────────────

def _safe_str(v, max_len: int = 500) -> str:
    if v is None:
        return ""
    return str(v).strip()[:max_len]


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v) if v not in (None, "", "null") else default
    except (ValueError, TypeError):
        return default


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", "null") else default
    except (ValueError, TypeError):
        return default


def _safe_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes", "on")
    return bool(v)


def _safe_date(v) -> str:
    # Accept yyyy-mm-dd or dd-mm-yyyy; always store as yyyy-mm-dd.
    if not v:
        return ""
    s = str(v).strip()
    # dd-mm-yyyy → yyyy-mm-dd
    import re as _re
    m = _re.match(r"^(\\d{1,2})[/\\\\\\-\\.](\\d{1,2})[/\\\\\\-\\.](\\d{4})$", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return s


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _current_user(request: Request):
    try:
        return request.session.get("user")
    except Exception:
        return None


def _require_user(request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=FileResponse, include_in_schema=False)
def login_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))


@app.post("/login", include_in_schema=False)
def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or user.password != _hash_pw(password):
            return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    except Exception as exc:
        logger.error("Login error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/register", include_in_schema=False)
def do_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        if not username or len(username) < 2:
            return JSONResponse(status_code=422, content={"error": "Username too short"})
        if not password or len(password) < 4:
            return JSONResponse(status_code=422, content={"error": "Password must be at least 4 characters"})
        if db.query(User).filter(User.username == username).first():
            return JSONResponse(status_code=409, content={"error": "Username already taken"})
        db.add(User(username=username, password=_hash_pw(password)))
        db.commit()
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    except Exception as exc:
        db.rollback()
        logger.error("Register error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/logout", include_in_schema=False)
def do_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/api/me", tags=["auth"])
def get_me(request: Request):
    user = _current_user(request)
    return {"user": user, "authenticated": bool(user)}


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse, include_in_schema=False)
def serve_index(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
CREATE_FIELDS_PLACEHOLDER


class ItemUpdate(BaseModel):
UPDATE_FIELDS_PLACEHOLDER


class ItemOut(BaseModel):
    id: int
OUT_FIELDS_PLACEHOLDER

    class Config:
        from_attributes = True


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "app": "APP_NAME_PLACEHOLDER"}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@app.post("/items", response_model=ItemOut, tags=["items"])
def create_item(item: ItemCreate, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        obj = MODEL_PLACEHOLDER(
CTOR_ARGS_PLACEHOLDER
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except Exception as exc:
        db.rollback()
        logger.error("create_item error: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/items", response_model=List[ItemOut], tags=["items"])
def read_items(request: Request, skip: int = 0, limit: int = 500, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        return (
            db.query(MODEL_PLACEHOLDER)
            .order_by(MODEL_PLACEHOLDER.id.desc())
            .offset(skip).limit(limit).all()
        )
    except Exception as exc:
        logger.error("read_items error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/items/{item_id}", response_model=ItemOut, tags=["items"])
def read_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    obj = db.query(MODEL_PLACEHOLDER).filter(MODEL_PLACEHOLDER.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@app.put("/items/{item_id}", response_model=ItemOut, tags=["items"])
def update_item(item_id: int, item: ItemUpdate, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    obj = db.query(MODEL_PLACEHOLDER).filter(MODEL_PLACEHOLDER.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
UPDATE_BODY_PLACEHOLDER
        db.commit()
        db.refresh(obj)
        return obj
    except Exception as exc:
        db.rollback()
        logger.error("update_item error: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/items/{item_id}", tags=["items"])
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    obj = db.query(MODEL_PLACEHOLDER).filter(MODEL_PLACEHOLDER.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(obj)
    db.commit()
    return {"detail": "Deleted"}


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/export/json", tags=["export"])
def export_json(request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        items = db.query(MODEL_PLACEHOLDER).order_by(MODEL_PLACEHOLDER.id.desc()).all()
        data = [{c.name: getattr(obj, c.name) for c in obj.__table__.columns} for obj in items]
        content = json.dumps(data, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=export.json"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/export/csv", tags=["export"])
def export_csv(request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        items = db.query(MODEL_PLACEHOLDER).order_by(MODEL_PLACEHOLDER.id.desc()).all()
        if not items:
            return StreamingResponse(
                io.BytesIO(b""),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=export.csv"},
            )
        cols = [c.name for c in items[0].__table__.columns]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for obj in items:
            writer.writerow({c: getattr(obj, c) for c in cols})
        return StreamingResponse(
            io.BytesIO(buf.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
"""
    main_src = main_src.replace("MODEL_PLACEHOLDER",         model_name)
    main_src = main_src.replace("APP_NAME_PLACEHOLDER",      app_name)
    main_src = main_src.replace("CREATE_FIELDS_PLACEHOLDER", create_fields)
    main_src = main_src.replace("UPDATE_FIELDS_PLACEHOLDER", update_fields)
    main_src = main_src.replace("OUT_FIELDS_PLACEHOLDER",    out_fields)
    main_src = main_src.replace("CTOR_ARGS_PLACEHOLDER",     ctor_args)
    main_src = main_src.replace("UPDATE_BODY_PLACEHOLDER",   update_body)
    (path / "main.py").write_text(main_src, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# HTML/JS fragment builders (unchanged from previous version)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# HTML helpers — static markup, IDs are always "inp-{field_name}"
# ─────────────────────────────────────────────────────────────────────────────

def _html_input_for_field(name: str, ftype: str) -> str:
    """
    Return one <div class="form-group"> block for a field.
    ID is always  inp-{name}  (create form) or  edit-{name}  (edit modal).
    The prefix is passed as `id_prefix`.
    """
    # Called with a prefix by the two callers below.
    raise NotImplementedError  # never called directly


def _html_create_field(name: str, ftype: str) -> str:
    return _html_field_block(name, ftype, prefix="inp")


def _html_edit_field(name: str, ftype: str) -> str:
    return _html_field_block(name, ftype, prefix="edit")


def _html_field_block(name: str, ftype: str, prefix: str) -> str:
    """
    Produce one labelled form-group block.
    ftype is the logical type ("string", "text", "integer", "float",
    "boolean", "date", "datetime") — mapped to the right HTML element.
    """
    fid   = f"{prefix}-{name}"
    label = name.replace("_", " ").title()

    lines = ['      <div class="form-group">']

    if ftype == "boolean":
        # Checkbox: wrap label+input together so the whole label is clickable
        lines.append(
            f'        <label class="checkbox-wrap" for="{fid}">'
            f'<input type="checkbox" id="{fid}" name="{name}" />'
            f' {label}</label>'
        )
    else:
        lines.append(
            f'        <label class="field-label" for="{fid}">{label}</label>'
        )
        if ftype == "text":
            lines.append(
                f'        <textarea id="{fid}" name="{name}" placeholder="{label}"></textarea>'
            )
        elif ftype == "integer":
            lines.append(
                f'        <input type="number" step="1" id="{fid}" name="{name}" placeholder="{label}" />'
            )
        elif ftype == "float":
            lines.append(
                f'        <input type="number" step="0.01" id="{fid}" name="{name}" placeholder="{label}" />'
            )
        elif ftype == "date":
            lines.append(
                f'        <input type="date" id="{fid}" name="{name}" />'
            )
        elif ftype == "datetime":
            lines.append(
                f'        <input type="datetime-local" id="{fid}" name="{name}" />'
            )
        else:  # string (default)
            lines.append(
                f'        <input type="text" id="{fid}" name="{name}" placeholder="{label}" />'
            )

    lines.append('      </div>')
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# _write_frontend
#
# Architecture: the schema is embedded as a JSON constant in script.js.
# All JavaScript functions (createItem, renderCard, openEdit, saveEdit) loop
# over this constant — no field name, type, or element ID is baked into any
# JS function body.  HTML elements still need static IDs so the browser can
# find them; those IDs are derived deterministically from the field name.
# ─────────────────────────────────────────────────────────────────────────────

def _write_frontend(path, display_name: str, safe_idea: str, schema: dict, app_type: str = "Custom CRUD App"):
    """
    Write index.html, login.html, style.css, and script.js.
    Additions over previous version:
    - Auth header (user display + logout)
    - Search bar, filter dropdown, sort control
    - 5 analytics stat cards (total, sum, avg, completion%, top category)
    - Export CSV + JSON buttons
    - All features remain schema-driven — no hardcoded field names
    """
    form_fields = schema["form_fields"]

    form_html = "\n".join(
        _html_create_field(f["api_key"], f["type"]) for f in form_fields
    )
    edit_html = "\n".join(
        _html_edit_field(f["api_key"], f["type"]) for f in form_fields
    )

    schema_entries = []
    for f in form_fields:
        req = "true" if f.get("required", False) else "false"
        schema_entries.append(
            f'  {{ name: "{f["api_key"]}", type: "{f["type"]}", required: {req} }}'
        )
    schema_json = "[\n" + ",\n".join(schema_entries) + "\n]"

    # ── login.html ───────────────────────────────────────────────────
    login_html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
        f"  <title>{display_name} — Login</title>\n"
        "  <link rel=\"stylesheet\" href=\"/static/style.css\" />\n"
        "</head>\n"
        "<body>\n"
        "  <div class=\"auth-wrap\">\n"
        "    <div class=\"auth-box\">\n"
        f"      <h1>&#9889; {display_name}</h1>\n"
        "      <p class=\"tagline\" style=\"text-align:center;margin-bottom:1.5rem\">Sign in to continue</p>\n"
        "      <div id=\"auth-error\" class=\"auth-error hidden\"></div>\n"
        "      <form id=\"login-form\" class=\"auth-form\">\n"
        "        <div class=\"form-group\">\n"
        "          <label class=\"field-label\">Username</label>\n"
        "          <input type=\"text\" id=\"auth-username\" placeholder=\"Username\" autocomplete=\"username\" />\n"
        "        </div>\n"
        "        <div class=\"form-group\">\n"
        "          <label class=\"field-label\">Password</label>\n"
        "          <input type=\"password\" id=\"auth-password\" placeholder=\"Password\" autocomplete=\"current-password\" />\n"
        "        </div>\n"
        "        <button type=\"button\" class=\"btn btn-primary\" style=\"width:100%;margin-top:0.5rem\" onclick=\"doLogin()\">Sign In</button>\n"
        "        <button type=\"button\" class=\"btn btn-secondary\" style=\"width:100%;margin-top:0.5rem\" onclick=\"doRegister()\">Create Account</button>\n"
        "      </form>\n"
        "    </div>\n"
        "  </div>\n"
        "  <script>\n"
        "    async function doLogin() {\n"
        "      const u = document.getElementById('auth-username').value.trim();\n"
        "      const p = document.getElementById('auth-password').value;\n"
        "      if (!u || !p) { showAuthError('Please enter username and password'); return; }\n"
        "      const fd = new FormData();\n"
        "      fd.append('username', u); fd.append('password', p);\n"
        "      const res = await fetch('/login', { method: 'POST', body: fd, redirect: 'follow' });\n"
        "      if (res.ok || res.redirected) { window.location.href = '/'; }\n"
        "      else { showAuthError('Invalid username or password'); }\n"
        "    }\n"
        "    async function doRegister() {\n"
        "      const u = document.getElementById('auth-username').value.trim();\n"
        "      const p = document.getElementById('auth-password').value;\n"
        "      if (!u || !p) { showAuthError('Please enter username and password'); return; }\n"
        "      if (p.length < 4) { showAuthError('Password must be at least 4 characters'); return; }\n"
        "      const fd = new FormData();\n"
        "      fd.append('username', u); fd.append('password', p);\n"
        "      const res = await fetch('/register', { method: 'POST', body: fd, redirect: 'follow' });\n"
        "      if (res.ok || res.redirected) { window.location.href = '/'; }\n"
        "      else { showAuthError('Username already taken'); }\n"
        "    }\n"
        "    function showAuthError(msg) {\n"
        "      const el = document.getElementById('auth-error');\n"
        "      el.textContent = msg; el.classList.remove('hidden');\n"
        "    }\n"
        "    document.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });\n"
        "  </script>\n"
        "</body>\n"
        "</html>\n"
    )
    (path / "login.html").write_text(login_html, encoding="utf-8")

    # ── index.html ───────────────────────────────────────────────────
    index_html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
        f"  <title>{display_name}</title>\n"
        "  <link rel=\"stylesheet\" href=\"/static/style.css\" />\n"
        "  <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>\n"
        "</head>\n"
        "<body>\n"
        "  <div class=\"app\">\n"
        "    <header>\n"
        "      <div class=\"header-inner\">\n"
        "        <div>\n"
        f"          <h1>&#9889; {display_name}</h1>\n"
        f"          <p class=\"tagline\">{safe_idea}</p>\n"
        f"          <span class=\"app-type-badge\">&#127381; {app_type}</span>\n"
        "        </div>\n"
        "        <div class=\"header-auth\">\n"
        "          <span class=\"auth-user\" id=\"header-user\"></span>\n"
        "          <button class=\"btn btn-ghost btn-sm\" onclick=\"doLogout()\">&#x2192; Logout</button>\n"
        "        </div>\n"
        "      </div>\n"
        "    </header>\n"
        "\n"
        "    <!-- Analytics bar (5 cards) -->\n"
        "    <div class=\"analytics\" id=\"analytics\">\n"
        "      <div class=\"stat-card\"><div class=\"stat-val\" id=\"stat-total\">0</div><div class=\"stat-label\">Total Items</div></div>\n"
        "      <div class=\"stat-card\"><div class=\"stat-val\" id=\"stat-numeric\">0</div><div class=\"stat-label\" id=\"stat-numeric-label\">Total</div></div>\n"
        "      <div class=\"stat-card\"><div class=\"stat-val\" id=\"stat-avg\">0</div><div class=\"stat-label\" id=\"stat-avg-label\">Average</div></div>\n"
        "      <div class=\"stat-card\"><div class=\"stat-val\" id=\"stat-bool\">—</div><div class=\"stat-label\" id=\"stat-bool-label\">Completed</div></div>\n"
        "      <div class=\"stat-card\"><div class=\"stat-val\" id=\"stat-top\" style=\"font-size:1.1rem\">—</div><div class=\"stat-label\" id=\"stat-top-label\">Top Category</div></div>\n"
        "    </div>\n"
        "\n"
        "    <!-- Charts -->\n"
        "    <div class=\"charts-row\">\n"
        "      <div class=\"chart-card\"><canvas id=\"barChart\"></canvas></div>\n"
        "      <div class=\"chart-card\"><canvas id=\"pieChart\"></canvas></div>\n"
        "    </div>\n"
        "\n"
        "    <!-- Add form -->\n"
        "    <section class=\"card\">\n"
        "      <h2>Add New Item</h2>\n"
        + form_html + "\n"
        "      <button class=\"btn btn-primary\" onclick=\"createItem()\">+ Add Item</button>\n"
        "    </section>\n"
        "\n"
        "    <!-- List + controls -->\n"
        "    <section class=\"card\">\n"
        "      <div class=\"list-header\">\n"
        "        <h2>All Items</h2>\n"
        "        <div class=\"list-controls\">\n"
        "          <input type=\"text\" id=\"search-input\" placeholder=\"&#128269; Search...\" oninput=\"applyFilters()\" style=\"width:180px;padding:0.45rem 0.8rem;font-size:0.85rem\" />\n"
        "          <select id=\"sort-select\" onchange=\"applyFilters()\">\n"
        "            <option value=\"newest\">Newest first</option>\n"
        "            <option value=\"oldest\">Oldest first</option>\n"
        "          </select>\n"
        "          <button class=\"btn btn-secondary btn-sm\" onclick=\"fetchItems()\">&#8635; Refresh</button>\n"
        "          <button class=\"btn btn-export\" onclick=\"exportData('csv')\">&#8595; CSV</button>\n"
        "          <button class=\"btn btn-export\" onclick=\"exportData('json')\">&#8595; JSON</button>\n"
        "        </div>\n"
        "      </div>\n"
        "      <div id=\"loading\" class=\"loading hidden\"><span class=\"spinner\"></span> Loading...</div>\n"
        "      <div id=\"items-list\"><p class=\"empty\">&#128218; No items yet — add your first one above!</p></div>\n"
        "    </section>\n"
        "\n"
        "    <!-- Edit modal -->\n"
        "    <div id=\"edit-modal\" class=\"modal hidden\">\n"
        "      <div class=\"modal-box\">\n"
        "        <h3>Edit Item</h3>\n"
        "        <input type=\"hidden\" id=\"edit-id\" />\n"
        + edit_html + "\n"
        "        <div class=\"modal-actions\">\n"
        "          <button class=\"btn btn-primary\" onclick=\"saveEdit()\">Save</button>\n"
        "          <button class=\"btn btn-ghost\"   onclick=\"closeModal()\">Cancel</button>\n"
        "        </div>\n"
        "      </div>\n"
        "    </div>\n"
        "  </div>\n"
        "  <script src=\"/static/script.js\"></script>\n"
        "</body>\n"
        "</html>\n"
    )
    (path / "index.html").write_text(index_html, encoding="utf-8")

    # ── style.css ─────────────────────────────────────────────────────
    (path / "style.css").write_text(
        """\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0f172a;
  --bg2: #1e293b;
  --surface: rgba(30,41,59,0.95);
  --border: rgba(148,163,184,0.12);
  --accent: #6366f1;
  --accent2: #f43f5e;
  --accent3: #10b981;
  --accent4: #f59e0b;
  --text: #f1f5f9;
  --muted: #94a3b8;
  --radius: 16px;
  --shadow: 0 10px 30px rgba(0,0,0,0.35);
}
body {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
  background-attachment: fixed;
  color: var(--text);
  font-family: 'Segoe UI', system-ui, sans-serif;
  min-height: 100vh;
  padding: 2rem 1rem;
}

/* Auth page */
.auth-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 1rem; }
.auth-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2.5rem 2rem;
  width: 100%; max-width: 400px;
  box-shadow: var(--shadow);
}
.auth-box h1 {
  font-size: 1.8rem; text-align: center; margin-bottom: 0.3rem;
  background: linear-gradient(135deg, #818cf8, #6366f1, #a78bfa);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.auth-form { display: flex; flex-direction: column; gap: 0.85rem; }
.auth-error { background: rgba(244,63,94,0.15); border: 1px solid rgba(244,63,94,0.4); color: #f87171; padding: 0.65rem 1rem; border-radius: 8px; font-size: 0.87rem; margin-bottom: 0.5rem; }
.auth-error.hidden { display: none; }

.app { max-width: 1100px; margin: 0 auto; }

/* Header */
header { margin-bottom: 2rem; }
.header-inner { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; }
header h1 {
  font-size: 2.2rem; letter-spacing: -1px;
  background: linear-gradient(135deg, #818cf8, #6366f1, #a78bfa);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.tagline { color: var(--muted); margin-top: 0.3rem; font-size: 0.92rem; }
.app-type-badge {
  display: inline-block; margin-top: 0.5rem;
  background: rgba(99,102,241,0.15); border: 1px solid rgba(99,102,241,0.35);
  color: #a5b4fc; font-size: 0.75rem; font-weight: 600;
  padding: 0.25rem 0.75rem; border-radius: 20px; letter-spacing: 0.5px;
  text-transform: uppercase;
}
.header-auth { display: flex; align-items: center; gap: 0.75rem; flex-shrink: 0; }
.auth-user { font-size: 0.82rem; color: var(--muted); }

/* Analytics — 5 cards */
.analytics {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 1.5rem;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.1rem 1rem;
  box-shadow: var(--shadow);
  text-align: center;
  transition: transform 0.2s, box-shadow 0.2s;
}
.stat-card:hover { transform: translateY(-3px); box-shadow: 0 16px 40px rgba(0,0,0,0.4); }
.stat-val { font-size: 1.75rem; font-weight: 700; color: var(--accent); letter-spacing: -0.5px; }
.stat-label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; margin-top: 0.2rem; }

/* Charts */
.charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 1.5rem; }
.chart-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 1.25rem; box-shadow: var(--shadow);
  max-height: 260px; display: flex; align-items: center; justify-content: center;
}
.chart-card canvas { max-height: 220px; width: 100% !important; }

/* Card */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: var(--shadow);
}
h2 { font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: var(--accent); margin-bottom: 1rem; }
.form-group { margin-bottom: 0.85rem; }
.field-label { display: block; font-size: 0.78rem; color: var(--muted); margin-bottom: 0.3rem; text-transform: uppercase; letter-spacing: 0.5px; }
.checkbox-wrap { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; color: var(--text); cursor: pointer; margin-top: 0.25rem; }
input, textarea, select {
  width: 100%; background: rgba(15,23,42,0.8); border: 1px solid var(--border);
  border-radius: 10px; padding: 0.7rem 1rem; color: var(--text);
  font-size: 0.95rem; font-family: inherit; outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}
input[type="checkbox"] { width: auto; padding: 0; margin: 0; accent-color: var(--accent); }
input[type="date"], input[type="datetime-local"] { color-scheme: dark; }
input:focus, textarea:focus, select:focus {
  border-color: var(--accent); box-shadow: 0 0 0 3px rgba(99,102,241,0.2);
}
textarea { min-height: 80px; resize: vertical; }
select option { background: var(--bg2); }

/* Buttons */
.btn {
  padding: 0.65rem 1.4rem; border-radius: 10px; border: none;
  font-size: 0.9rem; font-weight: 600; cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s, background 0.15s; font-family: inherit;
}
.btn-sm { padding: 0.4rem 0.9rem; font-size: 0.82rem; }
.btn-primary { background: var(--accent); color: #fff; box-shadow: 0 4px 14px rgba(99,102,241,0.4); }
.btn-primary:hover { background: #818cf8; transform: translateY(-2px) scale(1.02); box-shadow: 0 6px 20px rgba(99,102,241,0.55); }
.btn-primary:active { transform: scale(0.98); }
.btn-secondary { background: transparent; border: 1px solid var(--border); color: var(--text); }
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }
.btn-ghost { background: transparent; color: var(--muted); }
.btn-ghost:hover { color: var(--text); }
.btn-danger { background: transparent; border: 1px solid rgba(244,63,94,0.4); color: var(--accent2); padding: 0.3rem 0.8rem; font-size: 0.8rem; }
.btn-danger:hover { background: var(--accent2); color: #fff; transform: scale(1.05); box-shadow: 0 4px 12px rgba(244,63,94,0.4); }
.btn-edit { background: transparent; border: 1px solid var(--border); color: var(--muted); padding: 0.3rem 0.8rem; font-size: 0.8rem; margin-right: 0.4rem; }
.btn-edit:hover { border-color: var(--accent); color: var(--accent); transform: scale(1.05); }
.btn-export { background: rgba(16,185,129,0.12); border: 1px solid rgba(16,185,129,0.35); color: var(--accent3); padding: 0.4rem 0.9rem; font-size: 0.82rem; border-radius: 8px; }
.btn-export:hover { background: var(--accent3); color: #fff; transform: scale(1.03); }

/* List controls */
.list-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; gap: 0.6rem; }
.list-controls { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }

/* Item cards */
.item-card {
  background: rgba(15,23,42,0.6); border: 1px solid var(--border);
  border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 0.75rem;
  display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;
  animation: fadeIn 0.25s ease; transition: border-color 0.2s, transform 0.15s;
}
.item-card:hover { border-color: rgba(99,102,241,0.3); transform: translateY(-1px); }
@keyframes fadeIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
.item-info { flex: 1; min-width: 0; }
.item-id { font-size: 0.68rem; color: var(--accent); opacity: 0.6; margin-bottom: 0.35rem; }
.item-field { font-size: 0.87rem; margin-bottom: 0.2rem; word-break: break-word; }
.field-key { color: var(--muted); font-size: 0.73rem; text-transform: uppercase; letter-spacing: 0.4px; margin-right: 0.35rem; }
.field-val { color: var(--text); }
.item-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }

/* Loading */
.loading { display: flex; align-items: center; gap: 0.6rem; color: var(--muted); padding: 1rem; font-size: 0.9rem; }
.loading.hidden { display: none; }
.spinner { width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Empty */
.empty { color: var(--muted); text-align: center; padding: 2.5rem; font-size: 0.95rem; }

/* Modal */
.modal { position: fixed; inset: 0; background: rgba(0,0,0,0.75); display: flex; align-items: center; justify-content: center; z-index: 999; backdrop-filter: blur(4px); }
.modal.hidden { display: none; }
.modal-box { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 2rem; width: 100%; max-width: 480px; max-height: 90vh; overflow-y: auto; box-shadow: var(--shadow); }
.modal-box h3 { margin-bottom: 1.2rem; font-size: 1rem; }
.modal-actions { display: flex; gap: 0.75rem; margin-top: 1rem; }

/* Toast */
.toast {
  position: fixed; bottom: 2rem; right: 2rem;
  background: var(--accent); color: #fff;
  padding: 0.75rem 1.4rem; border-radius: 10px;
  font-size: 0.9rem; animation: slideUp 0.3s ease; z-index: 1000;
  box-shadow: 0 6px 24px rgba(99,102,241,0.5);
}
.toast.toast-error { background: var(--accent2); box-shadow: 0 6px 24px rgba(244,63,94,0.5); }
.toast.toast-success { background: var(--accent3); box-shadow: 0 6px 24px rgba(16,185,129,0.5); }
@keyframes slideUp { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:none; } }

@media (max-width: 900px) {
  .analytics { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 600px) {
  .analytics { grid-template-columns: 1fr 1fr; }
  .charts-row { grid-template-columns: 1fr; }
  .header-inner { flex-direction: column; align-items: flex-start; }
}
""",
        encoding="utf-8",
    )

    # ── script.js ─────────────────────────────────────────────────────
    script_js = (
        "const API = window.location.origin;\n"
        "\n"
        "// App metadata embedded at generation time\n"
        f"const APP_TYPE = \"{app_type}\";\n"
        "console.log('%c IdeaForge ', 'background:#6366f1;color:#fff;font-weight:bold;border-radius:4px', "
        "'App Type:', APP_TYPE);\n"
        "\n"
        "// Schema embedded at generation time — single source of truth.\n"
        "const SCHEMA_FIELDS = " + schema_json + ";\n"
        "\n"
        "// ── Derived field lists ───────────────────────────────────────────────────────\n"
        "const NUMERIC_FIELDS = SCHEMA_FIELDS.filter(f => f.type === 'integer' || f.type === 'float');\n"
        "const BOOLEAN_FIELDS = SCHEMA_FIELDS.filter(f => f.type === 'boolean');\n"
        "const STRING_FIELDS  = SCHEMA_FIELDS.filter(f => f.type === 'string' || f.type === 'text');\n"
        "const FIRST_NUMERIC  = NUMERIC_FIELDS[0] || null;\n"
        "const FIRST_STRING   = SCHEMA_FIELDS.filter(f => f.type === 'string')[0] || null;\n"
        "const CAT_KEYWORDS   = ['category','status','type','group','tag','priority','label','billing'];\n"
        "const CAT_FIELD      = STRING_FIELDS.find(f => CAT_KEYWORDS.some(k => f.name.includes(k)))\n"
        "                       || STRING_FIELDS[1] || FIRST_STRING;\n"
        "\n"
        "// Set dynamic analytics labels\n"
        "if (FIRST_NUMERIC) {\n"
        "  const label = FIRST_NUMERIC.name.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());\n"
        "  document.getElementById('stat-numeric-label').textContent = 'Total ' + label;\n"
        "  document.getElementById('stat-avg-label').textContent = 'Avg ' + label;\n"
        "}\n"
        "if (BOOLEAN_FIELDS[0]) {\n"
        "  document.getElementById('stat-bool-label').textContent =\n"
        "    BOOLEAN_FIELDS[0].name.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());\n"
        "}\n"
        "if (CAT_FIELD) {\n"
        "  document.getElementById('stat-top-label').textContent =\n"
        "    'Top ' + CAT_FIELD.name.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());\n"
        "}\n"
        "\n"
        "// Chart instances\n"
        "let _barChart = null;\n"
        "let _pieChart = null;\n"
        "\n"
        "// All items cache for client-side filtering\n"
        "let _allItems = [];\n"
        "\n"
        "// ── Auth ──────────────────────────────────────────────────────────────────────\n"
        "\n"
        "async function initAuth() {\n"
        "  try {\n"
        "    const res = await fetch(API + '/api/me');\n"
        "    if (!res.ok) { window.location.href = '/login'; return; }\n"
        "    const data = await res.json();\n"
        "    if (!data.authenticated) { window.location.href = '/login'; return; }\n"
        "    const el = document.getElementById('header-user');\n"
        "    if (el) el.textContent = '👤 ' + data.user;\n"
        "  } catch(e) { /* offline — don't redirect */ }\n"
        "}\n"
        "\n"
        "async function doLogout() {\n"
        "  await fetch(API + '/logout', { method: 'POST' });\n"
        "  window.location.href = '/login';\n"
        "}\n"
        "\n"
        "// ── Utilities ─────────────────────────────────────────────────────────────────\n"
        "\n"
        "function escHtml(str) {\n"
        "  return String(str == null ? '' : str)\n"
        "    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n"
        "}\n"
        "\n"
        "function showToast(msg, type) {\n"
        "  const t = document.createElement('div');\n"
        "  t.className = 'toast' + (type ? ' toast-' + type : '');\n"
        "  t.textContent = msg;\n"
        "  document.body.appendChild(t);\n"
        "  setTimeout(() => t.remove(), 2800);\n"
        "}\n"
        "\n"
        "function readField(field, prefix) {\n"
        "  const el = document.getElementById(prefix + '-' + field.name);\n"
        "  if (!el) return undefined;\n"
        "  if (field.type === 'boolean') return el.checked;\n"
        "  if (field.type === 'integer') { const v = parseInt(el.value, 10); return isNaN(v) ? 0 : v; }\n"
        "  if (field.type === 'float')   { const v = parseFloat(el.value);   return isNaN(v) ? 0.0 : v; }\n"
        "  if (field.type === 'date' || field.type === 'datetime') return el.value || '';\n"
        "  return el.value.trim();\n"
        "}\n"
        "\n"
        "function buildPayload(prefix) {\n"
        "  return SCHEMA_FIELDS.reduce((acc, field) => {\n"
        "    acc[field.name] = readField(field, prefix); return acc;\n"
        "  }, {});\n"
        "}\n"
        "\n"
        "function resetForm() {\n"
        "  SCHEMA_FIELDS.forEach(field => {\n"
        "    const el = document.getElementById('inp-' + field.name);\n"
        "    if (!el) return;\n"
        "    if (field.type === 'boolean') el.checked = false;\n"
        "    else el.value = '';\n"
        "  });\n"
        "}\n"
        "\n"
        "function validateForm() {\n"
        "  for (const field of SCHEMA_FIELDS) {\n"
        "    const el = document.getElementById('inp-' + field.name);\n"
        "    if (!el) continue;\n"
        "    const raw = el.value;\n"
        "    const label = field.name.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());\n"
        "    if (field.required && field.type !== 'boolean') {\n"
        "      if (!raw || !raw.trim()) { showToast(label + ' is required', 'error'); el.focus(); return false; }\n"
        "    }\n"
        "    if (raw && raw.trim()) {\n"
        "      if (field.type === 'integer' && isNaN(parseInt(raw, 10))) { showToast(label + ' must be a whole number', 'error'); el.focus(); return false; }\n"
        "      if (field.type === 'float'   && isNaN(parseFloat(raw)))   { showToast(label + ' must be a number', 'error'); el.focus(); return false; }\n"
        "    }\n"
        "  }\n"
        "  return true;\n"
        "}\n"
        "\n"
        "// ── Advanced Analytics ────────────────────────────────────────────────────────\n"
        "\n"
        "function updateAnalytics(items) {\n"
        "  document.getElementById('stat-total').textContent = items.length;\n"
        "\n"
        "  if (FIRST_NUMERIC && items.length) {\n"
        "    const vals = items.map(i => Number(i[FIRST_NUMERIC.name]) || 0);\n"
        "    const total = vals.reduce((a, b) => a + b, 0);\n"
        "    const avg   = total / vals.length;\n"
        "    const fmt   = v => FIRST_NUMERIC.type === 'float' ? v.toFixed(2) : Math.round(v);\n"
        "    document.getElementById('stat-numeric').textContent = fmt(total);\n"
        "    document.getElementById('stat-avg').textContent     = fmt(avg);\n"
        "  } else {\n"
        "    document.getElementById('stat-numeric').textContent = '—';\n"
        "    document.getElementById('stat-avg').textContent     = '—';\n"
        "  }\n"
        "\n"
        "  if (BOOLEAN_FIELDS[0] && items.length) {\n"
        "    const bfn = BOOLEAN_FIELDS[0].name;\n"
        "    const pct = Math.round(100 * items.filter(i => i[bfn]).length / items.length);\n"
        "    document.getElementById('stat-bool').textContent = pct + '%';\n"
        "  } else {\n"
        "    document.getElementById('stat-bool').textContent = '—';\n"
        "  }\n"
        "\n"
        "  if (CAT_FIELD && items.length) {\n"
        "    const counts = {};\n"
        "    items.forEach(i => { const k = String(i[CAT_FIELD.name] || 'Unknown'); counts[k] = (counts[k]||0)+1; });\n"
        "    const top = Object.entries(counts).sort((a,b)=>b[1]-a[1])[0];\n"
        "    document.getElementById('stat-top').textContent = top ? top[0].slice(0,14) : '—';\n"
        "  } else {\n"
        "    document.getElementById('stat-top').textContent = '—';\n"
        "  }\n"
        "}\n"
        "\n"
        "// ── Charts ────────────────────────────────────────────────────────────────────\n"
        "\n"
        "function updateCharts(items) {\n"
        "  if (_barChart) { _barChart.destroy(); _barChart = null; }\n"
        "  if (_pieChart) { _pieChart.destroy(); _pieChart = null; }\n"
        "  if (!items.length) return;\n"
        "  const CHART_DEFAULTS = {\n"
        "    plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },\n"
        "    scales: {\n"
        "      x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } },\n"
        "      y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.08)' } },\n"
        "    },\n"
        "  };\n"
        "  const barCtx = document.getElementById('barChart').getContext('2d');\n"
        "  if (FIRST_STRING && FIRST_NUMERIC) {\n"
        "    const slice = items.slice(0, 12);\n"
        "    _barChart = new Chart(barCtx, {\n"
        "      type: 'bar',\n"
        "      data: {\n"
        "        labels: slice.map(i => String(i[FIRST_STRING.name]||'').slice(0,14)),\n"
        "        datasets: [{ label: FIRST_NUMERIC.name.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase()),\n"
        "          data: slice.map(i => Number(i[FIRST_NUMERIC.name])||0),\n"
        "          backgroundColor: 'rgba(99,102,241,0.7)', borderColor: '#818cf8', borderWidth:1, borderRadius:4 }]\n"
        "      },\n"
        "      options: { ...CHART_DEFAULTS, responsive:true, maintainAspectRatio:true },\n"
        "    });\n"
        "  } else { barCtx.canvas.parentElement.style.display='none'; }\n"
        "\n"
        "  const pieCtx = document.getElementById('pieChart').getContext('2d');\n"
        "  if (CAT_FIELD) {\n"
        "    const counts = {};\n"
        "    items.forEach(i => { const k=String(i[CAT_FIELD.name]||'Unknown'); counts[k]=(counts[k]||0)+1; });\n"
        "    const COLORS = ['#6366f1','#f43f5e','#10b981','#f59e0b','#3b82f6','#a78bfa','#06b6d4','#84cc16'];\n"
        "    _pieChart = new Chart(pieCtx, {\n"
        "      type: 'pie',\n"
        "      data: { labels: Object.keys(counts),\n"
        "        datasets: [{ data: Object.values(counts),\n"
        "          backgroundColor: COLORS.slice(0,Object.keys(counts).length),\n"
        "          borderWidth:2, borderColor:'#1e293b' }] },\n"
        "      options: { responsive:true, maintainAspectRatio:true,\n"
        "        plugins: { legend: { position:'right', labels:{ color:'#94a3b8', font:{size:10}, boxWidth:12 } } } },\n"
        "    });\n"
        "  } else { pieCtx.canvas.parentElement.style.display='none'; }\n"
        "}\n"
        "\n"
        "// ── Search / Filter / Sort ────────────────────────────────────────────────────\n"
        "\n"
        "function applyFilters() {\n"
        "  const q    = (document.getElementById('search-input')?.value || '').toLowerCase();\n"
        "  const sort = document.getElementById('sort-select')?.value || 'newest';\n"
        "  let items  = [..._allItems];\n"
        "  if (q) {\n"
        "    items = items.filter(item =>\n"
        "      STRING_FIELDS.some(f => String(item[f.name]||'').toLowerCase().includes(q))\n"
        "    );\n"
        "  }\n"
        "  if (sort === 'oldest') items = items.slice().reverse();\n"
        "  renderItems(items);\n"
        "  updateAnalytics(items);\n"
        "  updateCharts(items);\n"
        "}\n"
        "\n"
        "// ── Render items list ─────────────────────────────────────────────────────────\n"
        "\n"
        "function renderItems(items) {\n"
        "  const list = document.getElementById('items-list');\n"
        "  if (!items.length) {\n"
        "    list.innerHTML = '<p class=\"empty\">&#128218; No items match your search.</p>';\n"
        "    return;\n"
        "  }\n"
        "  const typeMap = {};\n"
        "  SCHEMA_FIELDS.forEach(f => { typeMap[f.name] = f.type; });\n"
        "  const SKIP = new Set(['id', 'created_at']);\n"
        "  list.innerHTML = items.map(item => {\n"
        "    window._itemCache[item.id] = item;\n"
        "    const id = Number(item.id);\n"
        "    let fieldLines = '';\n"
        "    Object.entries(item).forEach(([key, value]) => {\n"
        "      if (SKIP.has(key)) return;\n"
        "      const label  = key.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());\n"
        "      const ftype  = typeMap[key] || 'string';\n"
        "      let display;\n"
        "      if (ftype === 'boolean')    display = value ? '&#10003; Yes' : '&#10007; No';\n"
        "      else if (ftype === 'float') display = Number(value).toFixed(2);\n"
        "      else                        display = escHtml(String(value == null ? '' : value));\n"
        "      fieldLines += `<div class=\"item-field\"><span class=\"field-key\">${label}:</span> <span class=\"field-val\">${display}</span></div>`;\n"
        "    });\n"
        "    return `<div class=\"item-card\">\n"
        "      <div class=\"item-info\"><div class=\"item-id\">#${id}</div>${fieldLines}</div>\n"
        "      <div class=\"item-actions\">\n"
        "        <button class=\"btn btn-edit\" onclick=\"openEdit(${id})\">Edit</button>\n"
        "        <button class=\"btn btn-danger\" onclick=\"deleteItem(${id})\">Delete</button>\n"
        "      </div></div>`;\n"
        "  }).join('');\n"
        "}\n"
        "\n"
        "// ── Fetch & render ────────────────────────────────────────────────────────────\n"
        "\n"
        "async function fetchItems() {\n"
        "  const loading = document.getElementById('loading');\n"
        "  loading.classList.remove('hidden');\n"
        "  document.getElementById('items-list').innerHTML = '';\n"
        "  try {\n"
        "    const res = await fetch(API + '/items');\n"
        "    if (res.status === 401) { window.location.href = '/login'; return; }\n"
        "    if (!res.ok) {\n"
        "      const detail = await res.text();\n"
        "      loading.classList.add('hidden');\n"
        "      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Failed to load'), 'error');\n"
        "      return;\n"
        "    }\n"
        "    const items = await res.json();\n"
        "    loading.classList.add('hidden');\n"
        "    _allItems = items;\n"
        "    window._itemCache = {};\n"
        "    if (!items.length) {\n"
        "      document.getElementById('items-list').innerHTML = '<p class=\"empty\">&#128218; No items yet — add your first one above!</p>';\n"
        "      updateAnalytics([]);\n"
        "      updateCharts([]);\n"
        "      return;\n"
        "    }\n"
        "    applyFilters();\n"
        "  } catch (e) {\n"
        "    loading.classList.add('hidden');\n"
        "    showToast('Cannot reach backend: ' + e.message, 'error');\n"
        "  }\n"
        "}\n"
        "\n"
        "// ── Create ────────────────────────────────────────────────────────────────────\n"
        "\n"
        "async function createItem() {\n"
        "  if (!validateForm()) return;\n"
        "  const payload = buildPayload('inp');\n"
        "  try {\n"
        "    const res = await fetch(API + '/items', {\n"
        "      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),\n"
        "    });\n"
        "    if (res.status === 401) { window.location.href = '/login'; return; }\n"
        "    if (!res.ok) {\n"
        "      const detail = await res.text();\n"
        "      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Unknown error'), 'error');\n"
        "      return;\n"
        "    }\n"
        "    resetForm();\n"
        "    showToast('Item created!', 'success');\n"
        "    fetchItems();\n"
        "  } catch (e) { showToast('Create failed: ' + e.message, 'error'); }\n"
        "}\n"
        "\n"
        "// ── Delete ────────────────────────────────────────────────────────────────────\n"
        "\n"
        "async function deleteItem(id) {\n"
        "  if (!confirm('Delete this item?')) return;\n"
        "  try {\n"
        "    const res = await fetch(API + '/items/' + id, { method: 'DELETE' });\n"
        "    if (res.status === 401) { window.location.href = '/login'; return; }\n"
        "    if (!res.ok) {\n"
        "      const detail = await res.text();\n"
        "      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Delete failed'), 'error');\n"
        "      return;\n"
        "    }\n"
        "    showToast('Deleted', 'success');\n"
        "    fetchItems();\n"
        "  } catch (e) { showToast('Delete failed: ' + e.message, 'error'); }\n"
        "}\n"
        "\n"
        "// ── Edit modal ────────────────────────────────────────────────────────────────\n"
        "\n"
        "function openEdit(id) {\n"
        "  const item = (window._itemCache || {})[id];\n"
        "  if (!item) { showToast('Item not found', 'error'); return; }\n"
        "  document.getElementById('edit-id').value = id;\n"
        "  SCHEMA_FIELDS.forEach(field => {\n"
        "    const el = document.getElementById('edit-' + field.name);\n"
        "    if (!el) return;\n"
        "    const v = item[field.name];\n"
        "    if (field.type === 'boolean')      el.checked = !!v;\n"
        "    else if (field.type === 'integer')  el.value = (v != null) ? String(parseInt(v,10)||0) : '0';\n"
        "    else if (field.type === 'float')    el.value = (v != null) ? String(parseFloat(v)||0) : '0';\n"
        "    else                                el.value = (v != null) ? v : '';\n"
        "  });\n"
        "  document.getElementById('edit-modal').classList.remove('hidden');\n"
        "}\n"
        "\n"
        "function closeModal() { document.getElementById('edit-modal').classList.add('hidden'); }\n"
        "\n"
        "async function saveEdit() {\n"
        "  const id = document.getElementById('edit-id').value;\n"
        "  const payload = buildPayload('edit');\n"
        "  try {\n"
        "    const res = await fetch(API + '/items/' + id, {\n"
        "      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),\n"
        "    });\n"
        "    if (res.status === 401) { window.location.href = '/login'; return; }\n"
        "    if (!res.ok) {\n"
        "      const detail = await res.text();\n"
        "      showToast('Error ' + res.status + ': ' + (detail.slice(0,120) || 'Unknown error'), 'error');\n"
        "      return;\n"
        "    }\n"
        "    closeModal();\n"
        "    showToast('Updated!', 'success');\n"
        "    fetchItems();\n"
        "  } catch (e) { showToast('Update failed: ' + e.message, 'error'); }\n"
        "}\n"
        "\n"
        "// ── Export ────────────────────────────────────────────────────────────────────\n"
        "\n"
        "function exportData(fmt) {\n"
        "  const a = document.createElement('a');\n"
        "  a.href = API + '/export/' + fmt;\n"
        "  a.download = 'export.' + fmt;\n"
        "  document.body.appendChild(a);\n"
        "  a.click();\n"
        "  a.remove();\n"
        "  showToast('Downloading ' + fmt.toUpperCase() + '...', 'success');\n"
        "}\n"
        "\n"
        "// ── Init ──────────────────────────────────────────────────────────────────────\n"
        "\n"
        "window._itemCache = {};\n"
        "window.addEventListener('load', async () => {\n"
        "  await initAuth();\n"
        "  fetchItems();\n"
        "});\n"
    )
    (path / "script.js").write_text(script_js, encoding="utf-8")



def _write_bat(base, app_name, backend_port):
    port_str = str(backend_port)

    # ── Windows .bat ──────────────────────────────────────────────────
    bat_content = (
        "@echo off\n"
        "setlocal\n"
        "echo.\n"
        "echo  ========================================\n"
        "echo   IdeaForge — Starting " + app_name + "\n"
        "echo  ========================================\n"
        "echo.\n"
        "\n"
        "cd /d \"%~dp0backend\"\n"
        "\n"
        "echo [1/3] Installing dependencies...\n"
        "pip install -r requirements.txt --quiet --disable-pip-version-check\n"
        "if errorlevel 1 (\n"
        "    echo ERROR: pip install failed. Check your Python/pip setup.\n"
        "    pause\n"
        "    exit /b 1\n"
        ")\n"
        "echo       Dependencies OK\n"
        "echo.\n"
        "\n"
        "echo [2/3] Starting backend on port " + port_str + "...\n"
        "echo       URL: http://127.0.0.1:" + port_str + "\n"
        "echo       Press Ctrl+C to stop\n"
        "echo.\n"
        "\n"
        "echo [3/3] Opening browser...\n"
        "timeout /t 2 /nobreak >nul\n"
        "start http://127.0.0.1:" + port_str + "\n"
        "\n"
        "uvicorn main:app --host 127.0.0.1 --port " + port_str + " --reload\n"
        "\n"
        "echo.\n"
        "echo  Server stopped.\n"
        "pause\n"
    )
    (base / "run_app.bat").write_text(bat_content, encoding="utf-8")

    # ── Unix shell script ─────────────────────────────────────────────
    sh_content = (
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "echo ''\n"
        "echo '  ========================================'\n"
        "echo '   IdeaForge — Starting " + app_name + "'\n"
        "echo '  ========================================'\n"
        "echo ''\n"
        "\n"
        "SCRIPT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
        "cd \"$SCRIPT_DIR/backend\"\n"
        "\n"
        "echo '[1/3] Installing dependencies...'\n"
        "pip install -r requirements.txt --quiet --disable-pip-version-check\n"
        "echo '      Dependencies OK'\n"
        "echo ''\n"
        "\n"
        "echo '[2/3] Starting backend on port " + port_str + "...'\n"
        "echo '      URL: http://127.0.0.1:" + port_str + "'\n"
        "echo '      Press Ctrl+C to stop'\n"
        "echo ''\n"
        "\n"
        "(sleep 2 && python -m webbrowser http://127.0.0.1:" + port_str + " 2>/dev/null) &\n"
        "echo '[3/3] Browser will open shortly...'\n"
        "\n"
        "uvicorn main:app --host 127.0.0.1 --port " + port_str + " --reload\n"
    )
    sh_path = base / "run_app.sh"
    sh_path.write_text(sh_content, encoding="utf-8")
    try:
        sh_path.chmod(0o755)
    except Exception:
        pass