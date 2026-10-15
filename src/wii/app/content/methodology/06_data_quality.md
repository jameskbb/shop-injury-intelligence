## Data quality checks

Every pipeline run scores itself against a fixed registry of checks and writes the complete result
to a QA report that is committed alongside the data. The panel below reads that committed report at
render time, so it shows the real outcome of the run that produced the numbers on this site,
including any check that did not pass.

Checks come in two kinds, and the difference decides what happens to the data.

**Blocking checks** cover the structural contract the whole analysis rests on: the column layout of
each annual file, case identifiers unique within and across years, every weight positive and
non-null, every treatment date inside its own file's year, every coded value present in its
dictionary, ages inside the documented domain, and every case row lining up with its design,
product, body-part, diagnosis and narrative rows. If a blocking check fails, the run stops. The
freshly built tables stay in a staging area and are never published, the metadata file is not
rewritten, and the app keeps serving the last set of tables that passed every blocking check. A
blocking failure therefore cannot quietly reach this page as data.

**Advisory checks** describe the source material rather than gate it: how many records had to be
rejoined after an embedded line break, which years needed a fallback text encoding, how many
co-product codes fall outside this project's tool taxonomy, how far year-over-year case volume
moves, how completely the optional secondary fields are filled, and how much of the narrative
classification landed. These run to completion and report, but never abort, because a non-zero
count is usually a property of the files CPSC published rather than a defect this pipeline could
correct. An advisory check that does not pass is a disclosure, not silent corruption; it is named
below, with the exact message the check itself recorded, so the reader can judge it.

All counts in the QA report are raw NEISS sample records, not national estimates.
