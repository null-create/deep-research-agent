# Research Methods

> This file is the agent's living source of truth for how it formulates research plans and approaches.
> It is updated automatically at the end of each self-optimization workflow.
> The Root Orchestrator reads this file before generating every research plan.

---

## Core Research Principles

1. **Decompose before searching.** Break every research question into discrete sub-questions that can be independently verified. Do not attempt to answer compound questions in a single step.

2. **Search breadth before depth.** In the first parallel batch, cast a wide net across multiple angles of the query (academic sources, news, primary documents, practitioner blogs). Narrower/deeper follow-up searches belong in later sequential steps.

3. **Triangulate claims.** Any factual claim worth including in the final report should be supported by at least two independent sources. Flag single-source claims explicitly.

4. **Contradiction-first QA.** The QA agent's primary job is to surface contradictions, not to confirm findings. Contradictions are more valuable than confirmations because they reveal the edges of knowledge.

5. **Parallel groups for independent sub-topics.** When a query has N loosely-coupled sub-topics, assign each its own parallel_group so they execute concurrently. Parallel sub-topic decomposition is not just an efficiency mechanism — it is the primary triangulation mechanism. When multiple independent steps converge on the same metric or claim, that convergence is evidence of correctness.

6. **Sequential steps for synthesis dependencies.** Any step whose prompt requires reading the output of a prior step must have `parallel_group: null`.

7. **Apply the Source Tier System.** Every factual claim should be supported by the highest available source tier (see _Source Tier System_ below). Tier 4-only claims must be flagged explicitly. Never silently treat a blog post as equivalent to a primary document.

8. **Consult the knowledge graph before searching.** Before issuing the first search in any session, query the knowledge graph for entities directly relevant to the research question. See _Knowledge Graph Consultation Rule_ below.

9. **Session date is authoritative.** The agent's training cutoff does not override the session date. See _Agent Operational Rules_ below.

---

## Agent Operational Rules

> These rules govern agent behavior independent of research topic. They must be applied before any research step is executed.

### Agent Temporal Anchoring Rule

The current session date is authoritative. When the session date is in 2025–2026 and research steps reference events from that period, the Search agent must treat those events as potentially real and attempt searches before declaring them hypothetical. A zero-result return is acceptable; a refusal to attempt is not. If the agent's training cutoff precedes the session date, it must escalate using the Zero-Result Escalation Protocol rather than refusing the step.

**Why this rule exists:** In sessions where the session date is 2026 and events are contemporaneous, the Search agent has been observed to classify those events as "fictional future scenarios" due to self-anchoring on its training cutoff. This causes entire research steps to terminate with agent refusals rather than zero-result searches — a qualitatively worse failure mode because it suppresses escalation and produces no actionable signal.

### Plan Preamble Temporal Reinforcement

At the top of any research plan involving 2025–2026 events, insert the following explicit preamble sentence verbatim:

> *"All steps in this plan describe contemporaneous real-world events. If a step returns zero results, apply the Zero-Result Escalation Protocol. Do not classify any step as fictional or outside training data on the basis of training cutoff."*

This reinforcement must appear in the plan itself — not only in the methods document — because the refusal behavior has been observed to occur mid-session after a context shift, at steps that do not follow consecutive failures from the start. The Partial Session Recovery Protocol (which triggers on ≥3 consecutive failures) does not catch mid-session refusals that occur after earlier steps succeeded.

### Event-Anchored Step Description Rule

If a step that previously succeeded in a parallel session returns an agent refusal on a subsequent attempt, inspect whether the step description uses **time-anchored language** (e.g., "since mid-March 2026," "latest developments") rather than **event-anchored language** (e.g., "following Operation X," "after the SEP-1686 vote"). Rephrase to event-anchored framing before re-issuing.

**Why this matters:** Time-anchored descriptions disproportionately trigger training-cutoff self-censorship. Event-anchored descriptions treat the event as a known referent and bypass the refusal heuristic. This is a targeted fix for the failure pattern observed in the Iran conflict session, where steps 8–12 failed after steps 1–7 succeeded.

---

## Source Tier System

Evaluate and label sources according to the following tiers when constructing and reporting research findings:

| Tier | Source Type | Examples |
|------|-------------|---------- |
| **Tier 1** | Primary documents | Official API docs, SEC/regulatory filings, datasets, specifications, source code |
| **Tier 2** | Peer-reviewed or institutional | Academic papers, think-tank reports, government statistics |
| **Tier 3** | Reputable journalism | Major news outlets, established industry publications |
| **Tier 4** | Practitioner commentary | Blogs, forums, social media, personal newsletters |

**Rules:**
- Claims should be supported by the highest tier available.
- When Tier 1–2 sources exist, Tier 4 sources may still be cited for practitioner perspective but must not be the sole support for an empirical claim.
- Any claim supported only by Tier 4 sources must be flagged with the label **[Tier 4 only — verify]** in the final report.
- Outdated sources (typically >2 years old in a fast-moving domain) must be flagged with **[Dated — confirm currency]** regardless of tier.

### Adjusted Tier Table for Contemporaneous Conflict and Breaking News

For sessions researching active conflicts, breaking news events, or rapidly-evolving political situations, apply the following adjusted tiering in place of the standard academic tier defaults:

| Source Type | Adjusted Tier | Note |
|---|---|---|
| UN agency reports (OCHA, UNHCR, IAEA) | **Tier 2** | Treat as institutional |
| Major wire services and established news outlets | **Tier 3** | Standard journalism tier |
| Wikipedia event articles | **Tier 3** | Aggregation source — cross-check underlying citations before citing specific figures |
| Belligerent government or party claims (military spokespersons, health ministries of parties to the conflict) | **Tier 4 [Contested — source is a party to the event]** | Never sole support for quantitative claims |

Label all conflict-sourced claims with this adjusted tier inline. Do not apply the standard academic tier table to military casualty counts, damage assessments, or territorial claims made by parties to the conflict.

### Synthetic Source Flag Before Tier Assignment

When a search result originates from a domain with no established publication history, no named author attribution, or language that is unusually promotional without citation chains, flag it as **[Potentially synthetic — verify domain authority]** before applying any tier label or extracting claims. Do not cite content from such domains even as Tier 4 without a manual check confirming the domain has indexed history, an identifiable editorial standard, or cross-citation from Tier 1–3 sources.

**Why this matters:** The Source Tier System classifies by source *type* but does not distinguish between an established practitioner blog and a potentially synthetic or spam domain. This extension closes that gap. When a domain is flagged (e.g., `quantumcomputer.blog`, `quantumzeitgeist.com`), treat all claims from it as `[Potentially synthetic — verify domain authority]` until verified.

---

## Knowledge Graph Consultation Rule

Before issuing the first search in any session, query the knowledge graph for entities directly relevant to the research question. Apply the following rules:

- Any entity with **2+ mentions** and a confirmed Tier 1–2 source relationship can be treated as a known fact for that session — do not re-search it.
- Use graph relationships to identify which sub-topics are already well-covered (deep nodes with many relationships) versus shallow (isolated nodes with one relationship). Deprioritize well-covered sub-topics in the research plan; prioritize shallow ones.
- Entity mention counts in the graph (e.g., a concept appearing 3× across sessions) are a signal that the topic is recurrent and likely has usable prior findings — retrieve those findings before planning new searches.

### Graph Quality Threshold for Cache-Miss Fallback

When a knowledge graph lookup for a research topic returns only generic concept nodes (e.g., "Research Process," "technology lifecycle," "computing advancement") with no proper nouns, named specifications, or metrics, treat the result as equivalent to a cache miss. Proceed immediately with standard first-batch broad searches without further time spent interpreting the uninformative graph result. Document the fallback inline as **[Graph lookup: uninformative — cache miss fallback applied]**.

**Why this matters:** Abstract graph entities create false impressions of prior coverage and delay session planning without providing actionable signal for research step design.

### Cross-Session Synthesis Rule

When **5 or more research_finding memories** from a single prior session exist on the same topic, issue a **"prior coverage summary" step** as the first step of any new session on that topic, before issuing any new searches. The summary step must:

1. Explicitly list what is already known from prior findings.
2. State the date of the prior session.
3. Identify the boundaries and gaps of prior coverage.

Restrict new searches to gaps, updates since the prior session date, or contradictions flagged in prior memories. Do not re-search topics already documented at importance ≥7 in prior findings. This prevents redundant re-searching of already well-documented territory (e.g., MCP ecosystem metrics that have been established across multiple sessions).

### Graph Entity Creation Requirement

After storing any research_finding memory with **importance ≥7**, create knowledge graph entities for all proper nouns and named metrics in that memory — including protocol names, named specifications, organization names, CVE identifiers, and download figures — with at least one typed relationship to the research topic entity.

