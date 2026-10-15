# Metric Layer Validation

Reconciliation of `wii.metrics` estimates against the official CPSC Query Builder,
offline, for every in-scope workshop product code. Product-code reconciliation is
unaffected by narrative refinement -- refinement changes `final_tool_id`, never
`product_code`. Per-tool rows do not sum to total burden: a case involving two in-scope tools counts once under each. Every figure below is
an estimate of injuries *associated with* the product, never described as caused by it.

## Method

Stratified with-replacement (ultimate cluster) Taylor linearization; multi-year periods
pool strata as `year|stratum` and PSUs as `year|stratum|psu`; 95% CI uses
t(0.975, df) with df = pooled PSUs - pooled strata.

## 2009

| Product | Official Est | Ours Est | Official N | Ours N | Official CV | Ours CV | Match |
|---|---|---|---|---|---|---|---|
| 803 | 1,319 | 1319 | 29 | 29 | 0.21 | 0.21 | yes |
| 804 | 1,654 | 1654 | 35 | 35 | 0.19 | 0.1918 | yes |
| 805 | suppressed | 645 | suppressed | 10 | suppressed | 0.3779 | yes |
| 807 | suppressed | 844 | suppressed | 14 | suppressed | 0.3076 | yes |
| 808 | suppressed | 15 | suppressed | 1 | suppressed | 1.0 | yes |
| 809 | 3,274 | 3274 | 66 | 66 | 0.17 | 0.1747 | yes |
| 830 | 3,413 | 3413 | 74 | 74 | 0.17 | 0.1748 | yes |
| 832 | 12,636 | 12636 | 293 | 293 | 0.11 | 0.1074 | yes |
| 841 | 35,624 | 35624 | 783 | 783 | 0.1 | 0.1 | yes |
| 842 | 3,895 | 3895 | 81 | 81 | 0.17 | 0.1663 | yes |
| 843 | suppressed | 242 | suppressed | 8 | suppressed | 0.4309 | yes |
| 845 | 16,449 | 16449 | 395 | 395 | 0.12 | 0.1164 | yes |
| 847 | 9,947 | 9947 | 231 | 231 | 0.13 | 0.1292 | yes |
| 855 | 3,157 | 3157 | 58 | 58 | 0.21 | 0.207 | yes |
| 856 | suppressed | 870 | suppressed | 19 | suppressed | 0.2726 | yes |
| 862 | 2,050 | 2050 | 38 | 38 | 0.25 | 0.2472 | yes |
| 864 | suppressed | 458 | suppressed | 7 | suppressed | 0.3605 | yes |
| 870 | 5,764 | 5764 | 110 | 110 | 0.13 | 0.1268 | yes |
| 871 | 4,147 | 4147 | 80 | 80 | 0.16 | 0.1597 | yes |
| 875 | suppressed | 659 | suppressed | 13 | suppressed | 0.3249 | yes |
| 879 | suppressed | 994 | suppressed | 22 | suppressed | 0.2826 | yes |
| 882 | 9,146 | 9146 | 211 | 211 | 0.11 | 0.1135 | yes |
| 894 | suppressed | 597 | suppressed | 17 | suppressed | 0.2693 | yes |
| 895 | 12,714 | 12714 | 267 | 267 | 0.1 | 0.1012 | yes |
| 897 | 6,517 | 6517 | 111 | 111 | 0.23 | 0.2289 | yes |
| 898 | suppressed | 390 | suppressed | 10 | suppressed | 0.3703 | yes |
| 899 | 18,450 | 18450 | 361 | 361 | 0.12 | 0.1189 | yes |

## 2014

