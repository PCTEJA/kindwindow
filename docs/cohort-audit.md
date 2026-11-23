# KindWindow Step 03 — Acquisition / Cohort audit

- **Audit run:** 2026-11-23 01:19–01:32 UTC (script elapsed 739.4 s: archive hashing 18.6 s + member streaming 720.8 s). No git commits, pushes, PRs, or GitHub writes. Local work only.
- **Rule frozen before inspection:** conservative 90-day rule — ≥90 distinct local dates with interpretable records **AND** span ≥90 inclusive days. Candidate order: casefolded lexical sort of source home IDs (`aruba` precedes `hh101`). First 20 qualifiers form the cohort; further qualifiers are preserved distinctly as `eligible_beyond_cap`.
- **Interpretable record (frozen definition):** a physical line matching one of the two observed serializations — `YYYY-MM-DD,HH:MM:SS[.frac],Sensor,Message` (comma) or `YYYY-MM-DD HH:MM:SS[.frac] Sensor Message` (space) — with a valid calendar date, valid clock time (HH<24, MM<60, SS<60), non-empty sensor token, non-empty message. No device type was inferred from sensor names or room text. `sensor_types` = count of distinct sensor-identifier tokens only.
- **Method:** Python `ZipFile.open` bounded streaming (8 MiB chunks); original physical line number preserved (counter incremented per line before filtering). Raw logs were never extracted to disk and the archive was never copied into git. No test outcomes were viewed; no models were fit.

## 1. Archive verification (recomputed this run)

| field | value |
|---|---|
| archive | the local CASAS v1 `data.zip` (private, not in the repo) |
| archive bytes | 2,723,059,864 |
| MD5 (recomputed) | `973a4ec0b67269566227ce8a5a3a9e21` — matches published MD5 in `source_record.json` |
| SHA-256 (recomputed) | `fa6d2a9d74e2529304ff9632929658433580b5a44559ac9b3c6e2de9d9b7c83c` |
| source | Diane Cook, CASAS Smart Home dataset v1, DOI 10.5281/zenodo.15708568, published 2025-06-20; NLM catalog rights CC BY 4.0 |
| ZIP namelist entries | 190 = 189 file members + 1 directory entry (`data/`) |
| inventory members | 189 (the local archive inventory (189 members)), all present in namelist, zero missing |
| total uncompressed bytes (inventory) | 14,696,510,326 |
| per-member `ZipInfo.file_size` vs inventory `bytes` | match for all 89 streamed members; no mismatches |

## 2. Member-count reconciliation (189 archive members)

| bucket | count |
|---|---|
| total archive file members | 189 |
| in `resident_evidence.csv` (93 of 94 evidence rows) | 93 |
| NOT in `resident_evidence.csv` | 96 |
| evidence rows total | 94 |
| evidence rows present in archive | 93 |
| evidence rows absent from archive | 1 (`mv101`) |
| candidate single-resident homes (catalog_residents=1, demographic_evidence=supported, present in archive) | 89 |
| candidates audited by streaming | 89 (100% — full candidate set, no subset sampling) |
| candidates qualifying (≥90 dates AND ≥90-day span) | 54 |
| candidates failing the duration rule | 35 |

Reconciliation check: 93 + 96 = 189 archive members. 93 + 1 (`mv101`) = 94 evidence rows. 89 candidates + 4 ambiguous mn rows = 93 evidence rows present in archive. Cohort file rows: 189 archive members + 1 absent evidence row (`mv101`) = 190.

### Category counts in `cohort.csv`

| inclusion_category | n |
|---|---|
| selected (first 20 qualifiers, casefolded order) | 20 |
| eligible_beyond_cap (qualifiers ranked 21–54) | 34 |
| excluded_duration (candidates failing the 90-day rule) | 35 |
| excluded_ambiguous (mn/mv group-description conflict) | 4 |
| excluded_no_evidence (archive members absent from evidence CSV) | 96 |
| excluded_absent (evidence row with no archive member) | 1 |
| **total rows** | **190** |

## 3. Selected cohort (n = 20)

Pseudonyms in selection order (casefolded lexical): aruba, hh101, hh102, hh103, hh104, hh105, hh106, hh108, hh109, hh111, hh112, hh113, hh114, hh115, hh116, hh117, hh118, hh119, hh120, hh122.

| pseudonym | first_date | last_date | span_days | interpretable_dates | raw_rows | eligible_rows | sensor_types | bytes |
|---|---|---|---|---|---|---|---|---|
| aruba | 2010-11-04 | 2011-06-11 | 220 | 220 | 1602821 | 1602821 | 10 | 64366717 |
| hh101 | 2012-07-18 | 2013-07-25 | 373 | 371 | 1286244 | 1286244 | 6 | 51267904 |
| hh102 | 2011-06-15 | 2014-04-16 | 1037 | 1000 | 4840159 | 4840159 | 7 | 191508197 |
| hh103 | 2011-06-15 | 2017-05-10 | 2157 | 2071 | 5425649 | 5425649 | 7 | 214673501 |
| hh104 | 2011-06-15 | 2013-09-04 | 813 | 812 | 4941414 | 4941414 | 7 | 193281352 |
| hh105 | 2011-06-15 | 2017-03-22 | 2108 | 1859 | 6430307 | 6430307 | 6 | 254553158 |
| hh106 | 2011-06-15 | 2015-01-05 | 1301 | 1124 | 4144444 | 4144444 | 8 | 164689619 |
| hh108 | 2011-06-15 | 2017-05-11 | 2158 | 1841 | 6930755 | 6930755 | 7 | 275091167 |
| hh109 | 2011-06-15 | 2016-01-05 | 1666 | 1649 | 10944661 | 10944661 | 8 | 430608700 |
| hh111 | 2011-06-15 | 2017-05-11 | 2158 | 2135 | 6980777 | 6980777 | 6 | 276103293 |
| hh112 | 2011-06-15 | 2011-10-31 | 139 | 137 | 593643 | 593643 | 8 | 23286280 |
| hh113 | 2011-06-15 | 2012-11-05 | 510 | 496 | 2282285 | 2282285 | 7 | 89441280 |
| hh114 | 2011-06-15 | 2017-05-06 | 2153 | 1872 | 7686879 | 7686879 | 5 | 302339814 |
| hh115 | 2011-06-15 | 2012-05-25 | 346 | 339 | 1659957 | 1659957 | 6 | 65040099 |
| hh116 | 2011-06-15 | 2011-11-18 | 157 | 148 | 412796 | 412796 | 8 | 16222658 |
| hh117 | 2011-06-15 | 2012-05-25 | 346 | 293 | 777106 | 777106 | 5 | 31115671 |
| hh118 | 2011-06-15 | 2014-08-29 | 1172 | 1039 | 6064264 | 6064264 | 7 | 242458777 |
| hh119 | 2012-01-28 | 2017-05-10 | 1930 | 1531 | 3160220 | 3160220 | 7 | 124917026 |
| hh120 | 2012-01-28 | 2014-01-12 | 716 | 662 | 1839899 | 1839899 | 7 | 72403584 |
| hh122 | 2013-03-01 | 2013-09-29 | 213 | 213 | 876303 | 876303 | 8 | 34149068 |