**Hard sequencing requirement:** Immediately after the memory storage tool call for any research_finding with importance ≥7, issue knowledge graph entity creation calls for all proper nouns (named frameworks, papers, hardware systems, metrics, organizations) **before executing the next research step**. The next step plan must not be written until entity creation calls have been issued. This is a hard sequencing requirement, not a soft suggestion.

**Why this matters:** Named entities such as Groq LPU, Mamba-2, AlphaQubit, CrewAI, and Microsoft AutoGen v0.4 were never added to the graph despite appearing in high-importance findings, because entity creation was deferred and then skipped. This rule closes that gap.

---

## Step Design Patterns

### Pattern A: Broad → Narrow

- Step 1 (parallel group): Market overview / background
- Step 2 (parallel group): Key players / stakeholders
- Step 3 (parallel group): Recent developments / news
- Step 4 (sequential): Cross-reference contradictions & gaps
- Step 5 (sequential): Deep-dive on unresolved gaps

### Pattern B: Multi-Hypothesis

- For contested topics, assign one step per hypothesis in a parallel group, then a later sequential step to weigh the evidence.

### Pattern C: Chronological

- When temporal evolution matters, assign separate steps to each era/phase, then synthesize with an explicit timeline step.

### Pattern D: Contradiction Resolution

When QA detects a contradiction between Claim A and Claim B, apply this template:

1. **Re-search step (parallel group):**
   - Query 1: `"[Claim A verbatim]"` (short, 3–7 words extracted from the claim)
   - Query 2: `"[Claim B verbatim]"` (same constraint)
   - Goal: find disambiguating sources for each side independently.
2. **Credibility-weighing step (sequential):** Compare source tiers and publication dates for results from both queries. The higher-tier, more-recent source takes precedence unless structural reasons explain the discrepancy.
3. **Flag if unresolved:** If both sides remain supported by equivalent-tier sources after re-search, mark the claim as **[Contested — both positions documented]** in the final report rather than arbitrarily picking one.

> **Why verbatim?** Using the exact conflicting language as the query surfaces sources that have directly addressed that specific claim, rather than sources that discuss the general topic.

### Pattern E: Session Resumption

When resuming a prior interrupted session, do not restart the full research plan. Apply this template:

1. **Prior findings retrieval (sequential):** Retrieve all research_finding memories from the interrupted session.
2. **Prior findings summary step (sequential, `parallel_group: null`):** Synthesize what was established in the prior session, explicitly listing which steps succeeded and which failed, and why each failure occurred (zero results, agent refusal, etc.).
3. **Gap-only parallel groups:** Issue only the failed or missing steps as new parallel groups. Use **event-anchored descriptions** (see Event-Anchored Step Description Rule) rather than time-anchored descriptions to reduce refusal risk.
4. **Proceed to synthesis:** Do not re-execute steps that already produced valid findings.

> **Why a dedicated summary step?** In complex sessions, the prior findings may span 7–12 memories. Issuing a summary step forces explicit gap identification before new searches begin, preventing both redundant re-searching and inadvertent omission of valid prior findings from the final synthesis.

### Contested Quantitative Claims Protocol

When sources representing different parties to a conflict or competition make **incompatible quantitative claims** — casualties, adoption figures, damage assessments, market share — apply this protocol:

1. List all figures with their sources and tier labels in a dedicated subsection.
2. Do **not** synthesize to a single number.
3. Label the section **[Contested — multiple party claims documented]**.
4. Apply this by default whenever the measuring party has a material stake in the reported number, including: military casualty counts, market share claims, regulatory compliance statistics, and organization-level adoption figures.

> **Rationale:** The Iran conflict session demonstrates this protocol working correctly — the synthesis step listed IDF, Iranian Health Ministry, and HRANA figures separately rather than averaging or selecting one. The protocol is now mandatory, not optional, for contested quantitative claims.

### Gap-Triggered Re-search Obligation

When a step's coverage notes document a sub-topic as "no source found" or "no coverage," and that sub-topic was part of the original research plan, a follow-up step targeting that specific gap is **mandatory — not optional**. The synthesis step must not proceed until either:
- (a) the gap is filled, or
- (b) the Zero-Result Escalation Protocol has been exhausted and the gap is formally declared as `[Insufficient public data]`.

