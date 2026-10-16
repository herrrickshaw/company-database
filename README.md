# company-database

A country-wise, sector-wise company universe built from **free, open registries** (no scraping of commercial
contact databases). 18.17M companies, 132 countries, 21 sectors (97.6% sector-coded).

> The data itself (1.2 GB of CSV + a 1.4 GB DuckDB) is **not in git**. It is rebuilt by `build.py` from the public
> sources below. `samples/` has 200 rows per source and `docs/` has the row counts.

## Layout produced by the build
```
company_lists.duckdb          table companies (all rows) and companies_dedup (one row per country + normalised name)
lists/<ISO2>/<sector>.csv     e.g. lists/IN/manufacturing.csv  (sector 'unclassified' when no industry code)
by_sector_country/            Parquet, partitioned by country and sector
```
Columns in the lists: `name, sources, source_id, city, website, sector_code, size, status, listed_ticker`.

## Sector scheme
The 21 UK SIC 2007 section names (Manufacturing, Wholesale & retail, Real estate, ...). NACE Rev2 / NAF Rev2 / EMTAK 2008 /
SN2007 share SIC 2007's two-digit divisions, so they map directly. Other schemes are mapped approximately:
Damodaran industry groups (lookup table `DAMO`), India NIC (see below), NAF Rev1/1993 via the NIC-2004 table.

India: the CIN company number embeds a 5-digit industry code. Companies incorporated **before 2023** use NIC-2004
(`NIC` table, approximate); **2023 onward** use NIC-2008, which matches SIC 2007 divisions. The 2023 break was found
empirically (share of NIC-2008-only divisions jumps from ~3% to ~27%).

## Sources (see SOURCES.md for licences)
| Source | Rows | Notes |
|---|---|---|
| France SIRENE (INSEE) | 11,263,902 | active legal units with a name; ~3M are small property companies (SCI) |
| UK Companies House | 5,166,057 | active companies; SIC codes are self-reported |
| Norway Brønnøysund | 1,165,528 | excludes bankrupt / winding-up |
| Estonia e-Business Register | 351,198 | registered companies, EMTAK main activity |
| India (MCA CIN via GLEIF) | 170,010 | only companies holding an LEI, not the full MCA register |
| Damodaran listed companies | 43,125 | 132 countries, exchange-listed only |
| India IEM-linked CINs | 8,513 | companies in DPIIT IEM filings, not already above |
| Defence-industry registry | 2,477 | 16 countries; 2,266 have no industry code |

GLEIF (3.46M entities) is loaded for identity but has no sector, so it is excluded from the lists.

## Dedupe
One row per `(country, normalised name)`. Registry rows win. A listed or defence row merges into a registry row only
when exactly one registry row has that name; otherwise it is kept separately. Merged rows gain `listed_ticker` and
`sources`.

## Known limits
- Registry sector can mislead for holding companies (e.g. a software firm filed under Finance & insurance).
- Name-based matching can miss merges and, rarely, wrong-merge.
- **Not covered:** Finland (PRH API refused connections), Brazil, Denmark, Singapore, US SEC. Outside the registry
  countries only listed and defence companies are present.
- PDL's free company dataset (global, needs a request form) is supported by `build.py load pdl <file>` but untested.

## Rebuild
```
pip install -r requirements.txt
python3 build.py fetch gleif && python3 build.py fetch ch
python3 build.py load gleif raw/gleif_lei2.csv.zip      # also: load in_mca raw/gleif_lei2.csv.zip
python3 build.py load ch raw/BasicCompanyDataAsOneFile-*.zip
python3 build.py load damodaran <damodaran_companies.parquet>
python3 build.py load fr_sirene <StockUniteLegale parquet>
python3 build.py load no_brreg <enheter csv.gz>
python3 build.py load ee_ariregister <13_11_yldandmed.parquet>
python3 build.py dedupe && python3 build.py lists
```
`fetch_fi.py` pulls Finland politely (resumable); it was blocked by the server during the first run.
