## Limitations

The most important limitation, restated from the top of this page: NEISS has no denominator for
tool ownership or hours of use, so nothing here measures per-use risk. Beyond that, NEISS
narratives are short, written quickly by ER staff for clinical purposes, and are not written to
answer analytical questions -- some details a researcher might want are simply never recorded. The
rule-based narrative reader below cannot recover information a narrative never states, and coding
practice for body part, diagnosis, and product fields has evolved over the two decades of data
loaded here. Every number in this app describes what is associated with a tool among ER cases,
never a cause, and never a rate.

## Narrative-classification methodology

The narrative reader is entirely rule-based and deterministic: the same narrative always produces
the same structured fields, and no large language model is required to run this app. It applies a
layered set of regular-expression and keyword rules -- tuned against a held-out development
sample and measured against a separate holdout sample, described in Validation below -- to derive
fields such as the specific tool subtype, the primary mechanism of injury, and whether protective
equipment, a guard, or a maintenance/setup context was mentioned. Every derived field carries a
confidence signal, and some narrative-derived fields exist alongside the officially coded product
fields rather than replacing them; the officially coded fields are never overridden by the
narrative reader.
