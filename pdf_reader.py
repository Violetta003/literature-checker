"""Local PDF text and numbered bibliography extraction. No OCR or external AI."""
import re
import pdfplumber

HEADING = re.compile(r"^\s*(references|bibliography|библиография|список\s+литературы|список\s+использованных\s+источников|литература)\s*[:.]?\s*$", re.I)
STOP = re.compile(r"^\s*(acknowledg\w*|appendix\b.*|приложение\b.*|дата поступления.*|received\b.*)\s*$", re.I)
NUMBER = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s*(?=\S)")
DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>\"]+", re.I)
AUTHOR = re.compile(r"\s*([A-Za-zА-Яа-яЁёÀ-ž'’\-]+)\s+(?:[A-ZА-ЯЁ](?:[a-z]{1,2})?\s*\.(?:[\s\-]*[A-ZА-ЯЁ](?:[a-z]{1,2})?\s*\.)*)\s*[,;]?\s*")


def parse_reference(raw, number, pages):
    text = re.sub(r"\s+", " ", raw).strip()
    match = DOI.search(text)
    doi = match.group().rstrip('.,;)') if match else None
    years = re.findall(r"\b(?:18|19|20)\d{2}\b", text)
    position = 0
    authors = []
    while (match := AUTHOR.match(text, position)) is not None:
        authors.append(match.group(1))
        position = match.end()
    # Only infer a title after a recognized author prefix; keep the original evidence.
    remainder = text[position:] if authors else ''
    title = re.split(r"\.\s+|\s+//\s+", remainder, maxsplit=1)[0].strip(' .')
    if len(title) < 8 or not re.search(r"[A-Za-zА-Яа-я]", title):
        title = None
    return {"number": number, "raw": text, "pages": sorted(set(pages)),
            "title": title, "author_surnames": authors,
            "year": int(years[0]) if years else None, "doi": doi,
            "parsing_method": "rules_v1", "metadata_requires_review": True}


def extract_bibliography(path):
    page_texts = extract_pdf_pages(path)
    pages = []
    lines = []
    for number, text in enumerate(page_texts, 1):
        pages.append({"page": number, "text_characters": len(text.strip()),
                      "has_text": bool(text.strip())})
        lines.extend((number, line) for line in text.splitlines())
    report = {"pages": pages, "status": "bibliography_not_found", "references": [],
              "warnings": [], "section_text": ""}
    if any(not p['has_text'] for p in pages):
        report['warnings'].append('Some pages have no extracted text; OCR may be needed')
    if not any(p['has_text'] for p in pages):
        report['status'] = 'no_text_extracted'
        return report
    starts = [i for i, (_, line) in enumerate(lines) if HEADING.fullmatch(line)]
    if not starts:
        return report
    # Prefer the first bibliography and stop at the translated duplicate section.
    start = starts[0]
    section = []
    for page, line in lines[start + 1:]:
        if HEADING.fullmatch(line) or STOP.fullmatch(line):
            break
        # Standalone page numbers and URL-only footers are not reference continuations.
        if re.fullmatch(r"\s*\d+\s*", line) or re.fullmatch(r"\s*https?://\S+\s+\d+\s*", line):
            continue
        section.append((page, line))
    report['section_text'] = '\n'.join(line for _, line in section)
    current = []
    source_pages = []
    number = None
    for page, line in section:
        match = NUMBER.match(line)
        if match:
            if current:
                report['references'].append(parse_reference(' '.join(current), number, source_pages))
            number = int(match.group(1) or match.group(2))
            current = [line[match.end():]]
            source_pages = [page]
        elif current:
            # Join words hyphenated across physical lines.
            if current[-1].endswith('-'):
                current[-1] = current[-1][:-1] + line.strip()
            else:
                current.append(line)
            source_pages.append(page)
    if current:
        report['references'].append(parse_reference(' '.join(current), number, source_pages))
    report['status'] = 'references_extracted' if report['references'] else 'reference_segmentation_failed'
    numbers = [r['number'] for r in report['references']]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        report['warnings'].append('Nonsequential reference numbering; review segmentation')
    report['warnings'].append('Rule-based title and author extraction requires review; unsupported styles may be missed')
    return report


# Keep the original module entry points for interactive use and learning.
def extract_pdf_pages(pdf_path):
    """Return page texts. File/access errors propagate to the CLI report."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def find_non_empty_pages(pages):
    return [number for number, page in enumerate(pages, 1) if page.strip()]


def references_check(page_text):
    return any(HEADING.fullmatch(line) for line in page_text.splitlines())


def find_references_page(pages):
    return next((number for number, page in enumerate(pages, 1) if references_check(page)), None)


def extract_references_text(pages):
    start_page = find_references_page(pages)
    if start_page is None:
        return None
    result = []
    started = False
    for line in "\n".join(pages[start_page - 1:]).splitlines():
        if HEADING.fullmatch(line):
            if started:
                break
            started = True
        elif started:
            if STOP.fullmatch(line):
                break
            result.append(line)
    return "\n".join(result)


def clear_references_text(references_text):
    return [line for line in references_text.splitlines()
            if not re.fullmatch(r"\s*https?://\S+\s+\d+\s*", line)
            and not STOP.fullmatch(line)]
