import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
VERSION = "0.2.0"


class PubMedError(Exception):
    pass


def element_text(node):
    return "" if node is None else "".join(node.itertext()).strip()


def parse_articles(payload):
    root = ET.fromstring(payload)
    candidates = []
    for record in root.findall("PubmedArticle"):
        article = record.find("MedlineCitation/Article")
        if article is None:
            continue
        pmid = element_text(record.find("MedlineCitation/PMID"))
        if not pmid:
            continue
        authors = []
        for author in article.findall("AuthorList/Author"):
            collective = element_text(author.find("CollectiveName"))
            name = " ".join(filter(None, [element_text(author.find("ForeName")) or
                                       element_text(author.find("Initials")),
                                       element_text(author.find("LastName"))]))
            if collective or name:
                authors.append(collective or name)
        identifiers = {item.get("IdType"): element_text(item)
                       for item in record.findall("PubmedData/ArticleIdList/ArticleId")}
        pub_date = article.find("Journal/JournalIssue/PubDate")
        year = element_text(pub_date.find("Year")) if pub_date is not None else ""
        medline_date = element_text(pub_date.find("MedlineDate")) if pub_date is not None else ""
        candidates.append({
            "pmid": pmid,
            "title": element_text(article.find("ArticleTitle")),
            "authors": authors,
            "journal": element_text(article.find("Journal/Title")),
            "year": int(year) if year.isdigit() else None,
            "medline_date": medline_date or None,
            "volume": element_text(article.find("Journal/JournalIssue/Volume")),
            "issue": element_text(article.find("Journal/JournalIssue/Issue")),
            "pages": element_text(article.find("Pagination/MedlinePgn")),
            "doi": identifiers.get("doi"),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    # записи о книгах в PubMed имеют другую структуру XML
    if root.findall("PubmedBookArticle"):
        raise PubMedError("Book records returned: this prototype supports PubmedArticle only")
    if root.find("ERROR") is not None:
        raise PubMedError(element_text(root.find("ERROR")))
    return candidates


class PubMedClient:
    def __init__(self, email, api_key=None, timeout=20):
        self.email = email
        self.api_key = api_key
        self.timeout = timeout
        self.last_request = 0.0
        self.evidence = []

    def request(self, endpoint, params):
        public_params = dict(params, tool="literature_checker", email=self.email)
        sent_params = dict(public_params)
        if self.api_key:
            sent_params["api_key"] = self.api_key
        # интервалы между запросами и ограничение интервалов по кол-ву
        for attempt in range(3):
            time.sleep(max(0, 0.5 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            event = {"endpoint": endpoint, "params": params,
                     "attempt": attempt + 1,
                     "timestamp_utc": datetime.now(timezone.utc).isoformat()}
            self.evidence.append(event)
            try:
                req = Request(BASE + endpoint + "?" + urlencode(sent_params),
                              headers={"User-Agent": f"literature_checker/{VERSION}"})
                with urlopen(req, timeout=self.timeout) as response:
                    payload = response.read()
                    event["http_status"] = response.status
                event["response_text"] = payload.decode("utf-8", errors="replace")
                return payload
            except HTTPError as exc:
                event["http_status"] = exc.code
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise PubMedError(f"HTTP {exc.code} from PubMed") from None
                # в логах и исключениях нет URL-адресов и ключей
                time.sleep(2 ** attempt)
            except (URLError, TimeoutError, OSError):
                event["error"] = "network_error"
                if attempt == 2:
                    raise PubMedError("PubMed network request failed after 3 attempts") from None
                time.sleep(2 ** attempt)

    def search(self, title, max_results=5, doi=None):
        # Prefer an explicit identifier; return candidates for independent comparison.
        if doi:
            query = '"' + doi.replace('"', ' ').strip() + '"[AID]'
        else:
            query = '"' + title.replace('"', ' ').strip() + '"[Title:~0]' 
        self.evidence = []
        result = {"software_version": VERSION,
                  "started_at_utc": datetime.now(timezone.utc).isoformat(),
                  "input": {"title": title, "doi": doi}, "source": "PubMed",
                  "query": query, "max_results": max_results,
                  "status": "request_failed", "candidates": [], "requests": self.evidence}
        try:
            search = json.loads(self.request("esearch.fcgi", {
                "db": "pubmed", "term": query, "retmode": "json", "retmax": max_results}))
            if "error" in search:
                raise PubMedError("PubMed returned an API error")
            data = search["esearchresult"]
            if data.get("errorlist"):
                result["search_error_details"] = data["errorlist"]
                raise PubMedError("PubMed could not execute the search as specified")
            if data.get("warninglist"):
                result["search_warnings"] = data["warninglist"]
            count = int(data["count"])
            ids = data["idlist"]
            result["total_search_hits"] = count
            result["returned_pmids"] = ids
            if count == 0:
                result["status"] = "no_candidates_for_query"
                return result
            if not ids:
                raise PubMedError("Search returned hits but no identifiers")
            candidates = parse_articles(self.request("efetch.fcgi", {
                "db": "pubmed", "id": ",".join(ids), "retmode": "xml"}))
            result["candidates"] = candidates
            if {c["pmid"] for c in candidates} != set(ids):
                raise PubMedError("Incomplete metadata response: not all requested PMIDs were parsed")
            result["status"] = "candidates_found"
        except (PubMedError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError) as exc:
            result["error"] = str(exc) if isinstance(exc, PubMedError) else "Invalid PubMed response structure"
        return result


def main():
    parser = argparse.ArgumentParser(description="Retrieve PubMed candidates; does not verify existence.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--email", default=os.getenv("NCBI_EMAIL"))
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--output", default="pubmed_result.json")
    args = parser.parse_args()
    if not args.email or "@" not in args.email:
        parser.error("Provide your contact email using --email or NCBI_EMAIL")
    if not args.title.strip() or not 1 <= args.max_results <= 20:
        parser.error("Title must be nonempty; max-results must be 1..20")
    client = PubMedClient(args.email, os.getenv("NCBI_API_KEY"))
    result = client.search(args.title.strip(), args.max_results)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Status: {result['status']}")
    for candidate in result["candidates"]:
        print(f"PMID {candidate['pmid']} | {candidate['year']} | {candidate['title']}")
    if result.get("error"):
        print(result["error"], file=sys.stderr)
    print(f"Report: {output.resolve()}")
    return 1 if result["status"] == "request_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
