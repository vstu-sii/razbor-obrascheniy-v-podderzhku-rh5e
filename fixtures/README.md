# Synthetic experiment fixtures

40 request cases: 10 per UC-01–04; six knowledge articles. Byte-identical snapshots of the existing draft_synthetic dataset. Labels are author proposals, not independently approved gold. Article approved status is part of the synthetic fixture protocol, not an assertion about production content.

Normal LLM input uses ticket/history/version and retrieval from the whole allowed corpus; expected, candidate_proposal and kb_candidates are excluded. UC-04 requires human review rather than a new model call. Case order is fixed before measuring this run.