| Product | Official Est | Ours Est | Official N | Ours N | Official CV | Ours CV | Match |
|---|---|---|---|---|---|---|---|
| 803 | 1,590 | 1590 | 30 | 30 | 0.27 | 0.2726 | yes |
| 804 | 2,003 | 2003 | 34 | 34 | 0.19 | 0.1932 | yes |
| 805 | suppressed | 702 | suppressed | 11 | suppressed | 0.3165 | yes |
| 807 | suppressed | 509 | suppressed | 9 | suppressed | 0.4124 | yes |
| 808 | not published | 0 | -- | 0 | -- | None | n/a (not published) |
| 809 | 1,766 | 1766 | 43 | 43 | 0.21 | 0.2104 | yes |
| 830 | 3,590 | 3590 | 80 | 80 | 0.16 | 0.162 | yes |
| 832 | 12,124 | 12124 | 279 | 279 | 0.1 | 0.0978 | yes |
| 841 | 33,475 | 33475 | 700 | 700 | 0.09 | 0.0868 | yes |
| 842 | 2,436 | 2436 | 52 | 52 | 0.19 | 0.1853 | yes |
| 843 | suppressed | 407 | suppressed | 7 | suppressed | 0.5309 | yes |
| 845 | 13,915 | 13915 | 315 | 315 | 0.13 | 0.1271 | yes |
| 847 | 9,981 | 9981 | 210 | 210 | 0.15 | 0.1487 | yes |
| 855 | 3,679 | 3679 | 63 | 63 | 0.23 | 0.2324 | yes |
| 856 | suppressed | 324 | suppressed | 7 | suppressed | 0.4432 | yes |
| 862 | 1,514 | 1514 | 38 | 38 | 0.3 | 0.2964 | yes |
| 864 | suppressed | 1008 | suppressed | 17 | suppressed | 0.2865 | yes |
| 870 | 3,933 | 3933 | 82 | 82 | 0.15 | 0.1538 | yes |
| 871 | 4,595 | 4595 | 98 | 98 | 0.23 | 0.2289 | yes |
| 875 | suppressed | 455 | suppressed | 11 | suppressed | 0.4512 | yes |
| 879 | suppressed | 750 | suppressed | 14 | suppressed | 0.3133 | yes |
| 882 | 9,288 | 9288 | 186 | 186 | 0.1 | 0.0971 | yes |
| 894 | suppressed | 805 | suppressed | 16 | suppressed | 0.3518 | yes |
| 895 | 9,847 | 9847 | 209 | 209 | 0.12 | 0.1181 | yes |
| 897 | 7,350 | 7350 | 138 | 138 | 0.23 | 0.2257 | yes |
| 898 | suppressed | 378 | suppressed | 17 | suppressed | 0.5462 | yes |
| 899 | 22,352 | 22352 | 379 | 379 | 0.16 | 0.1554 | yes |

## 2019

| Product | Official Est | Ours Est | Official N | Ours N | Official CV | Ours CV | Match |
|---|---|---|---|---|---|---|---|
| 803 | 1,634 | 1634 | 35 | 35 | 0.19 | 0.1944 | yes |
| 804 | suppressed | 1086 | suppressed | 20 | suppressed | 0.2223 | yes |
| 805 | suppressed | 274 | suppressed | 5 | suppressed | 0.6754 | yes |
| 807 | suppressed | 533 | suppressed | 8 | suppressed | 0.4085 | yes |
| 808 | not published | 0 | -- | 0 | -- | None | n/a (not published) |
| 809 | 3,144 | 3144 | 56 | 56 | 0.19 | 0.1946 | yes |
| 830 | 3,363 | 3363 | 69 | 69 | 0.17 | 0.1662 | yes |
| 832 | 8,561 | 8561 | 194 | 194 | 0.13 | 0.1313 | yes |
| 841 | 32,445 | 32445 | 668 | 668 | 0.1 | 0.0972 | yes |
| 842 | 2,496 | 2496 | 45 | 45 | 0.17 | 0.1716 | yes |
| 843 | suppressed | 120 | suppressed | 3 | suppressed | 0.731 | yes |
| 845 | 12,836 | 12836 | 278 | 278 | 0.1 | 0.1041 | yes |
| 847 | 10,225 | 10225 | 200 | 200 | 0.14 | 0.1375 | yes |
| 855 | 2,993 | 2993 | 60 | 60 | 0.17 | 0.1706 | yes |
| 856 | suppressed | 710 | suppressed | 11 | suppressed | 0.4167 | yes |
| 862 | 1,416 | 1416 | 29 | 29 | 0.25 | 0.2546 | yes |
| 864 | 1,377 | 1377 | 25 | 25 | 0.32 | 0.319 | yes |
| 870 | 2,536 | 2536 | 54 | 54 | 0.16 | 0.1646 | yes |
| 871 | 4,848 | 4848 | 110 | 110 | 0.27 | 0.2737 | yes |
| 875 | suppressed | 619 | suppressed | 9 | suppressed | 0.3597 | yes |
| 879 | suppressed | 410 | suppressed | 9 | suppressed | 0.4024 | yes |
| 882 | 9,570 | 9570 | 190 | 190 | 0.1 | 0.1034 | yes |
| 894 | suppressed | 317 | suppressed | 7 | suppressed | 0.4328 | yes |
| 895 | 15,130 | 15130 | 302 | 302 | 0.11 | 0.1106 | yes |
| 897 | 10,485 | 10485 | 186 | 186 | 0.14 | 0.1432 | yes |
| 898 | suppressed | 1028 | suppressed | 29 | suppressed | 0.3355 | yes |
| 899 | 20,279 | 20279 | 348 | 348 | 0.15 | 0.1491 | yes |

