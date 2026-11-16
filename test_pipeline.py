import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from pdf_reader import extract_bibliography, parse_reference, extract_references_text
from literature_checker import assess, run
from pubmed_search import PubMedClient

FIXTURE = Path(__file__).parent / 'tests' / 'fixtures'
CANDIDATE = {'pmid': '12225610', 'title': 'Statistics review 4: sample size calculations.',
             'authors': ['Elise Whitley', 'Jonathan Ball'], 'year': 2002, 'doi': '10.1186/cc1521'}


class PipelineTests(unittest.TestCase):
    def test_real_pdf_fixture_and_duplicate_section(self):
        result = extract_bibliography(FIXTURE / 'numbered.pdf')
        self.assertEqual(len(result['references']), 2)
        self.assertEqual(result['references'][0]['author_surnames'], ['Whitley', 'Ball'])
        self.assertEqual(result['references'][0]['doi'], '10.1186/cc1521')

    def test_blank_pdf_is_not_verification_success(self):
        self.assertEqual(run(FIXTURE / 'blank.pdf', extract_only=True)['status'], 'no_text_extracted')

    def test_no_heading(self):
        self.assertIsNone(extract_references_text(['Article text without bibliography']))

    def test_correct_and_corrupt_metadata(self):
        ref = extract_bibliography(FIXTURE / 'numbered.pdf')['references'][0]
        search = {'status': 'candidates_found', 'candidates': [copy.deepcopy(CANDIDATE)]}
        self.assertEqual(assess(ref, search)[0], 'metadata_match')
        ref['doi'] = '10.1186/cc1473'
        self.assertEqual(assess(ref, search)[0], 'metadata_review')
        ref['doi'] = CANDIDATE['doi']
        ref['author_surnames'] = ['Petrov']
        self.assertEqual(assess(ref, search)[0], 'metadata_review')

    def test_doi_lookup_does_not_accept_wrong_title(self):
        ref = extract_bibliography(FIXTURE / 'numbered.pdf')['references'][0]
        candidate = dict(CANDIDATE, title='Statistics review 2: samples and populations.')
        self.assertEqual(assess(ref, {'status': 'candidates_found', 'candidates': [candidate]})[0], 'metadata_review')

    def test_network_failure_differs_from_no_hits(self):
        self.assertEqual(assess({}, {'status': 'request_failed'})[0], 'request_failed')
        self.assertEqual(assess({}, {'status': 'no_candidates_for_query'})[0], 'not_found_in_pubmed')

    def test_pdf_to_search_integration(self):
        with patch('literature_checker.PubMedClient.search', return_value={'status': 'candidates_found', 'candidates': [copy.deepcopy(CANDIDATE)]}) as search:
            report = run(FIXTURE / 'numbered.pdf', 'test@example.invalid', limit=1)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(report['references'][0]['status'], 'metadata_match')
        self.assertEqual(report['references'][1]['status'], 'not_checked')
        self.assertTrue(all(r['needs_human_review'] for r in report['references']))

    def test_request_evidence_is_isolated_per_search(self):
        client = PubMedClient('test@example.invalid')
        def fake(endpoint, params):
            client.evidence.append({'endpoint': endpoint})
            return b'{"esearchresult":{"count":"0","idlist":[]}}'
        with patch.object(client, 'request', side_effect=fake):
            first = client.search('first')
            second = client.search('second')
        self.assertIsNot(first['requests'], second['requests'])
        self.assertEqual(len(first['requests']), 1)

    def test_hyphenated_initials(self):
        ref = parse_reference('Chow Sh.-Ch., Shao J., Wang H. Sample size calculations. 2003.', 1, [1])
        self.assertEqual(ref['title'], 'Sample size calculations')
        self.assertEqual(ref['author_surnames'], ['Chow', 'Shao', 'Wang'])


if __name__ == '__main__':
    unittest.main()
