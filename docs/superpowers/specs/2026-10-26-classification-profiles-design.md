# Classification Profiles

**Status:** proposed · **Date:** 2026-10-26

## Context

The tool is built around a reusable idea — *given a bug report, what kind of bug is
it, and what was its dependency context when it was filed* — but only one taxonomy
can ever be expressed. The four InEx labels are welded into six places in
`tools/helpers/classify.py`, and the seven reasoning steps are welded into the
response schema.

Three of those six were added in October 2026 when the classifier moved to Gemini
structured output. That change removed a real defect — 1,063 of 71,640 rows had been
silently stored as the label `Unknown` when no label was ever produced — but paid for
it with flexibility: the taxonomy moved from a mutable list and a regex into a
module-level Pydantic model.

Today, pointing `classifyPrompt` at a different guide does not work. The schema's
`final_answer` enum rejects the new labels and `validateResult` marks every response
`invalid_result`. It fails loudly rather than silently, which is correct, but it
fails completely.

This matters because the audience is developers *and* researchers, and most
repository-mining work defines its own taxonomy. A tool that only emits
Intrinsic/Extrinsic/Not-a-Bug/Unknown is one study's code. The same tool with a
user-supplied taxonomy is infrastructure.

**Intended outcome:** the taxonomy and reasoning framework become a user-supplied
profile. InEx ships as the default. Everything else — collection, fix attribution,
dependency linkage, retry, validation, concurrency — is shared and unchanged.

## Goals

- A profile defines the label set, the reasoning steps, and the guide text.
- The response schema, probability fields, fallback regex and the orchestrator's
  taxonomy description are all *derived* from the profile.
- The InEx profile reproduces today's behaviour exactly, proven by a golden test.
- A profile load failure is detected at startup, not per issue.
- Multiple profiles coexist in one database and can be queried side by side.

## Non-goals

Deliberately fixed, not profile-controlled:

- **The issue-formatting template** (`formatIssueData`). It holds the truncation
  budgets, maintainer-comment prioritisation, role detection, and the explicit
  "None found" for absent PRs. Exposing it needs a template language, creates an
  injection surface, and commits us to keeping every template variable stable.
- **Extraction regex as a user-authored field.** It is generated from the labels;
  there is no reason to hand-write it.
- Collection, fix attribution, dependency linkage, the database schema.
- Generalising beyond npm. The ecosystem stays npm by decision.

## The profile format

YAML, because PyYAML 6.0.3 is already present. One file, plus a guide file it
points at.

```yaml
name: inex
version: 1
description: Intrinsic / Extrinsic / Not-a-Bug root-cause attribution

guide: classification_prompt.md      # resolved relative to this file

labels:
  - name: Intrinsic
    description: Root cause is inside this repository.
  - name: Extrinsic
    description: Root cause is outside this repository.
  - name: Not a Bug
    description: Not a defect in production code.
  - name: Unknown
    description: Insufficient information to classify.

reasoning:
  - field: definition_check
    description: Identify which category definitions match the signals
  - field: maintainer_signal
    description: Note any authoritative maintainer comments about root cause
  - field: pr_commit_evidence
    description: If a closing PR exists, describe what it changed and why
  - field: temporal_signals
    description: Check for version changes, upgrades, works-on-X-fails-on-Y patterns
  - field: contract_rule
    description: If API usage is involved, determine who violated the contract
  - field: information_completeness
    description: Assess whether there is enough detail to classify confidently
  - field: decision
    description: State the classification and the primary supporting evidence

confidence: [High, Medium, Low]      # optional; omit to drop the field
```

A second profile ships as `profiles/example-severity.yaml` to prove the mechanism
generalises and to give users something to copy. Its reasoning steps are visibly
different from InEx's — that is the point.

### Label slugs

Probability field names derive from label names: lowercase, every run of
non-alphanumeric characters becomes `_`, leading and trailing `_` stripped. `Not a
Bug` becomes `not_a_bug`, matching today's hand-written field exactly.

Collisions are a load-time error. `Not a Bug` and `not-a-bug` both slug to
`not_a_bug` and must be rejected with both offending labels named.

## Deriving the schema

New module `tools/core/profile.py`:

```python
def buildSchema(profile):
    prob_fields = {
        label.slug: (float, Field(ge=0.0, le=1.0, description=f"P({label.name})"))
        for label in profile.labels
    }
    Probabilities = create_model(f"{profile.name}_Probabilities", **prob_fields)

    fields = {step.field: (str, Field(description=step.description))
              for step in profile.reasoning}
    if profile.confidence:
        fields["confidence"] = (Literal[tuple(profile.confidence)], ...)
    fields["probabilities"] = (Probabilities, ...)
    fields["final_answer"] = (Literal[tuple(l.name for l in profile.labels)], ...)
    return create_model(f"{profile.name}_Result", **fields)
```

