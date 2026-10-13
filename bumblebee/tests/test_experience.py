import pytest

from bumblebee.filters import experience_reason, html_to_text, parse_required_years


@pytest.mark.parametrize("text,expected", [
    ("You have 3+ years of experience building web apps.", 3),
    ("5+ years of professional software development experience", 5),
    ("At least 4 years of experience with Java", 4),
    ("Minimum of 3 years of industry experience", 3),
    ("3-5 years of experience in backend development", 3),
    ("1-3 years of experience", 1),
    ("2+ years experience with Python", 2),
    ("0-2 years of experience", 0),
    ("Three (3) years of experience", 3),
    ("three or more years of experience", 3),   # "three" -> 3
    ("more than 2 years of experience", 3),
    ("Experience: 4+ years", 4),
    ("7+ yrs of hands-on coding experience", 7),
    ("3 years' experience", 3),
])
def test_parse_years(text, expected):
    assert parse_required_years(text) == expected, text


@pytest.mark.parametrize("text", [
    "No experience required. New grads welcome!",
    "We've grown 10x over the past 5 years.",
    "Founded 12 years ago, Acme is a leader in payments.",
    "Our 401k vests after 3 years.",
    "",
])
def test_no_requirement(text):
    assert parse_required_years(text) is None


def test_preferred_section_ignored():
    desc = """
    Minimum qualifications:
    - BS in Computer Science
    - 1+ years of experience with Python

    Preferred qualifications:
    - 5+ years of experience with distributed systems
    """
    assert parse_required_years(desc) == 1


def test_inline_preferred_ignored():
    assert parse_required_years("3+ years of experience is a plus") is None
    assert parse_required_years("Nice to have: 4+ years of Go experience") is None


def test_required_after_preferred_section():
    desc = "Nice to have\n- 5+ years of experience with Rust\nRequirements\n- 3+ years of experience with Go"
    assert parse_required_years(desc) == 3


def test_degree_alternative_not_disqualifying():
    assert parse_required_years("Bachelor's degree in CS or 4+ years of equivalent experience") is None


def test_alternatives_in_one_line_take_minimum():
    line = "PhD, or MS with 2+ years of experience, or BS with 4+ years of experience"
    assert parse_required_years(line) == 2


def test_max_across_lines():
    assert parse_required_years("2+ years of Python experience\n4+ years of experience in SQL") == 4


def test_experience_reason_threshold():
    assert experience_reason("3+ years of experience", 2) == "requires 3+ years"
    assert experience_reason("2+ years of experience", 2) is None
    assert experience_reason(None, 2) is None


def test_html_to_text_greenhouse_double_escaped():
    raw = "&lt;ul&gt;&lt;li&gt;3+ years of experience&lt;/li&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "3+ years of experience" in text
    assert parse_required_years(text) == 3


@pytest.mark.parametrize("text,expected", [
    # A preferred word only covers its own clause, or what follows it.
    ("4+ years of experience building ML systems in production, ideally at an early stage startup", 4),
    ("3+ years building full-stack web applications, ideally with Django", 3),
    ("At least 2 years of software development experience, with approximately 5 years preferred.", 2),
    ("You have 2+ years of experience in a fulltime software engineering role. Ideally in SaaS.", 2),
    ("Ideally, 3+ years of experience with Go", None),
    ("5+ years operating AWS infrastructure in production", 5),
    ("We have shipped every week for 3 years in a row.", None),
])
def test_preferred_clauses_and_context(text, expected):
    assert parse_required_years(text) == expected, text


def _filter():
    from bumblebee.config import load_config
    from bumblebee.filters import JobFilter
    cfg = load_config()
    return JobFilter(cfg.criteria, cfg["locations"])


def _job(title, desc, source="ashby"):
    from bumblebee.models import Job
    return Job(id="1", company="X", title=title, location="San Francisco, CA", url="https://x", posted_at=None,
               source=source, description=desc)


@pytest.mark.parametrize("title,desc", [
    ("Software Engineer", "Requirements\n- 2+ years of experience in software development."),
    ("Software Engineer, Infra", "We are hiring an experienced infrastructure engineer to own CI."),
    ("Android Engineer", "Who you are\n- An experienced Android engineer who has shipped apps at scale."),
    ("Software Engineer, Payments", "Build payment systems used by millions."),          # no level at all
    ("Software Engineer", "You'll mentor junior engineers and early-career teammates."),
])
def test_entry_level_rejects(title, desc):
    assert _filter().entry_level(_job(title, desc)) is not None, desc


@pytest.mark.parametrize("title,desc", [
    ("Software Engineer, New Grad", "Build things alongside experienced engineers."),
    ("Software Engineer", "0-2 years of professional experience, or a strong internship record."),
    ("Software Engineer", "We hire at all experience levels."),
    ("Software Engineer", "We're hiring from junior through senior levels."),
    ("Software Engineer", "Seeking enthusiastic new graduates.\n- 2+ years building services, incl. internships."),
    ("Software Engineer", "Requirements\n- BS in CS and 1+ years of professional experience"),
    ("Machine Learning Engineer", "Handshake is hiring a Machine Learning Engineer I for the Growth team."),
    ("Associate Software Engineer, Frontend", "Build our web app."),
])
def test_entry_level_accepts(title, desc):
    assert _filter().entry_level(_job(title, desc)) is None, desc


def test_curated_new_grad_feed_skips_signal_gate():
    assert _filter().entry_level(_job("Software Engineer", None, source="simplify")) is None
    assert _filter().entry_level(_job("Software Engineer", "3+ years of experience", source="simplify")) is not None


def test_open_level_does_not_relax_years():
    # "All levels" opens the door, but 2+ years still isn't entry level.
    assert _filter().entry_level(_job("Software Engineer", "We hire at all levels.\n- 2+ years of experience")) is not None
