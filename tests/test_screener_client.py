from __future__ import annotations

import json

from backend.data.screener_client import ScreenerClient


SCREENER_HTML = """
<html>
  <body>
    <h1>Test Company Ltd</h1>
    <ul id="top-ratios">
      <li><span class="name">Market Cap</span><span class="number">12,345 Cr</span></li>
      <li><span class="name">Stock P/E</span><span class="number">18.4</span></li>
      <li><span class="name">Price to book value</span><span class="number">3.1</span></li>
      <li><span class="name">Dividend Yield</span><span class="number">1.2%</span></li>
      <li><span class="name">ROE</span><span class="number">22.0%</span></li>
      <li><span class="name">ROCE</span><span class="number">24.5%</span></li>
      <li><span class="name">Face Value</span><span class="number">10</span></li>
    </ul>
    <section>
      <h2>Quarterly Results</h2>
      <table>
        <thead><tr><th>Particulars</th><th>Mar 2026</th><th>Dec 2025</th><th>Sep 2025</th><th>Jun 2025</th></tr></thead>
        <tbody>
          <tr><td>Sales +</td><td>120</td><td>110</td><td>100</td><td>90</td></tr>
          <tr><td>Net Profit +</td><td>18</td><td>17</td><td>16</td><td>15</td></tr>
          <tr><td>EPS in Rs</td><td>4.1</td><td>3.9</td><td>3.6</td><td>3.4</td></tr>
          <tr><td>Operating Profit</td><td>26</td><td>24</td><td>22</td><td>20</td></tr>
        </tbody>
      </table>
    </section>
    <section>
      <h2>Profit & Loss</h2>
      <table>
        <thead><tr><th>Particulars</th><th>Mar 2025</th><th>Mar 2024</th></tr></thead>
        <tbody>
          <tr><td>Sales +</td><td>420</td><td>360</td></tr>
          <tr><td>Net Profit +</td><td>64</td><td>48</td></tr>
          <tr><td>Operating Profit</td><td>100</td><td>80</td></tr>
          <tr><td>OPM %</td><td>23.8</td><td>22.2</td></tr>
        </tbody>
      </table>
    </section>
    <section>
      <h2>Balance Sheet</h2>
      <table>
        <thead><tr><th>Particulars</th><th>Mar 2025</th></tr></thead>
        <tbody>
          <tr><td>Total Assets</td><td>500</td></tr>
          <tr><td>Borrowings</td><td>70</td></tr>
          <tr><td>Equity Capital</td><td>50</td></tr>
          <tr><td>Reserves</td><td>190</td></tr>
          <tr><td>Current Assets</td><td>150</td></tr>
          <tr><td>Current Liabilities</td><td>60</td></tr>
        </tbody>
      </table>
    </section>
    <section>
      <h2>Cash Flow</h2>
      <table>
        <thead><tr><th>Particulars</th><th>Mar 2025</th></tr></thead>
        <tbody>
          <tr><td>Cash from Operating Activity +</td><td>88</td></tr>
        </tbody>
      </table>
    </section>
    <section>
      <h2>Ratios</h2>
      <table>
        <thead><tr><th>Particulars</th><th>Mar 2025</th></tr></thead>
        <tbody>
          <tr><td>Debt to equity</td><td>0.30</td></tr>
          <tr><td>Current Ratio</td><td>2.5</td></tr>
          <tr><td>Interest Coverage</td><td>12.2</td></tr>
          <tr><td>Book Value</td><td>47.5</td></tr>
        </tbody>
      </table>
    </section>
    <section>
      <h2>Shareholding Pattern</h2>
      <table>
        <thead><tr><th>Category</th><th>Mar 2025</th><th>Dec 2024</th></tr></thead>
        <tbody>
          <tr><td>Promoters</td><td>54.2</td><td>51.0</td></tr>
          <tr><td>FIIs</td><td>12.5</td><td>10.1</td></tr>
          <tr><td>DIIs</td><td>9.3</td><td>8.8</td></tr>
        </tbody>
      </table>
    </section>
  </body>
</html>
"""


class _HtmlResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FallbackSession:
    def __init__(self):
        self.headers = {}
        self.calls: list[str] = []

    def mount(self, *_args, **_kwargs) -> None:
        return None

    def get(self, url, timeout=None, allow_redirects=True):  # noqa: ANN001
        self.calls.append(url)
        if url.endswith("/consolidated/"):
            return _HtmlResponse("", status_code=404)
        return _HtmlResponse(SCREENER_HTML, status_code=200)


def test_parse_company_page_extracts_required_sections():
    parsed = ScreenerClient.parse_company_page(symbol="TESTCO", html=SCREENER_HTML, source_url="https://www.screener.in/company/TESTCO/")
    flat = parsed.to_flat_dict()

    assert parsed.company_name == "Test Company Ltd"
    assert flat["market_cap"] == 123450000000.0
    assert flat["pb_ratio"] == 3.1
    assert flat["revenue_ttm"] == 420.0
    assert flat["net_profit_ttm"] == 66.0
    assert flat["latest_annual_revenue"] == 420.0
    assert flat["total_assets"] == 500.0
    assert flat["operating_cash_flow"] == 88.0
    assert flat["promoter_holding"] == 54.2
    assert round(float(flat["promoter_holding_change_pct"] or 0.0), 1) == 3.2
    assert flat["shares_outstanding"] == 50000000.0


def test_parse_company_page_handles_missing_sections():
    parsed = ScreenerClient.parse_company_page(symbol="TESTCO", html="<html><body><h1>Only Title</h1></body></html>")
    flat = parsed.to_flat_dict()

    assert parsed.company_name == "Only Title"
    assert flat["market_cap"] is None
    assert flat["revenue_ttm"] is None
    assert parsed.raw_sections["quarterly_results"]["rows"] == {}


def test_fetch_company_data_falls_back_from_consolidated_to_standalone():
    client = ScreenerClient(session=_FallbackSession())

    parsed = client.fetch_company_data("TESTCO")

    assert parsed.company_name == "Test Company Ltd"
    assert parsed.source_url.endswith("/company/TESTCO/")


def test_slug_override_file_is_used(tmp_path, monkeypatch):
    override_path = tmp_path / "overrides.json"
    override_path.write_text(json.dumps({"TESTCO": "TEST-COMPANY"}), encoding="utf-8")
    monkeypatch.setattr("backend.data.screener_client.settings.screener_symbol_override_path", override_path)

    client = ScreenerClient(session=_FallbackSession())

    assert client._slug_for_symbol("TESTCO") == "TEST-COMPANY"