Selected-cohort totals: 78,880,583 raw rows, 78,880,583 eligible rows, 3,117,517,865 uncompressed bytes. Date coverage overall 2010-11-04 (aruba) to 2017-05-11 (hh108/hh111 last date); per-home spans 139–2,158 days. Split counts are empty (not yet computed).

## 4. Per-candidate audit table (all 89 candidates, casefolded order)

| pseudonym | first_date | last_date | span_days | interpretable_dates | missing_days_in_span | n_gap_intervals | max_gap_missing_days | raw_rows | eligible_rows | malformed_rows | sensor_types | bytes | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| aruba | 2010-11-04 | 2011-06-11 | 220 | 220 | 0 | 0 | 0 | 1602821 | 1602821 | 0 | 10 | 64366717 | QUALIFIES |
| hh101 | 2012-07-18 | 2013-07-25 | 373 | 371 | 2 | 2 | 1 | 1286244 | 1286244 | 0 | 6 | 51267904 | QUALIFIES |
| hh102 | 2011-06-15 | 2014-04-16 | 1037 | 1000 | 37 | 7 | 27 | 4840159 | 4840159 | 0 | 7 | 191508197 | QUALIFIES |
| hh103 | 2011-06-15 | 2017-05-10 | 2157 | 2071 | 86 | 3 | 54 | 5425649 | 5425649 | 0 | 7 | 214673501 | QUALIFIES |
| hh104 | 2011-06-15 | 2013-09-04 | 813 | 812 | 1 | 1 | 1 | 4941414 | 4941414 | 0 | 7 | 193281352 | QUALIFIES |
| hh105 | 2011-06-15 | 2017-03-22 | 2108 | 1859 | 249 | 14 | 94 | 6430307 | 6430307 | 0 | 6 | 254553158 | QUALIFIES |
| hh106 | 2011-06-15 | 2015-01-05 | 1301 | 1124 | 177 | 13 | 55 | 4144444 | 4144444 | 0 | 8 | 164689619 | QUALIFIES |
| hh108 | 2011-06-15 | 2017-05-11 | 2158 | 1841 | 317 | 12 | 124 | 6930755 | 6930755 | 0 | 7 | 275091167 | QUALIFIES |
| hh109 | 2011-06-15 | 2016-01-05 | 1666 | 1649 | 17 | 6 | 5 | 10944661 | 10944661 | 0 | 8 | 430608700 | QUALIFIES |
| hh110 | 2011-06-15 | 2011-07-19 | 35 | 31 | 4 | 1 | 4 | 112544 | 112544 | 0 | 7 | 4439881 | fails duration |
| hh111 | 2011-06-15 | 2017-05-11 | 2158 | 2135 | 23 | 5 | 16 | 6980777 | 6980777 | 0 | 6 | 276103293 | QUALIFIES |
| hh112 | 2011-06-15 | 2011-10-31 | 139 | 137 | 2 | 1 | 2 | 593643 | 593643 | 0 | 8 | 23286280 | QUALIFIES |
| hh113 | 2011-06-15 | 2012-11-05 | 510 | 496 | 14 | 1 | 14 | 2282285 | 2282285 | 0 | 7 | 89441280 | QUALIFIES |
| hh114 | 2011-06-15 | 2017-05-06 | 2153 | 1872 | 281 | 9 | 156 | 7686879 | 7686879 | 0 | 5 | 302339814 | QUALIFIES |
| hh115 | 2011-06-15 | 2012-05-25 | 346 | 339 | 7 | 3 | 5 | 1659957 | 1659957 | 0 | 6 | 65040099 | QUALIFIES |
| hh116 | 2011-06-15 | 2011-11-18 | 157 | 148 | 9 | 8 | 2 | 412796 | 412796 | 0 | 8 | 16222658 | QUALIFIES |
| hh117 | 2011-06-15 | 2012-05-25 | 346 | 293 | 53 | 5 | 44 | 777106 | 777106 | 0 | 5 | 31115671 | QUALIFIES |
| hh118 | 2011-06-15 | 2014-08-29 | 1172 | 1039 | 133 | 4 | 88 | 6064264 | 6064264 | 0 | 7 | 242458777 | QUALIFIES |
| hh119 | 2012-01-28 | 2017-05-10 | 1930 | 1531 | 399 | 11 | 365 | 3160220 | 3160220 | 0 | 7 | 124917026 | QUALIFIES |
| hh120 | 2012-01-28 | 2014-01-12 | 716 | 662 | 54 | 7 | 23 | 1839899 | 1839899 | 0 | 7 | 72403584 | QUALIFIES |
| hh122 | 2013-03-01 | 2013-09-29 | 213 | 213 | 0 | 0 | 0 | 876303 | 876303 | 0 | 8 | 34149068 | QUALIFIES |
| hh123 | 2013-03-02 | 2014-10-09 | 587 | 587 | 0 | 0 | 0 | 1695797 | 1695797 | 0 | 6 | 66086499 | QUALIFIES |
| hh124 | 2013-03-01 | 2013-07-25 | 147 | 144 | 3 | 3 | 1 | 20921 | 20921 | 0 | 8 | 829640 | QUALIFIES |
| hh125 | 2013-03-01 | 2017-05-11 | 1533 | 1524 | 9 | 9 | 1 | 4943088 | 4943088 | 0 | 6 | 194185026 | QUALIFIES |
| hh126 | 2013-08-01 | 2017-05-10 | 1379 | 1228 | 151 | 1 | 151 | 3317283 | 3317283 | 0 | 6 | 131986934 | QUALIFIES |
| hh127 | 2013-07-24 | 2017-02-01 | 1289 | 1206 | 83 | 25 | 19 | 2357756 | 2357756 | 0 | 8 | 91191214 | QUALIFIES |
| hh128 | 2013-08-20 | 2017-05-09 | 1359 | 1211 | 148 | 3 | 63 | 8941966 | 8941966 | 0 | 8 | 352240022 | QUALIFIES |
| hh129 | 2013-10-22 | 2016-12-09 | 1145 | 664 | 481 | 5 | 252 | 3445246 | 3445246 | 0 | 5 | 137055680 | QUALIFIES |
| hh130 | 2014-04-17 | 2014-12-23 | 251 | 172 | 79 | 10 | 58 | 750632 | 750632 | 0 | 5 | 29953604 | QUALIFIES |
| ihs07 | 2015-10-04 | 2015-12-13 | 71 | 71 | 0 | 0 | 0 | 451520 | 451519 | 1 | 32 | 20210005 | fails duration |
| ihs107 | 2014-07-17 | 2018-10-10 | 1547 | 1462 | 85 | 8 | 66 | 2775175 | 2775175 | 0 | 14 | 120285023 | QUALIFIES |
| ihs108 | 2014-07-21 | 2018-10-10 | 1543 | 1529 | 14 | 3 | 6 | 7770009 | 7770009 | 0 | 20 | 341714573 | QUALIFIES |
| ihs11 | 2015-11-18 | 2016-01-10 | 54 | 44 | 10 | 2 | 8 | 435820 | 435820 | 0 | 18 | 19685099 | fails duration |
| ihs114 | 2014-08-14 | 2018-10-10 | 1519 | 1478 | 41 | 1 | 41 | 6230990 | 6230990 | 0 | 20 | 273681379 | QUALIFIES |
| ihs118 | 2018-06-18 | 2018-09-11 | 86 | 86 | 0 | 0 | 0 | 356010 | 356010 | 0 | 19 | 15836235 | fails duration |
| ihs12 | 2015-11-30 | 2016-02-24 | 87 | 87 | 0 | 0 | 0 | 233925 | 233925 | 0 | 25 | 10438360 | fails duration |
| ihs21 | 2015-12-09 | 2016-02-12 | 66 | 66 | 0 | 0 | 0 | 560861 | 560861 | 0 | 24 | 24831234 | fails duration |
| ihs28 | 2016-02-16 | 2016-05-04 | 79 | 79 | 0 | 0 | 0 | 534926 | 534926 | 0 | 32 | 24233368 | fails duration |
| ihs35 | 2016-02-09 | 2016-03-24 | 45 | 2 | 43 | 1 | 43 | 1402 | 1402 | 0 | 26 | 58168 | fails duration |
| ihs37 | 2016-03-25 | 2016-05-20 | 57 | 57 | 0 | 0 | 0 | 173438 | 173438 | 0 | 20 | 7720426 | fails duration |
| ihs38 | 2016-03-25 | 2016-06-01 | 69 | 69 | 0 | 0 | 0 | 570398 | 570398 | 0 | 26 | 25582723 | fails duration |
| ihs40 | 2016-03-31 | 2016-06-03 | 65 | 65 | 0 | 0 | 0 | 730647 | 730647 | 0 | 22 | 32765834 | fails duration |
| ihs58 | 2016-08-11 | 2016-10-17 | 68 | 68 | 0 | 0 | 0 | 626677 | 626677 | 0 | 31 | 28033031 | fails duration |
| ihs59 | 2016-08-09 | 2016-11-04 | 88 | 84 | 4 | 1 | 4 | 576826 | 576826 | 0 | 28 | 25597190 | fails duration |
| ihs68 | 2016-07-28 | 2016-12-09 | 135 | 132 | 3 | 1 | 3 | 1131459 | 1131459 | 0 | 23 | 50282805 | QUALIFIES |
| ihs70 | 2016-07-17 | 2016-09-23 | 69 | 69 | 0 | 0 | 0 | 511793 | 511793 | 0 | 29 | 22833315 | fails duration |
| ihs75 | 2016-10-26 | 2017-02-28 | 126 | 126 | 0 | 0 | 0 | 1401081 | 1401081 | 0 | 30 | 62048324 | QUALIFIES |
| ihs80 | 2016-10-11 | 2017-01-23 | 105 | 105 | 0 | 0 | 0 | 1223630 | 1223630 | 0 | 23 | 55018040 | QUALIFIES |
| ihs84 | 2016-09-15 | 2017-11-07 | 419 | 419 | 0 | 0 | 0 | 1780314 | 1780314 | 0 | 17 | 78403363 | QUALIFIES |
| ihs85 | 2016-09-15 | 2019-03-27 | 924 | 913 | 11 | 1 | 11 | 5501913 | 5501913 | 0 | 17 | 245656418 | QUALIFIES |
| ihs95 | 2017-05-19 | 2017-07-26 | 69 | 69 | 0 | 0 | 0 | 939773 | 939773 | 0 | 36 | 41920819 | fails duration |
| ihs96 | 2017-05-19 | 2017-07-26 | 69 | 69 | 0 | 0 | 0 | 267724 | 267724 | 0 | 30 | 11867843 | fails duration |
| rw101 | 2014-07-17 | 2018-10-09 | 1546 | 1461 | 85 | 8 | 66 | 2774741 | 2774741 | 0 | 14 | 120266434 | QUALIFIES |
| rw103 | 2014-08-14 | 2016-04-06 | 602 | 561 | 41 | 1 | 41 | 3045556 | 3045556 | 0 | 20 | 136177670 | QUALIFIES |
| rw105 | 2014-08-14 | 2018-10-09 | 1518 | 1477 | 41 | 1 | 41 | 6228936 | 6228936 | 0 | 20 | 279822135 | QUALIFIES |
| rw106 | 2014-07-21 | 2018-10-09 | 1542 | 1528 | 14 | 3 | 6 | 7766284 | 7766284 | 0 | 20 | 349314575 | QUALIFIES |
| rw107 | 2014-07-21 | 2017-06-04 | 1050 | 922 | 128 | 5 | 71 | 2464871 | 2464871 | 0 | 20 | 110776568 | QUALIFIES |
| tm001 | 2016-09-16 | 2016-11-15 | 61 | 61 | 0 | 0 | 0 | 248180 | 248180 | 0 | 17 | 10974457 | fails duration |
| tm002 | 2016-09-14 | 2017-02-23 | 163 | 152 | 11 | 1 | 11 | 806980 | 806980 | 0 | 8 | 31559172 | QUALIFIES |
| tm003 | 2016-11-22 | 2017-01-31 | 71 | 71 | 0 | 0 | 0 | 209095 | 209095 | 0 | 15 | 9309712 | fails duration |
| tm005 | 2016-10-28 | 2016-11-27 | 31 | 31 | 0 | 0 | 0 | 272835 | 272835 | 0 | 19 | 12246092 | fails duration |
| tm006 | 2017-11-16 | 2017-12-15 | 30 | 30 | 0 | 0 | 0 | 222154 | 222154 | 0 | 8 | 8692993 | fails duration |
| tm007 | 2018-01-06 | 2018-02-04 | 30 | 30 | 0 | 0 | 0 | 84429 | 84429 | 0 | 7 | 3344284 | fails duration |
| tm008 | 2018-03-30 | 2019-03-12 | 348 | 348 | 0 | 0 | 0 | 1529218 | 1529218 | 0 | 18 | 67148410 | QUALIFIES |
| tm009 | 2018-11-01 | 2018-12-31 | 61 | 61 | 0 | 0 | 0 | 207550 | 207550 | 0 | 7 | 8304646 | fails duration |
| tm010 | 2019-03-27 | 2019-05-26 | 61 | 61 | 0 | 0 | 0 | 391352 | 391352 | 0 | 22 | 17431074 | fails duration |
| tm011 | 2019-04-01 | 2019-05-31 | 61 | 61 | 0 | 0 | 0 | 571037 | 571037 | 0 | 29 | 25560613 | fails duration |
| tm013 | 2019-04-01 | 2019-05-31 | 61 | 61 | 0 | 0 | 0 | 295910 | 295910 | 0 | 13 | 13189825 | fails duration |
| tm014 | 2019-07-01 | 2019-08-31 | 62 | 62 | 0 | 0 | 0 | 164863 | 164863 | 0 | 20 | 7404046 | fails duration |
| tm015 | 2019-12-11 | 2020-01-10 | 31 | 31 | 0 | 0 | 0 | 160436 | 160436 | 0 | 14 | 7327052 | fails duration |
| tm016 | 2019-12-15 | 2020-01-15 | 32 | 32 | 0 | 0 | 0 | 75437 | 75437 | 0 | 22 | 3440941 | fails duration |
| tm017 | 2019-12-15 | 2020-01-15 | 32 | 32 | 0 | 0 | 0 | 101337 | 101337 | 0 | 25 | 4510096 | fails duration |
| tm018 | 2021-07-19 | 2021-08-19 | 32 | 32 | 0 | 0 | 0 | 142139 | 142139 | 0 | 16 | 6353737 | fails duration |
| tm019 | 2021-07-14 | 2022-06-01 | 323 | 323 | 0 | 0 | 0 | 1013189 | 1013189 | 0 | 16 | 45170940 | QUALIFIES |
| tm020 | 2021-07-14 | 2021-08-11 | 29 | 29 | 0 | 0 | 0 | 164100 | 164100 | 0 | 27 | 7310102 | fails duration |
| tm021 | 2021-08-15 | 2021-09-15 | 32 | 32 | 0 | 0 | 0 | 188280 | 188280 | 0 | 21 | 8405671 | fails duration |
| tm022 | 2021-08-15 | 2021-09-15 | 32 | 32 | 0 | 0 | 0 | 304472 | 304472 | 0 | 19 | 13809013 | fails duration |
| tm026 | 2021-12-06 | 2022-01-06 | 32 | 32 | 0 | 0 | 0 | 270746 | 270746 | 0 | 27 | 12059197 | fails duration |
| tm029 | 2022-05-08 | 2022-06-08 | 32 | 32 | 0 | 0 | 0 | 168977 | 168977 | 0 | 27 | 7564505 | fails duration |
| tm032 | 2022-07-14 | 2023-11-09 | 484 | 182 | 302 | 2 | 287 | 140154 | 140154 | 0 | 18 | 6164285 | QUALIFIES |
| tm035 | 2022-10-21 | 2023-11-05 | 381 | 363 | 18 | 1 | 18 | 2066887 | 2066887 | 0 | 26 | 92343148 | QUALIFIES |
| tm036 | 2023-03-12 | 2023-12-01 | 265 | 241 | 24 | 1 | 24 | 1142132 | 1142132 | 0 | 26 | 51761586 | QUALIFIES |
| tm037 | 2023-03-03 | 2023-10-22 | 234 | 230 | 4 | 1 | 4 | 950727 | 950727 | 0 | 16 | 43035880 | QUALIFIES |
| tm038 | 2023-03-03 | 2023-10-22 | 234 | 212 | 22 | 1 | 22 | 2751305 | 2751305 | 0 | 17 | 126613122 | QUALIFIES |
| tm039 | 2023-03-06 | 2023-10-25 | 234 | 188 | 46 | 1 | 46 | 1421741 | 1421741 | 0 | 24 | 64325166 | QUALIFIES |
| tm040 | 2023-04-01 | 2023-10-15 | 198 | 181 | 17 | 2 | 15 | 1772131 | 1772131 | 0 | 16 | 81720239 | QUALIFIES |
| tm041 | 2023-05-09 | 2023-11-02 | 178 | 178 | 0 | 0 | 0 | 1471725 | 1471725 | 0 | 17 | 66258476 | QUALIFIES |
| tm042 | 2023-06-07 | 2024-01-23 | 231 | 231 | 0 | 0 | 0 | 1764284 | 1764284 | 0 | 15 | 81957752 | QUALIFIES |
| tm043 | 2023-07-26 | 2024-02-01 | 191 | 191 | 0 | 0 | 0 | 496207 | 496207 | 0 | 17 | 21928778 | QUALIFIES |

