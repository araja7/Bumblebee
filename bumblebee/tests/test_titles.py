import pytest

from bumblebee.filters import TitleFilter


@pytest.fixture
def tf(cfg):
    return TitleFilter(cfg.criteria)


@pytest.mark.parametrize("title", [
    "Software Engineer",
    "Software Engineer, New Grad",
    "Software Engineer - New Grad (2027)",
    "New Grad Software Engineer",
    "Software Developer",
    "Backend Engineer",
    "Back-End Engineer",
    "Frontend Engineer",
    "Front End Developer",
    "Full Stack Engineer",
    "Full-Stack Software Engineer",
    "Entry Level Software Engineer",
    "Entry-Level Backend Developer",
    "Associate Software Engineer",
    "SWE I",
    "Software Engineer I",
    "Engineer I, Platform",
    "University Grad - Software Engineer",
    "Software Development Engineer I",
    "SDE I",
])
def test_includes(tf, title):
    assert tf.check(title) is None, title


@pytest.mark.parametrize("title,why", [
    ("Senior Software Engineer", "senior"),
    ("Sr. Software Engineer", "sr"),
    ("Staff Software Engineer", "staff"),
    ("Principal Engineer", "principal"),
    ("Lead Backend Engineer", "lead"),
    ("Engineering Manager", "manager"),
    ("Software Engineering Manager, New Grad Programs", "manager"),
    ("Director of Engineering", "director"),
    ("Software Engineer Intern", "intern"),
    ("Software Engineering Internship", "internship"),
    ("Software Engineer II", "ii"),
    ("Software Engineer III", "iii"),
    ("Solutions Architect", "architect"),
    ("Sales Engineer", "sales"),
    ("Data Scientist, New Grad", "data scientist"),
])
def test_excludes(tf, title, why):
    reason = tf.check(title)
    assert reason is not None and why in reason, (title, reason)


@pytest.mark.parametrize("title", [
    "Software Engineer 2",
    "Software Engineer, Level 3",
    "Product Designer, New Grad",      # level keyword but not engineering
    "Associate Counsel",
    "Associate Product Manager",
    "Recruiter",
    "Account Executive",
    "Mechanical Engineer, New Grad",
])
def test_rejects_other(tf, title):
    assert tf.check(title) is not None


def test_word_boundaries(tf):
    # "intern" must not match "internal"; "lead" must not match "leadership"
    assert tf.check("Software Engineer, Internal Tools") is None
    assert tf.check("Software Engineer, Leadership Platform") is None
    # "Engineer I" must not match "Engineer II" (handled by exclude), nor "Engineer In Test"
    assert tf.check("Engineer In Test") is not None


@pytest.mark.parametrize("title", [
    "RF Engineer I (R6108)",
    "Engineer I, Quality (R5976)",
    "Engineer I, PCB (R6065)",
    "Engineer I, Electromechanical (R6042)",
    "Thermal Associate Engineer (Winter 2027)",
    "Software Developer, Network Software Associate (Summer 2027)",
    "Software Engineer (Co-op)",
    "Supplier Quality Engineer Associate",
])
def test_rejects_hardware_and_intern_terms(tf, title):
    assert tf.check(title) is not None, title


def test_quality_only_excluded_without_software_signal(tf):
    assert tf.check("Software Engineer, Data Quality") is None
