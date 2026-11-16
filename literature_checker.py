"""PDF -> bibliography -> PubMed candidates -> auditable JSON report."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from pubmed_search import PubMedClient, VERSION
from pdf_reader import extract_bibliography


def normalize(value):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold()))


def assess(reference, search):
    if search['status'] == 'request_failed':
        return 'request_failed', 'PubMed request or response parsing failed'
    if search['status'] == 'no_candidates_for_query':
        return 'not_found_in_pubmed', 'No match for this query; publication may exist outside PubMed'
    for candidate in search['candidates']:
        title_matches = bool(reference['title']) and normalize(reference['title']) == normalize(candidate['title'])
        doi_matches = bool(reference['doi']) and reference['doi'].casefold() == (candidate['doi'] or '').casefold()
        year_matches = reference['year'] is not None and reference['year'] == candidate['year']
        author_tokens = set(normalize(' '.join(candidate['authors'])).split())
        authors_match = bool(reference['author_surnames']) and all(normalize(a) in author_tokens for a in reference['author_surnames'])
        candidate['comparison'] = {'title': title_matches, 'doi': doi_matches if reference['doi'] else None,
                                   'year': year_matches, 'cited_surnames': authors_match}
    matches = [c for c in search['candidates'] if c['comparison']['title']
               and c['comparison']['year'] and c['comparison']['cited_surnames']
               and (not reference['doi'] or c['comparison']['doi'])]
    if len(matches) == 1:
        return 'metadata_match', 'Title, year and extracted surnames match one PubMed record; extraction still requires review'
    return 'metadata_review', 'Candidate records need review for mismatches, ambiguity or missing fields'


def run(pdf, email=None, extract_only=False, limit=None, timeout=20):
    extraction = extract_bibliography(pdf)
    report = {'software_version': VERSION, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'input_file': Path(pdf).name, 'sha256': hashlib.sha256(Path(pdf).read_bytes()).hexdigest(),
              'mode': 'extract_only' if extract_only else 'pubmed', 'extraction': extraction,
              'references': [], 'status': extraction['status'],
              'sources': {'PubMed': 'enabled' if not extract_only else 'not_run',
                          'eLIBRARY': 'not_implemented', 'CyberLeninka': 'not_implemented',
                          'Google Scholar': 'not_implemented'}}
    client = PubMedClient(email, os.getenv('NCBI_API_KEY'), timeout) if not extract_only else None
    for index, reference in enumerate(extraction['references']):
        item = dict(reference, needs_human_review=True)
        if extract_only or (limit is not None and index >= limit):
            item.update(status='not_checked', reason='Extraction only or per-run limit reached')
        elif not reference['title'] and not reference['doi']:
            item.update(status='parsing_review', reason='No reliable title or DOI extracted')
        else:
            search = client.search(reference['title'] or '', doi=reference['doi'])
            item['search'] = search
            item['status'], item['reason'] = assess(reference, search)
        report['references'].append(item)
    if extraction['references']:
        report['status'] = 'completed_with_review'
    report['summary'] = {status: sum(r['status'] == status for r in report['references'])
                         for status in sorted({r['status'] for r in report['references']})}
    return report


def main():
    parser = argparse.ArgumentParser(description='Extract numbered PDF references and retrieve PubMed records')
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--email', default=os.getenv('NCBI_EMAIL'))
    parser.add_argument('--extract-only', action='store_true')
    parser.add_argument('--limit', type=int, help='Check first N references; retain all others as not_checked')
    parser.add_argument('--timeout', type=int, default=20)
    parser.add_argument('--output', default='results/pdf_report.json')
    args = parser.parse_args()
    if not args.extract_only and (not args.email or '@' not in args.email):
        parser.error('Set NCBI_EMAIL or --email')
    if (args.limit is not None and args.limit < 1) or args.timeout < 1:
        parser.error('limit and timeout must be positive')
    try:
        report = run(args.pdf, args.email, args.extract_only, args.limit, args.timeout)
    except Exception as exc:
        # A document-level failure must still produce a report rather than zero verified references.
        report = {'software_version': VERSION, 'status': 'document_failed',
                  'error_type': type(exc).__name__, 'references': []}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(output)
    print(f"Status: {report['status']}")
    print(f"References: {len(report['references'])}")
    print(f'Report: {output.resolve()}')
    return 1 if report['status'] != 'completed_with_review' or any(r['status'] == 'request_failed' for r in report['references']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