Totals across the 89 audited candidates: 183,102,524 raw rows, 183,102,523 eligible rows, 1 malformed row (see §6).

## 5. Gaps within qualifying homes (largest gaps only)

Gaps are intervals between consecutive distinct interpretable dates; "missing days" counts dates inside the span with no interpretable record. The rule counts distinct dates and span only — gaps do not disqualify — but they are recorded here because later steps must decide segment/outage handling per home.

- **aruba**: span 220d, 220 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **hh101**: span 373d, 371 dates, 2 missing days in span, 2 gap intervals, max gap 1d. Largest: 2012-07-18..2012-07-20 (1d missing); 2013-07-07..2013-07-09 (1d missing).
- **hh102**: span 1037d, 1000 dates, 37 missing days in span, 7 gap intervals, max gap 27d. Largest: 2011-08-24..2011-09-21 (27d missing); 2012-11-20..2012-11-24 (3d missing); 2014-03-23..2014-03-26 (2d missing); 2014-03-27..2014-03-30 (2d missing); 2014-03-16..2014-03-18 (1d missing).
- **hh103**: span 2157d, 2071 dates, 86 missing days in span, 3 gap intervals, max gap 54d. Largest: 2015-11-18..2016-01-12 (54d missing); 2016-11-10..2016-12-12 (31d missing); 2011-09-30..2011-10-02 (1d missing).
- **hh104**: span 813d, 812 dates, 1 missing days in span, 1 gap intervals, max gap 1d. Largest: 2013-08-01..2013-08-03 (1d missing).
- **hh105**: span 2108d, 1859 dates, 249 missing days in span, 14 gap intervals, max gap 94d. Largest: 2014-01-12..2014-04-17 (94d missing); 2013-07-28..2013-10-21 (84d missing); 2013-10-29..2013-11-22 (23d missing); 2012-04-24..2012-05-10 (15d missing); 2011-06-27..2011-07-12 (14d missing).
- **hh106**: span 1301d, 1124 dates, 177 missing days in span, 13 gap intervals, max gap 55d. Largest: 2013-12-31..2014-02-25 (55d missing); 2013-11-27..2013-12-30 (32d missing); 2014-03-10..2014-04-04 (24d missing); 2013-10-30..2013-11-22 (22d missing); 2013-09-09..2013-09-26 (16d missing).
- **hh108**: span 2158d, 1841 dates, 317 missing days in span, 12 gap intervals, max gap 124d. Largest: 2014-06-07..2014-10-10 (124d missing); 2013-06-22..2013-10-21 (120d missing); 2014-11-02..2014-12-12 (39d missing); 2013-10-29..2013-11-22 (23d missing); 2013-11-22..2013-11-26 (3d missing).
- **hh109**: span 1666d, 1649 dates, 17 missing days in span, 6 gap intervals, max gap 5d. Largest: 2013-01-04..2013-01-10 (5d missing); 2012-11-20..2012-11-24 (3d missing); 2012-12-26..2012-12-30 (3d missing); 2012-11-16..2012-11-19 (2d missing); 2013-01-28..2013-01-31 (2d missing).
- **hh111**: span 2158d, 2135 dates, 23 missing days in span, 5 gap intervals, max gap 16d. Largest: 2014-04-01..2014-04-18 (16d missing); 2014-08-04..2014-08-09 (4d missing); 2013-11-08..2013-11-10 (1d missing); 2014-03-23..2014-03-25 (1d missing); 2015-12-31..2016-01-02 (1d missing).
- **hh112**: span 139d, 137 dates, 2 missing days in span, 1 gap intervals, max gap 2d. Largest: 2011-09-07..2011-09-10 (2d missing).
- **hh113**: span 510d, 496 dates, 14 missing days in span, 1 gap intervals, max gap 14d. Largest: 2011-11-18..2011-12-03 (14d missing).
- **hh114**: span 2153d, 1872 dates, 281 missing days in span, 9 gap intervals, max gap 156d. Largest: 2014-01-12..2014-06-18 (156d missing); 2013-07-28..2013-10-21 (84d missing); 2013-10-30..2013-11-22 (22d missing); 2013-12-10..2013-12-20 (9d missing); 2013-10-23..2013-10-29 (5d missing).
- **hh115**: span 346d, 339 dates, 7 missing days in span, 3 gap intervals, max gap 5d. Largest: 2012-01-20..2012-01-26 (5d missing); 2011-07-05..2011-07-07 (1d missing); 2012-05-13..2012-05-15 (1d missing).
- **hh116**: span 157d, 148 dates, 9 missing days in span, 8 gap intervals, max gap 2d. Largest: 2011-09-14..2011-09-17 (2d missing); 2011-09-01..2011-09-03 (1d missing); 2011-09-25..2011-09-27 (1d missing); 2011-10-06..2011-10-08 (1d missing); 2011-10-10..2011-10-12 (1d missing).
- **hh117**: span 346d, 293 dates, 53 missing days in span, 5 gap intervals, max gap 44d. Largest: 2011-08-07..2011-09-21 (44d missing); 2011-07-05..2011-07-12 (6d missing); 2012-03-22..2012-03-24 (1d missing); 2012-03-24..2012-03-26 (1d missing); 2012-04-27..2012-04-29 (1d missing).
- **hh118**: span 1172d, 1039 dates, 133 missing days in span, 4 gap intervals, max gap 88d. Largest: 2014-04-20..2014-07-18 (88d missing); 2013-03-19..2013-04-29 (40d missing); 2012-01-20..2012-01-25 (4d missing); 2014-04-06..2014-04-08 (1d missing).
- **hh119**: span 1930d, 1531 dates, 399 missing days in span, 11 gap intervals, max gap 365d. Largest: 2015-12-12..2016-12-12 (365d missing); 2012-06-20..2012-07-12 (21d missing); 2012-09-08..2012-09-12 (3d missing); 2013-07-20..2013-07-24 (3d missing); 2012-10-05..2012-10-07 (1d missing).
- **hh120**: span 716d, 662 dates, 54 missing days in span, 7 gap intervals, max gap 23d. Largest: 2013-10-29..2013-11-22 (23d missing); 2013-09-10..2013-09-26 (15d missing); 2013-10-22..2013-10-29 (6d missing); 2013-08-07..2013-08-12 (4d missing); 2013-10-09..2013-10-14 (4d missing).
- **hh122**: span 213d, 213 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **hh123**: span 587d, 587 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **hh124**: span 147d, 144 dates, 3 missing days in span, 3 gap intervals, max gap 1d. Largest: 2013-05-20..2013-05-22 (1d missing); 2013-05-24..2013-05-26 (1d missing); 2013-07-23..2013-07-25 (1d missing).
- **hh125**: span 1533d, 1524 dates, 9 missing days in span, 9 gap intervals, max gap 1d. Largest: 2014-05-22..2014-05-24 (1d missing); 2014-06-27..2014-06-29 (1d missing); 2014-07-23..2014-07-25 (1d missing); 2014-08-12..2014-08-14 (1d missing); 2014-08-30..2014-09-01 (1d missing).
- **hh126**: span 1379d, 1228 dates, 151 missing days in span, 1 gap intervals, max gap 151d. Largest: 2014-08-31..2015-01-30 (151d missing).
- **hh127**: span 1289d, 1206 dates, 83 missing days in span, 25 gap intervals, max gap 19d. Largest: 2013-08-27..2013-09-16 (19d missing); 2013-10-29..2013-11-14 (15d missing); 2016-09-10..2016-09-21 (10d missing); 2015-11-12..2015-11-21 (8d missing); 2014-10-15..2014-10-21 (5d missing).
- **hh128**: span 1359d, 1211 dates, 148 missing days in span, 3 gap intervals, max gap 63d. Largest: 2013-08-20..2013-10-23 (63d missing); 2014-06-29..2014-08-21 (52d missing); 2014-04-25..2014-05-29 (33d missing).
- **hh129**: span 1145d, 664 dates, 481 missing days in span, 5 gap intervals, max gap 252d. Largest: 2014-09-02..2015-05-13 (252d missing); 2013-11-01..2014-04-16 (165d missing); 2014-04-25..2014-05-29 (33d missing); 2015-06-05..2015-07-06 (30d missing); 2015-05-31..2015-06-02 (1d missing).
- **hh130**: span 251d, 172 dates, 79 missing days in span, 10 gap intervals, max gap 58d. Largest: 2014-04-20..2014-06-18 (58d missing); 2014-11-09..2014-11-21 (11d missing); 2014-12-02..2014-12-06 (3d missing); 2014-11-22..2014-11-24 (1d missing); 2014-11-25..2014-11-27 (1d missing).
- **ihs107**: span 1547d, 1462 dates, 85 missing days in span, 8 gap intervals, max gap 66d. Largest: 2014-07-17..2014-09-22 (66d missing); 2016-11-11..2016-11-21 (9d missing); 2018-02-21..2018-02-26 (4d missing); 2017-02-12..2017-02-15 (2d missing); 2016-06-01..2016-06-03 (1d missing).
- **ihs108**: span 1543d, 1529 dates, 14 missing days in span, 3 gap intervals, max gap 6d. Largest: 2018-05-31..2018-06-07 (6d missing); 2018-08-21..2018-08-28 (6d missing); 2014-07-21..2014-07-24 (2d missing).
- **ihs114**: span 1519d, 1478 dates, 41 missing days in span, 1 gap intervals, max gap 41d. Largest: 2014-08-14..2014-09-25 (41d missing).
- **ihs68**: span 135d, 132 dates, 3 missing days in span, 1 gap intervals, max gap 3d. Largest: 2016-10-16..2016-10-20 (3d missing).
- **ihs75**: span 126d, 126 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **ihs80**: span 105d, 105 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **ihs84**: span 419d, 419 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **ihs85**: span 924d, 913 dates, 11 missing days in span, 1 gap intervals, max gap 11d. Largest: 2016-12-01..2016-12-13 (11d missing).
- **rw101**: span 1546d, 1461 dates, 85 missing days in span, 8 gap intervals, max gap 66d. Largest: 2014-07-17..2014-09-22 (66d missing); 2016-11-11..2016-11-21 (9d missing); 2018-02-21..2018-02-26 (4d missing); 2017-02-12..2017-02-15 (2d missing); 2016-06-01..2016-06-03 (1d missing).
- **rw103**: span 602d, 561 dates, 41 missing days in span, 1 gap intervals, max gap 41d. Largest: 2014-08-14..2014-09-25 (41d missing).
- **rw105**: span 1518d, 1477 dates, 41 missing days in span, 1 gap intervals, max gap 41d. Largest: 2014-08-14..2014-09-25 (41d missing).
- **rw106**: span 1542d, 1528 dates, 14 missing days in span, 3 gap intervals, max gap 6d. Largest: 2018-05-31..2018-06-07 (6d missing); 2018-08-21..2018-08-28 (6d missing); 2014-07-21..2014-07-24 (2d missing).
- **rw107**: span 1050d, 922 dates, 128 missing days in span, 5 gap intervals, max gap 71d. Largest: 2015-06-22..2015-09-02 (71d missing); 2015-04-14..2015-05-30 (45d missing); 2015-11-01..2015-11-09 (7d missing); 2015-11-17..2015-11-21 (3d missing); 2014-07-21..2014-07-24 (2d missing).
- **tm002**: span 163d, 152 dates, 11 missing days in span, 1 gap intervals, max gap 11d. Largest: 2016-12-01..2016-12-13 (11d missing).
- **tm008**: span 348d, 348 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **tm019**: span 323d, 323 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **tm032**: span 484d, 182 dates, 302 missing days in span, 2 gap intervals, max gap 287d. Largest: 2022-07-17..2023-05-01 (287d missing); 2023-05-14..2023-05-30 (15d missing).
- **tm035**: span 381d, 363 dates, 18 missing days in span, 1 gap intervals, max gap 18d. Largest: 2022-10-26..2022-11-14 (18d missing).
- **tm036**: span 265d, 241 dates, 24 missing days in span, 1 gap intervals, max gap 24d. Largest: 2023-04-15..2023-05-10 (24d missing).
- **tm037**: span 234d, 230 dates, 4 missing days in span, 1 gap intervals, max gap 4d. Largest: 2023-04-13..2023-04-18 (4d missing).
- **tm038**: span 234d, 212 dates, 22 missing days in span, 1 gap intervals, max gap 22d. Largest: 2023-04-23..2023-05-16 (22d missing).
- **tm039**: span 234d, 188 dates, 46 missing days in span, 1 gap intervals, max gap 46d. Largest: 2023-04-05..2023-05-22 (46d missing).
- **tm040**: span 198d, 181 dates, 17 missing days in span, 2 gap intervals, max gap 15d. Largest: 2023-06-26..2023-07-12 (15d missing); 2023-04-29..2023-05-02 (2d missing).
- **tm041**: span 178d, 178 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **tm042**: span 231d, 231 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.
- **tm043**: span 191d, 191 dates, 0 missing days in span, 0 gap intervals, max gap 0d. Largest: none.

