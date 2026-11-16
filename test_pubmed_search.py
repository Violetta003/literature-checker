import json
import unittest
from pubmed_search import PubMedClient, PubMedError, parse_articles

XML = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
<ArticleTitle>A <i>nested</i> title</ArticleTitle><Journal><Title>Test Journal</Title>
<JournalIssue><PubDate><MedlineDate>2020 Winter</MedlineDate></PubDate></JournalIssue></Journal>
<AuthorList><Author><CollectiveName>Study Group</CollectiveName></Author>
<Author><LastName>Smith</LastName><Initials>AB</Initials></Author></AuthorList></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="doi">10.0/test</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>'''

class Client(PubMedClient):
    def __init__(self, responses):
        super().__init__('test@example.invalid')
        self.responses = iter(responses)
    def request(self, endpoint, params):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

class Tests(unittest.TestCase):
    def test_nested_title_collective_author_and_uncertain_date(self):
        article = parse_articles(XML)[0]
        self.assertEqual(article['title'], 'A nested title')
        self.assertEqual(article['authors'], ['Study Group', 'AB Smith'])
        self.assertIsNone(article['year'])
        self.assertEqual(article['medline_date'], '2020 Winter')
    def test_no_hits_is_not_network_failure(self):
        empty = Client([b'{"esearchresult":{"count":"0","idlist":[]}}']).search('Title')
        failure = Client([PubMedError('Network failed')]).search('Title')
        self.assertEqual(empty['status'], 'no_candidates_for_query')
        self.assertEqual(failure['status'], 'request_failed')
    def test_complete_result(self):
        client = Client([b'{"esearchresult":{"count":"1","idlist":["42"]}}', XML])
        self.assertEqual(client.search('Title')['status'], 'candidates_found')
    def test_missing_record_is_not_success(self):
        client = Client([b'{"esearchresult":{"count":"1","idlist":["43"]}}', XML])
        self.assertEqual(client.search('Title')['status'], 'request_failed')
    def test_invalid_json_is_failure(self):
        self.assertEqual(Client([b'<html>blocked</html>']).search('Title')['status'], 'request_failed')
    def test_search_error_is_not_absence(self):
        client = Client([b'{"esearchresult":{"count":"0","idlist":[],"errorlist":{"x":"bad"}}}'])
        self.assertEqual(client.search('Title')['status'], 'request_failed')

if __name__ == '__main__':
    unittest.main()
