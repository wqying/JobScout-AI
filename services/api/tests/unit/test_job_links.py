"""Job-link extraction rules (`job-links-v2`, DESIGN_DOC.md Section 13.3)."""

from __future__ import annotations

from app.monitoring.job_links import extract_job_links, rank_listing_candidates

PAGE = "https://acme.example/early-careers"

INFORMATIONAL_PAGE = """
<html><body>
  <nav>
    <a href="/careers">Careers</a>
    <a href="/careers/search">Search jobs</a>
    <a href="/careers/job-alerts">Get job alerts</a>
    <a href="/early-careers/students">Students</a>
    <a href="/careers/life-at-acme">Life at Acme</a>
    <a href="/careers/benefits">Benefits</a>
    <a href="/careers/university-recruiting">University Recruiting</a>
  </nav>
  <p>Our internship program runs every summer.</p>
  <a href="/careers/internships">Internship Program</a>
  <a href="https://acme.example/early-careers">This page</a>
</body></html>
"""

LISTING_PAGE = """
<html><body>
  <a href="/careers">Careers</a>
  <a href="/careers/job/software-engineer-4471">Software Engineer</a>
  <a href="/careers/job/data-analyst-4472">Data Analyst</a>
  <a href="/careers/job/search-engineer-4473">Search Engineer</a>
  <a href="/careers/job/product-designer-4474">Product Designer</a>
  <a href="/careers/job/backend-engineer-4475">Backend Engineer</a>
  <a href="/careers/search">Search jobs</a>
</body></html>
"""


def test_informational_page_yields_no_jobs() -> None:
    assert extract_job_links(INFORMATIONAL_PAGE, PAGE) == []


def test_listing_page_yields_only_the_individual_openings() -> None:
    links = extract_job_links(LISTING_PAGE, PAGE)

    assert [link.title for link in links] == [
        "Software Engineer",
        "Data Analyst",
        "Search Engineer",
        "Product Designer",
        "Backend Engineer",
    ]


def test_a_role_called_search_engineer_survives_while_search_jobs_does_not() -> None:
    html = """
    <a href="/careers/job/search-engineer-1">Search Engineer</a>
    <a href="/careers/job/anything">Search jobs</a>
    """

    titles = [link.title for link in extract_job_links(html, PAGE)]

    assert titles == ["Search Engineer"]


def test_slug_only_and_ats_job_urls_are_both_accepted() -> None:
    html = (
        '<a href="/careers/software-engineer">Software Engineer</a>'
        '<a href="https://jobs.ashbyhq.com/acme/uuid-1">Product Engineer</a>'
    )

    assert {link.title for link in extract_job_links(html, PAGE)} == {
        "Software Engineer",
        "Product Engineer",
    }


def test_top_level_and_offsite_links_are_rejected() -> None:
    html = (
        '<a href="/jobs">Jobs</a>'
        '<a href="https://linkedin.example/company/acme/jobs/1234">Acme on LinkedIn</a>'
        '<a href="/about/team">Our Team</a>'
    )

    assert extract_job_links(html, PAGE) == []


def test_duplicate_links_collapse_to_one_job() -> None:
    html = (
        '<a href="/careers/job/software-engineer-4471">Software Engineer</a>'
        '<a href="/careers/job/software-engineer-4471/">Software Engineer</a>'
    )

    assert len(extract_job_links(html, PAGE)) == 1


def test_dominant_cluster_drops_residual_chrome_from_a_real_listing() -> None:
    html = (
        "".join(f'<a href="/careers/job/role-{index}">Engineer {index}</a>' for index in range(6))
        + '<a href="/press/careers-announcement-2026">Careers Announcement 2026</a>'
    )

    links = extract_job_links(html, PAGE)

    assert len(links) == 6
    assert all("/careers/job/" in link.url for link in links)


def test_a_small_board_is_not_discarded_by_clustering() -> None:
    html = '<a href="/careers/job/role-a">Engineer A</a><a href="/openings/role-b">Engineer B</a>'

    assert len(extract_job_links(html, PAGE)) == 2


def test_listing_candidates_prefer_job_board_links_over_navigation() -> None:
    candidates = rank_listing_candidates(INFORMATIONAL_PAGE, PAGE, limit=3)

    assert candidates
    assert candidates[0].url == "https://acme.example/careers/search"
    assert all(candidate.url != PAGE for candidate in candidates)


def test_listing_candidates_rank_a_recognized_ats_board_first() -> None:
    html = (
        '<a href="/careers/search">Search jobs</a>'
        '<a href="https://boards.greenhouse.io/acme">Open positions</a>'
    )

    candidates = rank_listing_candidates(html, PAGE, limit=2)

    assert candidates[0].url == "https://boards.greenhouse.io/acme"


def test_listing_candidate_limit_is_respected() -> None:
    assert rank_listing_candidates(INFORMATIONAL_PAGE, PAGE, limit=0) == []
    assert len(rank_listing_candidates(INFORMATIONAL_PAGE, PAGE, limit=1)) == 1


def test_accessibility_skip_links_and_pagination_are_not_jobs() -> None:
    """Both were observed as stored `Job` rows under the previous rules."""

    html = (
        '<a href="/early-careers/page/1#page-main-content">Skip to content</a>'
        '<a href="/early-careers/page/2">2</a>'
        '<a href="/careers/page/3">Next page of jobs</a>'
        '<a href="/jobs">Search careers</a>'
        '<a href="/early-careers">Emerging Talent</a>'
    )

    assert extract_job_links(html, PAGE) == []


def test_a_real_posting_under_a_job_segment_survives() -> None:
    html = (
        '<a href="/cloud-infrastructure-platform-engineer/job/615">'
        "Cloud Infrastructure &amp; Platform Engineer</a>"
    )

    links = extract_job_links(html, "https://careers.acme.example/early-careers")

    assert [link.title for link in links] == ["Cloud Infrastructure & Platform Engineer"]


def test_program_and_culture_section_pages_are_not_jobs() -> None:
    """Real rows observed on careers.ea.com under the previous rules."""

    html = (
        '<a href="/careers/why-ea">Why EA</a>'
        '<a href="/en-us/careers/life-at-ea">Why EA</a>'
        '<a href="/careers/interns-and-university-graduates">Emerging Talent</a>'
        '<a href="/en-us/careers/interns-and-university-graduates/frequently-asked-questions">'
        "Internships &amp; Co-Ops FAQs</a>"
    )

    assert extract_job_links(html, "https://careers.ea.com/careers") == []


def test_a_graduate_posting_is_not_confused_with_a_graduates_program_index() -> None:
    html = '<a href="/careers/job/graduate-software-engineer">Graduate Software Engineer</a>'

    links = extract_job_links(html, "https://careers.ea.com/careers")

    assert [link.title for link in links] == ["Graduate Software Engineer"]


def test_category_links_titled_like_a_group_of_roles_are_not_jobs() -> None:
    """`UK Jobs` on a SuccessFactors board is a saved search, not a posting."""

    html = (
        '<a href="/go/UK-Jobs/8711800/">UK Jobs</a>'
        '<a href="/go/Engineering-Careers/8711801/">Engineering Careers</a>'
        '<a href="/job/London-Software-Engineer/8711802/">Software Engineer</a>'
    )

    links = extract_job_links(html, "https://careers.acme.example/careers")

    assert [link.title for link in links] == ["Software Engineer"]