Notable: `tm032` qualifies with only 182 dates over a 484-day span (max gap 287 missing days); `hh119` has a 365-day max gap; `hh114` 156d; `hh126` 151d; `hh108` 124d. These homes pass the frozen rule but are gappy — flag for Step 04/05 segment decisions.

## 6. Data-quality notes from streaming

- **Serializations observed:** two, cleanly split by group. Comma format (`date,time,sensor,message`): `aruba`, all `ihs*`, `rw*`, `tm*` (60 homes). Space format (`date time sensor message`): all `hh*` (29 homes). No home mixed formats; no header lines observed (first physical line is a data record in every streamed member).
- **Malformed rows:** 1 of 183,102,524 audited rows — `ihs07`, physical line 123429 (content not retained; line-number only). All other 88 candidates had zero malformed rows; no invalid calendar-date strings anywhere.
- **Message vocabulary is wider than motion/door ON/OFF/OPEN/CLOSE.** Observed tokens include numeric readings (e.g. `0`–`100`, `14.5`–`34.5`, plausibly temperature/humidity-style payloads), button-event tokens (`DEPRESS`, `RELEASE`, `TAP`, `TAP_COUNT_00`–`TAP_COUNT_09`, `HOLD_DEPRESS`, `HOLD_RELEASE`), and two anomalies: a bare `OF` token in `tm005` and `tm014` (possibly a truncated `OFF`; occurrence counts not tallied), and tab-terminated tokens (`ON\t`, `OFF\t`, `OPEN\t`, `CLOSE\t`) in a few homes. These rows counted as *interpretable* under the frozen structural definition, but they are **not** all motion/door positives — sensor/message mapping in the next step must classify them (and must not treat numeric/button payloads as activity positives) before any label or feature work.
- **Sensor-token counts** range 5–36 per home (`ihs*` homes carry the most distinct tokens, up to 36 in `ihs95`). Counts are distinct identifier strings only; room names are mapped labels, not device types.