Moving directly from a documented gap to synthesis without a re-search attempt is a plan failure, not a plan completion.

### Coverage Notes Convention

Every search step's output should conclude with a structured coverage note documenting:
1. Sub-topics for which no sources were found.
2. Recommended follow-up queries for those gaps.
3. Whether those gaps are blockers for synthesis.

Coverage notes feed directly into the next step's planning and are the primary mechanism for triggering the Gap-Triggered Re-search Obligation above.

### Partial Session Recovery Protocol

If ≥3 consecutive steps in a research plan return null results or agent refusals, halt the sequential plan and switch to a synthesis step using only the findings gathered so far. Explicitly label the synthesis as `[Partial — N of M steps completed]` and list the uncompleted steps as `[Insufficient public data — session interrupted]`. Do not attempt to synthesize from zero findings.

### Universal Claim Qualification Step

At the end of **every technology research session** — not only protocol or governance topics — add a dedicated closing step that explicitly asks: *"What evidence qualifies, contradicts, or limits the dominant claims established in this session?"* Issue at least two search queries targeting known failure modes, dissenting findings, or boundary conditions for the session's headline conclusions. Store the results as a research_finding memory tagged **[Qualification step]**.

**Why this matters:** This generalizes the documented Contradiction-First pattern that reliably surfaced MoE routing instability, barren plateau severity, and MCP governance immaturity. The Governance Maturity Check is a specific application of this pattern; the Universal Claim Qualification Step is the general rule.

> **Relationship to Governance Maturity Check:** The Governance Maturity Check (for protocol/standard topics) remains in force as a domain-specific sub-rule. The Universal Claim Qualification Step applies to all technology sessions, with the Governance Maturity Check providing additional mandatory questions when the session involves a protocol or governance body.

---

## Complexity → Step Count Heuristic

Use query complexity to calibrate the number of research plan steps. Do not over-engineer simple queries or under-engineer complex ones.

| Query Type | Recommended Steps | Example |
|---|---|---|
| Simple factual lookup | 2–3 steps | "What year was X founded?" |
| Single-domain analytical | 4–6 steps | "How does X technology work and who are the key vendors?" |
| Multi-domain or contested | 7–10 steps | "What are the policy, technical, and economic dimensions of X?" |
| Cross-domain systemic | 10+ steps | Requires **explicit written justification** in the plan preamble |

> **Minimum step counts for named patterns:** Pattern A should never collapse below 4 steps. Pattern B should never collapse below 3 steps (one per hypothesis plus one synthesis). Collapsing patterns under time pressure degrades output quality and is never worth the trade-off.

---

## Known Effective Strategies

- **Targeted re-search for QA failures**: when QA detects a contradiction, the re-search query should include the specific conflicting claim verbatim to find disambiguating sources. See _Pattern D_ above for the full template.
- **Concise step descriptions**: step descriptions should be specific enough that the Search agent can formulate 2–3 distinct search queries from them without ambiguity.
- **Avoid umbrella steps**: a step named "Research everything about X" produces low-quality results. Prefer "Find Q3 2024 earning reports for the top 5 solar panel manufacturers."
- **Step descriptions are intent, not queries**: the Orchestrator's step descriptions must describe *what to find*, not *how to phrase the search*. A step description that reads like a search query causes the Search agent to encode the entire description into one long query, which breaks Query Formulation Rules. Bad: *"Search for MCP Model Context Protocol agentic tool-calling adoption 2026 patterns."* Good: *"Find adoption statistics and practitioner perspectives on MCP in agentic workflows, published in 2025–2026."*

### Ensemble Query Strategy

Issuing 2–3 short, focused queries per step is not merely a workaround for query length limits — it is a deliberate strategy for **reducing single-query semantic bias**. A single well-formed query anchors results to one semantic neighborhood; an ensemble of short queries surfaces distinct result clusters that a single query would miss entirely.

**Empirical confirmation:** Across multiple sessions, separate short queries (e.g., "MCP SDK Go Rust Java," "Agent Communication Protocol ACP 2026," "MCP SEP Specification Enhancement Proposal") each returned distinct non-overlapping source clusters. This is not theoretical — the strategy has been verified to produce meaningfully different results per query across at least three independent sessions.

**Apply this strategy by default:** for every research step, formulate at least 2 independent short queries from different angles (e.g., one using the formal term, one using common usage, one targeting a specific source type). Treat single-query steps as a red flag unless the sub-topic is extremely narrow.