**Field order is load-bearing and must be asserted in tests.** google-genai preserves
property ordering and Gemini emits in schema order, so the reasoning fields must
precede `probabilities` and `final_answer` or the model commits to an answer before
reasoning. `create_model` preserves kwargs insertion order; the test pins it.

`Classify` then takes a profile instead of a prompt path. `self.Labels`, `LABEL_MAP`
and `PROB_FIELDS` derive from it, and the module-level `ClassificationResult` and
`_Probabilities` classes are deleted.

`validateResult` is unchanged in logic — all labels present, each in [0,1], sum
within 0.02 of 1.0, argmax recorded but never overriding the stated label. It simply
reads the label set from the profile.

## The non-structured path

`extractLabel`'s regex is generated from the label names rather than hard-coded.
Each name is regex-escaped, internal whitespace relaxed so `Not a Bug`, `Not-a-Bug`
and `not a bug` all match, and alternatives are sorted longest-first so no label is
shadowed by a prefix of another.

## Identity and the database

`prompt_version` becomes:

```
<profile name>@<sha256(profile bytes + guide bytes)[:12]>+t<TEMPLATE_VERSION>
```

Hashing both files matters — the guide changes outputs as much as the label list does.

No schema migration is required. `classifications` is already keyed
`UNIQUE(issue_id, model, prompt_version, temperature)`, and the resume filter is
already scoped to that key, so two profiles over the same issues produce two
independent row sets that coexist and query cleanly. This falls out of work already
done.

**Consequence to accept:** existing rows carry
`prompts/classification_prompt.md@<hash>+t2` and will not match the new identity, so
they read as unclassified under the new scheme. Given the 71,640-row qwen3 corpus is
already frozen and fix-forward, this is acceptable. It must be stated in the README
rather than discovered.

## The orchestrator prompt

`prompts/system_prompt.md` names the four labels in six places and its analysis
patterns assume Intrinsic/Extrinsic. It gains a `{{TAXONOMY}}` placeholder, filled at
load from the profile's labels and descriptions, and its analysis patterns are
rewritten to be label-agnostic — "the classification breakdown" rather than "the
Intrinsic/Extrinsic breakdown".

`.github/workflows/issue-classify.yml` carries its own divergent three-class prompt
and is not profile-driven. Out of scope here; flag it separately for removal or
alignment so the repo stops shipping two disagreeing taxonomies.

## Migration

`profiles/inex.yaml` reproduces the current taxonomy and the seven reasoning steps
verbatim. The acceptance criterion is mechanical:

> The JSON schema generated from `profiles/inex.yaml` is byte-identical to the schema
> produced by today's hard-coded `ClassificationResult` — same field names, same
> order, same enums, same required list.

That is a golden test, not a judgement call. If it passes, no re-validation of the
classifier is needed.

`config.classifyPrompt` is replaced by `config.classifyProfile`, defaulting to
`profiles/inex.yaml`. The guide file does not move.

## Failure handling

Profiles are validated once, at construction, and failures raise immediately:

- fewer than two labels; duplicate label names; colliding slugs
- no reasoning steps; duplicate or non-identifier field names
- a reasoning field named `probabilities`, `final_answer` or `confidence`
- missing or unreadable guide file
- a `confidence` key present but empty

A profile error must never surface as a per-issue classification failure.

## Testing

The repository has no tests. This change introduces `tests/`, using the already
pinned pytest:

- slug derivation, including collision rejection
- schema generation: field order, enum contents, all-required, nested probabilities
- the golden InEx equivalence test described above
- generated regex: matches all label spellings, longest-first, no shadowing
- `validateResult` driven by a non-InEx profile with three labels
- every profile load failure above raises at construction
- Gemini conversion: the *generated* schema survives `_dict_to_gapic_schema` with
  nested object and enums intact — verified for the static schema, must be
  re-verified for dynamic ones

## Risks

- **Dynamic models and the Gemini converter.** `$defs`/`$ref` inlining was verified
  for the hand-written model. Generated models produce different `$defs` names and
  must be re-verified, not assumed.
- **Many-label profiles.** Ten labels make "probabilities sum to 1.0" harder for the
  model and may raise the `prob_sum_off` flag rate. Document that validation flags it
  rather than failing, and suggest keeping label sets small.
- **PyYAML is pinned but only as a transitive dependency** (`PyYAML==6.0.3` is in
  `requirements.txt` because something else pulled it in). Making it a direct
  dependency of ours means it must survive any future dependency pruning.
- **Claim scope narrows, honestly.** "Validated at X%" applies to the InEx profile,
  not to the tool. Other profiles are user-validated. State that plainly in the
  README rather than implying it away.