## 7. Exclusions with reasons

### 7a. Candidates failing the duration rule (35)

- **hh110**: 31 distinct dates / 35-day span (2011-06-15..2011-07-19), 112,544 rows — fails ≥90 dates AND ≥90-day span.
- **ihs07**: 71 distinct dates / 71-day span (2015-10-04..2015-12-13), 451,520 rows — fails ≥90 dates AND ≥90-day span.
- **ihs11**: 44 distinct dates / 54-day span (2015-11-18..2016-01-10), 435,820 rows — fails ≥90 dates AND ≥90-day span.
- **ihs118**: 86 distinct dates / 86-day span (2018-06-18..2018-09-11), 356,010 rows — fails ≥90 dates AND ≥90-day span.
- **ihs12**: 87 distinct dates / 87-day span (2015-11-30..2016-02-24), 233,925 rows — fails ≥90 dates AND ≥90-day span.
- **ihs21**: 66 distinct dates / 66-day span (2015-12-09..2016-02-12), 560,861 rows — fails ≥90 dates AND ≥90-day span.
- **ihs28**: 79 distinct dates / 79-day span (2016-02-16..2016-05-04), 534,926 rows — fails ≥90 dates AND ≥90-day span.
- **ihs35**: 2 distinct dates / 45-day span (2016-02-09..2016-03-24), 1,402 rows — fails ≥90 dates AND ≥90-day span.
- **ihs37**: 57 distinct dates / 57-day span (2016-03-25..2016-05-20), 173,438 rows — fails ≥90 dates AND ≥90-day span.
- **ihs38**: 69 distinct dates / 69-day span (2016-03-25..2016-06-01), 570,398 rows — fails ≥90 dates AND ≥90-day span.
- **ihs40**: 65 distinct dates / 65-day span (2016-03-31..2016-06-03), 730,647 rows — fails ≥90 dates AND ≥90-day span.
- **ihs58**: 68 distinct dates / 68-day span (2016-08-11..2016-10-17), 626,677 rows — fails ≥90 dates AND ≥90-day span.
- **ihs59**: 84 distinct dates / 88-day span (2016-08-09..2016-11-04), 576,826 rows — fails ≥90 dates AND ≥90-day span.
- **ihs70**: 69 distinct dates / 69-day span (2016-07-17..2016-09-23), 511,793 rows — fails ≥90 dates AND ≥90-day span.
- **ihs95**: 69 distinct dates / 69-day span (2017-05-19..2017-07-26), 939,773 rows — fails ≥90 dates AND ≥90-day span.
- **ihs96**: 69 distinct dates / 69-day span (2017-05-19..2017-07-26), 267,724 rows — fails ≥90 dates AND ≥90-day span.
- **tm001**: 61 distinct dates / 61-day span (2016-09-16..2016-11-15), 248,180 rows — fails ≥90 dates AND ≥90-day span.
- **tm003**: 71 distinct dates / 71-day span (2016-11-22..2017-01-31), 209,095 rows — fails ≥90 dates AND ≥90-day span.
- **tm005**: 31 distinct dates / 31-day span (2016-10-28..2016-11-27), 272,835 rows — fails ≥90 dates AND ≥90-day span.
- **tm006**: 30 distinct dates / 30-day span (2017-11-16..2017-12-15), 222,154 rows — fails ≥90 dates AND ≥90-day span.
- **tm007**: 30 distinct dates / 30-day span (2018-01-06..2018-02-04), 84,429 rows — fails ≥90 dates AND ≥90-day span.
- **tm009**: 61 distinct dates / 61-day span (2018-11-01..2018-12-31), 207,550 rows — fails ≥90 dates AND ≥90-day span.
- **tm010**: 61 distinct dates / 61-day span (2019-03-27..2019-05-26), 391,352 rows — fails ≥90 dates AND ≥90-day span.
- **tm011**: 61 distinct dates / 61-day span (2019-04-01..2019-05-31), 571,037 rows — fails ≥90 dates AND ≥90-day span.
- **tm013**: 61 distinct dates / 61-day span (2019-04-01..2019-05-31), 295,910 rows — fails ≥90 dates AND ≥90-day span.
- **tm014**: 62 distinct dates / 62-day span (2019-07-01..2019-08-31), 164,863 rows — fails ≥90 dates AND ≥90-day span.
- **tm015**: 31 distinct dates / 31-day span (2019-12-11..2020-01-10), 160,436 rows — fails ≥90 dates AND ≥90-day span.
- **tm016**: 32 distinct dates / 32-day span (2019-12-15..2020-01-15), 75,437 rows — fails ≥90 dates AND ≥90-day span.
- **tm017**: 32 distinct dates / 32-day span (2019-12-15..2020-01-15), 101,337 rows — fails ≥90 dates AND ≥90-day span.
- **tm018**: 32 distinct dates / 32-day span (2021-07-19..2021-08-19), 142,139 rows — fails ≥90 dates AND ≥90-day span.
- **tm020**: 29 distinct dates / 29-day span (2021-07-14..2021-08-11), 164,100 rows — fails ≥90 dates AND ≥90-day span.
- **tm021**: 32 distinct dates / 32-day span (2021-08-15..2021-09-15), 188,280 rows — fails ≥90 dates AND ≥90-day span.
- **tm022**: 32 distinct dates / 32-day span (2021-08-15..2021-09-15), 304,472 rows — fails ≥90 dates AND ≥90-day span.
- **tm026**: 32 distinct dates / 32-day span (2021-12-06..2022-01-06), 270,746 rows — fails ≥90 dates AND ≥90-day span.
- **tm029**: 32 distinct dates / 32-day span (2022-05-08..2022-06-08), 168,977 rows — fails ≥90 dates AND ≥90-day span.