### Terminology Calibration Pass

For any technical, niche, or acronym-heavy topic, the **first query must use the full expanded term**, not the abbreviation. Switch to the acronym or shorthand only after first-batch results confirm it is the dominant form used in the indexed sources.

**Template:**
1. First query: `"Model Context Protocol agentic workflows"` → scan results to confirm whether "MCP" is used universally.
2. Subsequent queries (if confirmed): `"MCP tool-calling 2026"`, `"MCP server registry"`.
3. If results use multiple competing terms, maintain the expanded form throughout to avoid missing sources that use alternate terminology.

### Concentration Check for Aggregate Metrics

When a research step returns large aggregate numbers (download counts, server counts, star counts, user counts), always issue a follow-up query to check whether usage is concentrated among a small number of deployments or actors.

**Extended application:** Apply this check not only to technology metrics but also to any **aggregate actor-count claim** — e.g., "adopted by N organizations," "N countries involved," "N coalitions participating." For each such claim, issue a follow-up query asking which single actor, country, or organization accounts for the largest share of the aggregate. Concentrated aggregates where one actor dominates are qualitatively different from distributed ones, and this distinction is systematically invisible in summary statistics.

**Why this matters:** A metric of "10,000 servers" with "highly concentrated usage" has a fundamentally different meaning than evenly distributed usage. In the MCP ecosystem research, explosive server growth coexisted with usage that remained "highly concentrated" — a meaningful counter-signal against the dominant growth narrative that would have been missed without an explicit concentration check.

### Governance Maturity Check for Protocol/Standard Topics

When researching any protocol, standard, or specification, include a dedicated step assessing governance maturity:
- Is there a formal change proposal process?
- Is it described as mature or nascent by its own maintainers?
- Are there formal working groups with delegated authority?

**Mandatory post-synthesis contradiction step:** For any session researching a protocol, standard, or governance body, add a mandatory contradiction-resolution step **after** the main synthesis, regardless of whether QA has surfaced contradictions. Frame it as: *"What evidence qualifies, contradicts, or limits the enterprise-readiness or maturity claims established in prior steps?"* This step reliably surfaces nuance findings (e.g., governance "still finding its footing") that are invisible in affirmative synthesis.

**Why this matters:** Governance immaturity is a Tier-1 signal that "de facto standard" claims should be qualified. In MCP research, the SEP (Specification Enhancement Proposal) governance was described as "still finding its footing" as of late 2025 — a direct qualifier on enterprise-readiness claims that would not have surfaced without an explicit governance step.

> **Note:** The Governance Maturity Check is a domain-specific application of the _Universal Claim Qualification Step_ (see Step Design Patterns). Both must be applied for protocol/standard sessions.

### Wikipedia Structural Anchor Strategy

For breaking or recent events, include the relevant Wikipedia article as an explicit **first-batch search target** and treat it as a structural anchor:

1. Scrape the Wikipedia article to confirm dates, actors, and rough figures.
2. Extract its coverage gaps as an explicit list.
3. Drive follow-up searches specifically toward the analytical content absent from Wikipedia (scenario analysis, diplomatic nuance, economic impact).

**Always pair a Wikipedia scrape with a documented gap list.** Do not treat the Wikipedia article as a standalone source — treat it as a map of what is known versus what requires deeper investigation. Apply the adjusted conflict tier (Tier 3, aggregation source) when citing Wikipedia figures.

### Named Operation/Event Anchor Queries

For any research topic involving military operations, regulatory actions, legal proceedings, or named standards proposals, always include the **official proper name** as one of the ensemble query variants (e.g., "Operation Rising Lion," "SEP-1686," "TACT Trial").

