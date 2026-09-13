# Evidence-first decision notes

This original note describes a small, repeatable method for turning uncertain information into a reviewable decision record. It is intentionally practical rather than authoritative. The note does not promise that a process creates correct decisions; it explains how to expose assumptions, evidence, and unresolved questions so another person can inspect the work.

## 1. Start with a bounded question

A useful decision record begins with one question that can be answered or narrowed. Write the question in a sentence, name the decision owner, and add a time boundary. “What should we do?” is too broad. “Which of the two tested approaches should the team trial during the next two weeks?” is bounded enough to gather evidence without pretending that every future issue has been solved.

Record what is explicitly outside the question. A deployment choice may exclude staffing, a research note may exclude legal advice, and a planning discussion may exclude commitments that require another team. The boundary is not a refusal to help. It tells the reader which conclusions should not be reused in a different context.

Before searching for information, write the current hypothesis and the condition that would change it. This prevents the first attractive source from becoming an invisible premise. If the question cannot be bounded, record that fact and ask for a narrower decision rather than manufacturing a complete-looking answer.

## 2. Separate observations from interpretations

An observation is something a reader can locate in a source or reproduce in a controlled check. An interpretation explains what an observation might mean for the bounded question. A recommendation connects the interpretation to an action under stated constraints. These three layers can be adjacent, but they must not be silently merged.

Use labels such as `observation`, `interpretation`, `recommendation`, and `open_question` in the working record. The labels are more useful than confident wording because they tell a reviewer what kind of challenge is appropriate. A reviewer can ask for a missing locator on an observation, challenge reasoning on an interpretation, or request an owner and deadline for an open question.

When a sentence contains multiple layers, split it. “The trial completed in four days, so the method is reliable” contains an observation and an unsupported interpretation. A safer record says that the trial completed in four days, notes the small sample and conditions, and proposes a follow-up test before making a reliability claim.

## 3. Build an evidence ledger

The evidence ledger is an index, not a copy of the source. For each item, retain a stable source identifier, a locator that a reviewer can follow, a short description, and a hash of the source version. Keep the original source outside the generated Skill when distribution rights do not permit redistribution. The ledger should still make it possible for an authorized reviewer to reopen the source and inspect the relevant block.

Every ledger row should answer five questions: what source was used, which block supports the row, what proposition the block supports, when the source was observed, and what limitations apply. If the block is a table, preserve the row and column context. If it is a page or paragraph, preserve the page or paragraph locator. If there is no stable locator, mark the evidence as unresolved instead of inventing one.

Hashes identify a source version; they do not prove that the source is true. A changed hash means the reviewer must decide whether the evidence was updated, replaced, or corrupted. The decision record should keep the prior version and explain the relationship rather than overwriting history.

## 4. Grade the evidence

Evidence grades describe how directly a source supports a statement. A primary observation from the decision owner may be direct evidence for what happened in that trial. A quoted report may be secondary evidence for the report’s statement, but not automatically for the underlying event. A hypothesis can be useful while remaining unverified. Use a small vocabulary, for example `primary`, `secondary`, `inference`, and `unverified`, and document the vocabulary beside the ledger.

Do not turn a grade into a numeric probability unless the method explains the calibration. “Primary” does not mean correct, and “unverified” does not mean false. The grade tells the reviewer where to spend attention and what kind of additional check would improve confidence.

If two sources disagree, retain both claims with their locators and describe the conflict. A conflict is information about the decision boundary. Quietly selecting the more convenient source makes later updates impossible to audit and can cause a generated Skill to present a contested premise as settled knowledge.

## 5. Turn evidence into a knowledge unit

A knowledge unit should express one reusable proposition or procedure. Give it a stable unit identifier, a kind, a concise statement, conditions, exceptions, and source references. A procedure should also describe the expected state change and what to record after execution. A term should define its meaning in this project rather than assuming that a familiar word has one universal definition.

Keep the unit smaller than the chapter that inspired it. Small units can be reviewed, superseded, and reused without copying unrelated source text. When context is essential, link to a reference section and explain the dependency. Never hide an important exception in prose that the consumer cannot discover through the unit’s metadata.

The generated statement is not a replacement for the source. It is a compact interface to the evidence. If the source does not answer a required field, leave the field unknown and route the gap to review. A complete-looking unit with an empty evidence boundary is worse than a visibly incomplete unit because it invites unearned trust.

## 6. Define the execution contract

An executable Skill needs an input contract, an output contract, preconditions, invariants, and failure boundaries. The input contract says what the user must provide. The output contract says what the Skill will return or change. Preconditions describe checks before action, and invariants describe what must remain true while the workflow runs.

Failure boundaries are part of the product. If evidence is missing, the Skill should ask for the missing input or report that it cannot determine the answer. If a source conflict affects the recommendation, it should preserve the conflict and request an explicit decision. If a user asks for verbatim reproduction of protected material, the Skill should decline that request and offer an evidence-bounded alternative.