Near misses: `ihs12` (87 dates / 87 days), `ihs118` (86/86), `ihs59` (84/88), `ihs28` (79/79). Sparse case: `ihs35` has only 2 distinct dates across a 45-day span (1,402 rows).

### 7b. Ambiguous mn/mv rows (4) — NOT streamed

- **mn57, mn77, mn82, mn85**: resident-count row says `mn` but catalog description says `mv`; demographic evidence marked `verify_group_description` in the kit. Excluded pending corroboration.

### 7c. Evidence row absent from the archive (1)

- **mv101**: supported single-resident older-adult evidence in `resident_evidence.csv`, but no `data/mv101.csv` member exists in `data.zip` (not in inventory). Cannot audit.

### 7d. Archive members with no demographic evidence in the kit (96) — NOT streamed

No resident-count or age evidence for these members in `resident_evidence.csv`; resident status unknown from kit sources. Listed for completeness; do not treat group prefixes as evidence of household composition.

`atmo1`, `atmo10`, `atmo2`, `atmo4`, `atmo6`, `atmo7`, `atmo8`, `atmo9`, `cairo`, `hh107`, `hh121`, `ihs06`, `ihs08`, `ihs09`, `ihs100`, `ihs101`, `ihs104`, `ihs109`, `ihs115`, `ihs116`, `ihs117`, `ihs119`, `ihs120`, `ihs121`, `ihs123`, `ihs124`, `ihs125`, `ihs14`, `ihs22`, `ihs25`, `ihs31`, `ihs60`, `ihs93`, `ihs98`, `ihs99`, `kyoto07`, `kyoto10`, `kyoto11`, `kyoto12`, `kyoto13`, `kyoto14`, `kyoto15`, `kyoto16`, `kyoto17`, `kyoto18`, `kyoto19`, `kyoto20`, `kyoto21`, `laval`, `lc001`, `mb001`, `milan`, `mn33`, `mn50`, `mn51`, `mn58`, `mn59`, `mn61`, `mn62`, `mn64`, `mn71`, `mn73`, `mn76`, `mn79`, `mn83`, `mn86`, `mv001`, `mva001`, `mva002`, `mva003`, `mva004`, `navan`, `paris`, `ras001`, `rw102`, `rw104`, `rw110`, `shib003`, `shib004`, `shib009`, `shib010`, `shib011`, `shib014`, `shib015`, `shib017`, `shib018`, `shib019`, `shib020`, `shib024`, `shiblsdf`, `tm004`, `tm024`, `tm027`, `tm030`, `tm033`, `tulum`