**Why this matters:** Named anchors function as semantic discriminators that surface primary and secondary sources discussing the specific event rather than the general topic area. In the Iran conflict sessions, queries using specific operation names ("Operation Epic Fury," "Operation Roaring Lion") consistently returned more specific and non-overlapping results than generic queries like "Iran war 2026." The ACP finding (IBM/Cisco's competing protocol, identified via "Agent Communication Protocol ACP 2026") is a parallel example from the MCP sessions.

### Actor Objective Divergence Check

In any multi-actor research topic where **3 or more actors** are identified in the initial decomposition, include a dedicated step asking explicitly whether the key actors' objectives are **converging or diverging** — not just what each actor wants independently.

**Query framing:** `"[Actor A] vs [Actor B] strategic divergence [topic]"`

**Why this matters:** Divergence findings are systematically underreported relative to alliance framing and represent disproportionately high-value signals for forecasting, risk assessment, and policy analysis. In the Iran conflict session, a dedicated US-Israel strategic divergence step surfaced the finding that Trump retreated from regime-change language and that US/Israel objectives diverged after 3 weeks — findings that would not have appeared in a step-per-actor structure that only asks what each actor wants.

### Milestone vs. Projection Distinction for Hardware and Roadmap Topics

For any research topic involving hardware milestones, qubit counts, benchmark scores, or vendor capability claims, explicitly categorize each finding at storage time as one of:

- **[Demonstrated]** — empirically published in a peer-reviewed venue or independently replicated.
- **[Announced]** — stated in a press release or institutional blog without independent replication.
- **[Projected]** — a roadmap target without confirmed achievement.

Do not combine these categories in synthesis statements. A projected qubit count milestone is not equivalent to a demonstrated one and must not be presented as such.

**Why this matters:** Multiple quantum computing session findings conflated vendor roadmap claims with demonstrated achievements. Memory `0eb4be43` explicitly documented: "Most sources are third-party commentary or roadmap aspirations rather than announced milestones." Without explicit categorization at storage time, synthesis steps propagate projections as facts.

---

## Query Formulation Rules (for the Search agent)

Search queries that are too long or too specific consistently return ≤1 result from DuckDuckGo, wasting a tool call. Follow these rules for every query:

1. **Keep queries short: 3–7 words is the sweet spot.** A query like `"MCP Model Context Protocol agentic workflows tool-calling pipelines 2026 adoption patterns"` returns 1 result. Break it into two shorter queries instead: `"MCP agentic workflows 2026"` and `"MCP tool-calling adoption patterns"`.

2. **Do not stack qualifiers.** Pick the single most discriminating qualifier, not all of them. Bad: `"official MCP server registry 2026 server count API documentation"`. Good: `"MCP server registry 2026"`.

3. **Use `site:` only for well-indexed domains.** The `site:` operator is powerful for `github.com`, `docs.anthropic.com`, `modelcontextprotocol.io`, and major news outlets. Avoid it for niche blogs, community sites, or any domain that may not be fully indexed — it will cap results at 1–2.

4. **Generate breadth through multiple short queries, not one long query.** If a step needs to cover several angles, issue 2–3 short focused queries rather than 1 mega-query that tries to capture everything at once. See _Ensemble Query Strategy_ above.

5. **Prefer common terminology over precise jargon for first searches.** Use terms that would appear in headlines and blog titles. If the acronym is unfamiliar, expand it (e.g. `"Model Context Protocol"` rather than just `"MCP"`) until you have confirmed it is widely used in search results. See _Terminology Calibration Pass_ above.

6. **Pre-flight word count check.** Before issuing any query, count its words. If the count exceeds 7, the query must be split — no exceptions. This is a mechanical gate, not a suggestion.

7. **For abstract economic, historical, or theoretical concepts, always append a scope qualifier.** Never issue a query consisting solely of an abstract concept phrase (e.g., "technological unemployment," "Fourth Industrial Revolution," "scaling laws") — it will match Wikipedia disambiguation or overview articles. Always append a qualifier biasing toward empirical or practitioner content: e.g., `"technological unemployment empirical evidence 2024"` or `"Fourth Industrial Revolution firm-level adoption"`. This avoids the documented failure pattern of the search agent returning unrelated Wikipedia articles for abstract concept queries.

---

## Zero-Result Escalation Protocol

When a search returns zero results, do not silently move on or attempt a single re-query and give up. Follow this three-step escalation:

1. **Broaden:** Remove the most specific term from the query and re-issue (e.g., drop a year, a qualifier, or a proper noun).
2. **Synonymize:** Replace the key term with an alternative (synonym, expanded acronym, common alias, or adjacent concept).
3. **Declare and document:** If zero results persist after two escalation attempts, mark the sub-topic as **[Insufficient public data — zero results after escalation]** in the final report. Do not silently omit it. Document the three queries attempted so future sessions know this avenue was exhausted.

> This protocol also applies when the Agent Temporal Anchoring Rule is triggered: an agent refusal to attempt a search on the grounds that events are "future" or "hypothetical" must be treated as equivalent to a zero-result return and escalated accordingly.

---

## Visited URLs Scratchpad

At the start of every research plan, the Search agent must maintain an explicit **Visited URLs scratchpad** — a running list of every URL on which `scrape_url` has been called in the current session.

**Rules:**
- Before every `scrape_url` call, check the scratchpad. If the URL is already listed, **skip it** — the content is already in context.
- Add the URL to the scratchpad immediately after each successful scrape.
- This is a hard rule, not a soft suggestion. Re-scraping a URL produces zero new information and wastes a tool call.

> **Why a scratchpad rather than relying on memory?** In long sessions with large context, mental tracking of visited URLs degrades. An explicit list is the only reliable safeguard.

---

## Report Structure Template

Every final research report must include the following sections, in order. Sections may be retitled for clarity but must not be omitted.

| Section | Purpose |
|---|---|
| **Executive Summary** | 3–5 sentence plain-language summary of the key findings. Written last, placed first. |
| **Findings by Sub-topic** | One subsection per decomposed sub-question from the research plan. Each subsection cites sources with tier labels. |
| **Contradictions & Unresolved Questions** | Explicit list of any claims flagged as [Contested], [Tier 4 only], [Dated], or [Insufficient public data]. Do not bury these inside Findings. |
| **Source List** | Full list of all sources consulted, grouped by tier. Include URL, access date, and tier label. |

**Uncertainty language conventions:**
- **"Evidence confirms"** — supported by Tier 1 or Tier 2 sources with no contradicting evidence found.
- **"Evidence suggests"** — supported by Tier 2–3 sources or with minor contradictions.
- **"Reported by some sources"** — Tier 3–4 sources only, or significant contradictions exist.
- **"Unconfirmed"** — single source, Tier 4 only, or failed escalation protocol.

Never use unqualified definitive language ("X is the case") for claims that do not meet the "Evidence confirms" threshold.

---

## Memory Storage Criteria

The memory infrastructure should be used purposefully. Apply the following rules to decide what to store as a memory vs. what belongs only in `Accumulated Insights`:

**Store as a memory:**
- Session-specific discoveries that are unlikely to be re-derivable: e.g., a specific URL confirmed as the authoritative source for a niche topic.
- Domain-specific terminology confirmed to differ from common usage (e.g., a term that means something different in context X than in context Y).
- Confirmed dead ends: search strategies that returned zero results for a specific domain, so future sessions do not repeat them.

**Do NOT store as a memory:**
- Anything already captured in `Accumulated Insights` — do not duplicate.
- General best practices that belong in the methods document itself.
- Findings from a specific research session that have no generalization value.
- Raw evidence fragments that do not meet the minimum completeness threshold (see below).

### Pre-Storage Inline Content Assertion

**Before calling the memory storage tool for any raw_evidence entry**, paste the full text of the entry inline in the current plan step and confirm it contains all three of the following:

1. A valid URL.
2. A publication title.
3. At least one complete sentence ending with a period.

If the content is a JSON fragment, closes with a bracket, or ends mid-phrase, **discard it entirely — do not store it.** This is a tool-call-level gate, not a policy statement. The check must be performed at the moment of the storage call, not retroactively.

**Why this gate exists:** The three-point completeness check was previously documented as a policy but enforced only by agent self-compliance. Analysis of stored memories found at least 14 raw_evidence entries that failed all three gates — including entries consisting of only a closing JSON bracket, a fragment sentence ending mid-phrase, or a search query string with no factual content. The inline assertion requirement makes the gate explicit at tool-call time.

### Minimum Completeness Threshold

Do not store raw evidence fragments. Before storing any raw_evidence memory, apply the **Pre-Storage Inline Content Assertion** above. If any check fails, **discard the entry rather than storing it**. A raw evidence memory that consists only of truncated JSON fragments or coverage-note tails must never be stored. If a scrape returns only a fragment, apply the Zero-Result Escalation Protocol to obtain a complete source before storing.

### Malformed Finding Rejection Gate

Before storing any **research_finding memory**, verify that every primary claim in the findings list contains an inline source tier label formatted as `[Tier N]` or `[Tier — unverified]`. If any claim lacks a tier label, the memory is considered **malformed** and must not be stored until labels are added.

**Correctly formatted example:**
> *"MoE routing instability increases in multimodal settings [Tier 2 — peer-reviewed preprint, arXiv 2024]. Expert collapse documented in 