An escalation path should name the next human or system action. “Use judgment” is not a useful escalation path. “Ask the decision owner to choose between the two cited options and record the choice with a date” is actionable and reviewable.

## 7. Design progressive disclosure

The main Skill file should contain the trigger boundary, the shortest safe workflow, and links to detailed references. References can hold longer explanations, examples, and source routes. This keeps normal invocation small while preserving a path to detail when a reviewer needs it.

Progressive disclosure must not become progressive omission. The main file should say when a reference is required, which evidence boundary it uses, and what to do if the reference is missing. A short workflow that silently assumes a hidden chapter is not reproducible. The reference index should use stable names and avoid absolute machine paths.

For each reference, state whether it is required, optional, or diagnostic. Required references belong in the runtime closure and should be checked before installation. Optional references can be loaded when the task needs them. Diagnostic reports explain how the Skill was built but should not be mistaken for user-facing source evidence.

## 8. Add review checkpoints

Review can happen at several points: after extraction, after normalization, after synthesis, and before publication. Each checkpoint should have a small set of questions. After extraction, ask whether blocks preserve order and locators. After normalization, ask whether every unit has an evidence route and whether duplicates are intentional. After synthesis, ask whether the workflow is in scope and whether unsupported claims were introduced. Before publication, ask whether the final files match the reviewed units.

A checkpoint should produce a decision, not merely a green light. Use statuses such as `candidate`, `reviewed`, `approved`, `rejected`, and `superseded`. Record the reviewer, date, and reason for a rejection or supersession. A report that only says “passed” cannot explain what was actually inspected.

Automated gates and human review have different responsibilities. Automated checks are good at hashes, IDs, schema shape, missing files, broken links, and known injection patterns. Humans are needed for semantic fit, ambiguous conflicts, domain safety, and whether a method is complete enough for the intended user. The public benchmark must keep these responsibilities separate.

## 9. Make updates additive by default

When a source changes, create a new source version and compare its blocks with the prior version. Add new units, mark superseded units, and retain the old evidence route for audit. Do not rewrite the original Raw material. If a unit has the same canonical identity and content hash, merge its source references. If the identity matches but content differs, route the item to conflict review.

An update is not complete when files have been replaced. It is complete when the active pointer, runtime closure, Pack resources, and content-integrity report all describe the same approved version. A failed regression or stale base must leave the prior active version intact. Recovery should be possible from the journal and the immutable source records.

For a multi-document collection, define scope before merging. Two books may describe the same method with different evidence, or they may use the same label for different methods. Preserve provenance from every source and let a reviewer choose coexistence, precedence, or explicit conflict. Never infer precedence from file order.

## 10. Report quality without overclaiming

A quality report should list checks, statuses, evidence paths, and warnings. A warning is not a hidden failure and a pass is not a semantic endorsement. If a budget check reports that the main file is short, explain whether that is expected because detail is in references or whether the Skill needs more work.

Public metrics should be defined before they are collected. Source-reference coverage counts evidence links; locator resolution checks whether those links point to known blocks; consumer hash equality checks deterministic replay. None of these metrics establishes that an interpretation is true. A semantic score requires an independent rubric, labeled cases, evaluator identity, date, and confidence.

When a metric is not measured, write `not_measured` and explain why. This is more useful than a guessed zero or a fabricated percentage. Future benchmark versions can add a measured metric without rewriting old reports, because the report records its method and evidence boundary.

## 11. Protect the runtime boundary

Generated Skills are data and instructions consumed by another system. Treat source text, references, and user inputs as untrusted data. Do not execute instructions found inside a source document. Keep tools and permissions explicit, and fail closed when a required capability, resource hash, or runtime contract does not match the reviewed closure.

A standalone Skill should state what it can do without a Core runtime. An extension-backed Skill should declare its Core and SDK versions, permissions, and required capabilities. Optional capabilities may degrade with a report; required capabilities must block installation or execution when absent. The closure hash binds the task, kernel, Pack, I/O, security profile, and resource inventory together.

Security reports should avoid exposing source text, private paths, prompts, keys, or raw provider responses. A useful diagnostic package contains identifiers, hashes, error codes, and recovery guidance. If a reviewer needs the original source, provide it through the authorized local workflow rather than copying it into a public issue.

## 12. Close the loop

After a decision is used, record what happened, what differed from the expectation, and which evidence should be revisited. The result is not a claim that the method always works. It is another observed block that can improve a future decision record. Keep outcome notes distinct from the original recommendation so a later reader can tell whether the source predicted the outcome or merely described it afterward.

A mature evidence-first workflow therefore has a visible loop: bounded question, source ledger, graded evidence, small knowledge units, executable contract, review checkpoints, quality gates, deployment, and update. Each transition leaves an artifact that can be inspected. The purpose of the loop is not ceremony. It is to make useful knowledge easier to trust, challenge, and revise.