## 8. Eligible beyond cap (34 qualifiers, ranks 21–54)

hh123, hh124, hh125, hh126, hh127, hh128, hh129, hh130, ihs107, ihs108, ihs114, ihs68, ihs75, ihs80, ihs84, ihs85, rw101, rw103, rw105, rw106, rw107, tm002, tm008, tm019, tm032, tm035, tm036, tm037, tm038, tm039, tm040, tm041, tm042, tm043

These satisfy the rule and the demographic evidence but fall outside the 20-home cap under casefolded ordering. Preserved distinctly in `cohort.csv` as `eligible_beyond_cap` for possible cap revision; they were fully audited (counts above), so no re-streaming is needed if the cap changes.

## 9. Cohort freeze

- **Frozen cohort (n=20):** aruba, hh101, hh102, hh103, hh104, hh105, hh106, hh108, hh109, hh111, hh112, hh113, hh114, hh115, hh116, hh117, hh118, hh119, hh120, hh122
- **Freeze hash (SHA-256 of `cohort.csv`):** `407d8ecff36a50d64ecb234641e8e5e5aa1c36c47821d8408377d69951f89f2f`
- **Cohort file:** `data/manifests/cohort.csv` (190 rows: 189 archive members + `mv101`; repo-safe export fields only — no raw sensor rows, no private data; split counts empty, not yet computed; approval_state `pending_approval` for the selected 20).
- **Selection basis:** deterministic casefolded order + frozen conservative rule; made without viewing any test outcomes or fitting any model.

## 10. What remains unaudited / not done

- The 96 no-evidence archive members were **not** streamed (no demographic basis in the kit); their resident counts/ages remain unknown. If the study later needs them, demographic evidence must be established first — do not infer from filename prefixes.
- The 4 ambiguous mn rows were not streamed (awaiting mn/mv corroboration).
- `mv101` cannot be audited (absent from archive).
- Train/dev/test split counts are not computed (empty in `cohort.csv`).
- No per-home timezone verification, sensor-message mapping, outage/segment analysis, or label work was done here — all belong to later steps. The gappy qualifiers in §5 and the non-motion message tokens in §6 are flagged for those steps.
- Nothing was committed, pushed, or written to GitHub; no raw logs were extracted to disk; the archive was not copied into git.

## Appendix — reproducibility

- Audit script: the Step 03 audit script (run log and machine-readable results kept in the coordinator work directory, not in the repo).
- Hashing: MD5 + SHA-256 recomputed over the 2,723,059,864-byte archive in a single streaming pass (18.6 s); both match the previously verified values.
- Archive used: the local CASAS v1 `data.zip` (private, not in the repo; MD5 verified, see §1). Inventory: 189 members. Evidence: `resident_evidence.csv` from the handoff kit (94 rows).