## 2024

| Product | Official Est | Ours Est | Official N | Ours N | Official CV | Ours CV | Match |
|---|---|---|---|---|---|---|---|
| 803 | 1,456 | 1456 | 23 | 23 | 0.26 | 0.264 | yes |
| 804 | 1,733 | 1733 | 29 | 29 | 0.19 | 0.1911 | yes |
| 805 | suppressed | 393 | suppressed | 9 | suppressed | 0.392 | yes |
| 807 | suppressed | 300 | suppressed | 6 | suppressed | 0.5314 | yes |
| 808 | not published | 0 | -- | 0 | -- | None | n/a (not published) |
| 809 | 3,473 | 3473 | 63 | 63 | 0.16 | 0.1552 | yes |
| 830 | 3,611 | 3611 | 63 | 63 | 0.17 | 0.1729 | yes |
| 832 | 9,539 | 9539 | 172 | 172 | 0.15 | 0.1495 | yes |
| 841 | 33,052 | 33052 | 560 | 560 | 0.1 | 0.1009 | yes |
| 842 | 2,382 | 2382 | 44 | 44 | 0.18 | 0.1788 | yes |
| 843 | suppressed | 213 | suppressed | 2 | suppressed | 0.6953 | yes |
| 845 | 20,980 | 20980 | 370 | 370 | 0.11 | 0.1126 | yes |
| 847 | 14,381 | 14381 | 251 | 251 | 0.12 | 0.122 | yes |
| 855 | 3,075 | 3075 | 42 | 42 | 0.26 | 0.258 | yes |
| 856 | suppressed | 664 | suppressed | 11 | suppressed | 0.3739 | yes |
| 862 | 2,573 | 2573 | 47 | 47 | 0.21 | 0.2099 | yes |
| 864 | suppressed | 1198 | suppressed | 18 | suppressed | 0.3322 | yes |
| 870 | 2,955 | 2955 | 52 | 52 | 0.18 | 0.1771 | yes |
| 871 | 5,578 | 5578 | 107 | 107 | 0.16 | 0.1556 | yes |
| 875 | suppressed | 590 | suppressed | 11 | suppressed | 0.3463 | yes |
| 879 | suppressed | 1184 | suppressed | 22 | suppressed | 0.238 | yes |
| 882 | 9,057 | 9057 | 154 | 154 | 0.14 | 0.1402 | yes |
| 894 | suppressed | 163 | suppressed | 4 | suppressed | 0.5196 | yes |
| 895 | 15,914 | 15914 | 276 | 276 | 0.1 | 0.1008 | yes |
| 897 | 10,176 | 10176 | 168 | 168 | 0.14 | 0.1425 | yes |
| 898 | suppressed | 1865 | suppressed | 29 | suppressed | 0.5266 | yes |
| 899 | 27,932 | 27932 | 445 | 445 | 0.13 | 0.1265 | yes |

**Totals: 0 mismatch(es) across 108 rows.**

## Pooled multi-year

Not run (pass --pooled-qb).
